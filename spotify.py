import os
import re
import time
from typing import Tuple, Dict

import requests
import spotipy
from ovos_backend_client.api import OAuthApi
from ovos_backend_client.database import OAuthTokenDatabase, OAuthApplicationDatabase
from ovos_utils.log import LOG
from ovos_utils.parse import match_one, fuzzy_match, MatchStrategy
from ovos_utils.xdg_utils import xdg_config_home
from ovos_utils.ocp import PlaybackType, MediaType
from requests.exceptions import HTTPError
from spotipy import SpotifyOAuth
from spotipy.oauth2 import SpotifyAuthBase


class SpotifyPlaybackError(Exception):
    pass


class NoSpotifyDevicesError(Exception):
    pass


class PlaylistNotFoundError(Exception):
    pass


class SpotifyNotAuthorizedError(Exception):
    pass


OAUTH_TOKEN_ID = "ocp_spotify"


class OVOSSpotifyCredentials(SpotifyAuthBase):
    """ Oauth through ovos-backend-client"""

    def __init__(self):
        super().__init__(requests.Session())

    @staticmethod
    def is_token_expired(token_info: dict):
        return time.time() >= token_info["expires_at"]

    @staticmethod
    def get_access_token():
        t = OAuthApi().get_oauth_token(OAUTH_TOKEN_ID,
                                       auto_refresh=True)
        # TODO auto_refresh flag not working
        if OVOSSpotifyCredentials.is_token_expired(t):
            LOG.warning("SPOTIFY TOKEN EXPIRED")
            t = OVOSSpotifyCredentials.refresh_oauth()
        return t["access_token"]

    @staticmethod
    def refresh_oauth():
        AUTH_DIR = os.environ.get('SPOTIFY_SKILL_CREDS_DIR', f"{xdg_config_home()}/spotipy")
        SCOPE = 'user-library-read streaming playlist-read-private user-top-read user-read-playback-state'
        TOKEN_ID = "ocp_spotify"

        with OAuthApplicationDatabase() as db:
            app = db.get_application(TOKEN_ID)

        am = SpotifyOAuth(scope=SCOPE,
                          client_id=app["client_id"],
                          client_secret=app["client_secret"],
                          redirect_uri='https://localhost:8888',
                          cache_path=f"{AUTH_DIR}/token",
                          open_browser=False)

        with OAuthTokenDatabase() as db:
            token_info = db.get_token(TOKEN_ID)
            token_info = am.refresh_access_token(token_info["refresh_token"])
            db.add_token(TOKEN_ID, token_info)
            LOG.info(f"{TOKEN_ID} oauth token  refreshed")
        return token_info


class SpotifyClient:
    # Return value definition indication nothing was found
    NOTHING_FOUND = (0.0, None)
    # Confidence levels for generic play handling
    DIRECT_RESPONSE_CONFIDENCE = 0.8

    MATCH_CONFIDENCE = 0.5

    def __init__(self):
        self._spotify = None
        self.__playlists_fetched = 0
        self._playlists = None
        self.__device_list = None
        self.__devices_fetched = 0

    @property
    def spotify(self):
        if self._spotify is None:
            self.load_credentials()
        return self._spotify

    @staticmethod
    def best_result(results):
        """Return best result from a list of result tuples.
        Arguments:
            results (list): list of spotify result tuples
        Returns:
            Best match in list
        """
        if len(results) == 0:
            return SpotifyClient.NOTHING_FOUND
        else:
            results.reverse()
            return sorted(results, key=lambda x: x[0])[-1]

    @staticmethod
    def best_confidence(title, query) -> int:
        """Find best match for a title against a query.
        Some titles include ( Remastered 2016 ) and similar info. This method
        will test the raw title and a version that has been parsed to remove
        such information.
        Arguments:
            title: title name from spotify search
            query: query from user
        Returns:
            (int) best confidence (0-100)
        """
        best = title.lower()
        best_stripped = re.sub(r'(\(.+\)|-.+)$', '', best).strip()
        return int(max(fuzzy_match(best, query, strategy=MatchStrategy.DAMERAU_LEVENSHTEIN_SIMILARITY),
                   fuzzy_match(best_stripped, query, strategy=MatchStrategy.DAMERAU_LEVENSHTEIN_SIMILARITY)) * 100)

    def load_credentials(self):
        """ Retrieve credentials from the backend and connect to Spotify """
        try:
            creds = OVOSSpotifyCredentials()
            self._spotify = spotipy.Spotify(auth_manager=creds)
        except(HTTPError, SpotifyNotAuthorizedError):
            LOG.error('Couldn\'t fetch spotify credentials')

    @property
    def devices(self):
        """ Devices, cached for 60 seconds """
        if not self.spotify:
            return []  # No connection, no devices
        now = time.time()
        if not self.__device_list or (now - self.__devices_fetched > 60):
            self.__device_list = self.spotify.devices().get('devices', [])
            self.__devices_fetched = now
        return self.__device_list

    @property
    def playlists(self):
        """ Playlists, cached for 5 minutes """
        if not self.spotify:
            return []  # No connection, no playlists
        now = time.time()
        if not self._playlists or (now - self.__playlists_fetched > 5 * 60):
            self._playlists = {}
            playlists = self.spotify.current_user_playlists().get('items', [])
            for p in playlists:
                self._playlists[p['name'].lower()] = p
            self.__playlists_fetched = now
        return self._playlists

    def query_artist(self, artist) -> Tuple[int, Dict]:
        """Try to find an artist.
            Arguments:
                artist (str): Artist to search for
            Returns: Tuple with confidence (0-100) and data or NOTHING_FOUND
        """
        data = self.spotify.search(artist, type='artist')
        if data and data['artists']['items']:
            best = data['artists']['items'][0]['name']
            confidence = fuzzy_match(best, artist.lower(),
                                     strategy=MatchStrategy.DAMERAU_LEVENSHTEIN_SIMILARITY) * 100
            confidence = min(confidence, 100)
            return (confidence,
                    {
                        'data': data,
                        'name': None,
                        'type': 'artist'
                    })
        else:
            return SpotifyClient.NOTHING_FOUND

    def query_album(self, album) -> Tuple[int, Dict]:
        """ Try to find an album.
            Searches Spotify by album and artist if available.
            Arguments:
                album (str): Album to search for
            Returns: Tuple with confidence (0-100) and data or NOTHING_FOUND
        """
        # TODO localize
        by_word = ' by '
        bonus = 0
        if len(album.split(by_word)) > 1:
            album, artist = album.split(by_word)
            album_search = '*{}* artist:{}'.format(album, artist)
            bonus = 10
        else:
            album_search = album
        data = self.spotify.search(album_search, type='album')
        if data and data['albums']['items']:
            best = data['albums']['items'][0]['name'].lower()
            confidence = self.best_confidence(best, album)
            # Also check with parentheses removed for example
            # "'Hello Nasty ( Deluxe Version/Remastered 2009" as "Hello Nasty")
            confidence = min(confidence + bonus, 100)
            LOG.info((album, best, confidence))
            return (confidence,
                    {
                        'data': data,
                        'name': None,
                        'type': 'album'
                    })
        return SpotifyClient.NOTHING_FOUND

    def query_song(self, song) -> Tuple[int, Dict]:
        """ Try to find a song.
            Searches Spotify for song and artist if provided.
            Arguments:
                song (str): Song to search for
            Returns: Tuple with confidence (0-100) and data or NOTHING_FOUND
        """
        by_word = ' by '  # TODO lang support
        if len(song.split(by_word)) > 1:
            song, artist = song.split(by_word)
            song_search = '*{}* artist:{}'.format(song, artist)
        else:
            song_search = song

        data = self.spotify.search(song_search, type='track')
        if data and len(data['tracks']['items']) > 0:
            tracks = [(self.best_confidence(d['name'], song), d)
                      for d in data['tracks']['items']]
            tracks.sort(key=lambda x: x[0])
            tracks.reverse()  # Place best matches first
            # Find pretty similar tracks to the best match
            tracks = [t for t in tracks if t[0] > tracks[0][0] - 0.1]
            # Sort remaining tracks by popularity
            tracks.sort(key=lambda x: x[1]['popularity'])
            bonus = int(fuzzy_match(song_search, tracks[-1][1]['artists'][0]['name'],
                                    strategy=MatchStrategy.TOKEN_SET_RATIO) * 100)
            LOG.debug([(t[0] + bonus, t[1]['name'], t[1]['artists'][0]['name'])
                       for t in tracks])
            data['tracks']['items'] = [tracks[-1][1]]
            return (min(100, tracks[-1][0] + bonus) ,
                    {'data': data, 'name': None, 'type': 'track'})
        else:
            return SpotifyClient.NOTHING_FOUND

    def get_best_user_playlist(self, playlist) -> Tuple[str, int]:
        """ Get best playlist matching the provided name
        Arguments:
            playlist (str): Playlist name
        Returns: ((str)best match, (float)confidence)
        """
        playlists = self.playlists
        if len(playlists) > 0:
            # Only check if the user has playlists
            key, confidence = match_one(playlist.lower(), playlists,
                                        strategy=MatchStrategy.TOKEN_SET_RATIO)
            if confidence > 0.7:
                return key, int(confidence * 100)
        return SpotifyClient.NOTHING_FOUND

    def tracks_from_playlist(self, playlist_id):
        playlist_id = playlist_id.replace("spotify:playlist:", "")
        return self.spotify.playlist_tracks(playlist_id)

    def tracks_from_artist(self, artist_id):
        # get top tracks
        # spotify:artist:3TOqt5oJwL9BE2NG9MEwDa
        top_tracks = self.spotify.artist_top_tracks(artist_id)
        return [t for t in top_tracks["tracks"]]

    def tracks_from_album(self, artist_id):
        # get top tracks
        # spotify:artist:3TOqt5oJwL9BE2NG9MEwDa
        top_tracks = self.spotify.album_tracks(artist_id)
        return [t for t in top_tracks["items"]]


if __name__ == "__main__":
    from pprint import pprint

    spotify = SpotifyClient()


    def search_artists(query):
        score, data = spotify.query_artist(query)

        for artist in data["data"]["artists"]["items"]:

            uri = artist["uri"]
            playlist = []

            for t in spotify.tracks_from_artist(uri):
                playlist.append({
                    "title": t["name"],
                    "duration": t["duration_ms"] / 1000,
                    "artist": artist["name"],
                    "match_confidence": score,
                    "media_type": MediaType.MUSIC,
                    "uri": t["uri"],
                    "playback": PlaybackType.AUDIO,
                    # "skill_icon": self.skill_icon,
                    "skill_id": "spotify.openvoiceos",
                    "image": artist["images"][-1]["url"] if artist["images"] else "",
                    "bg_image": artist["images"][0]["url"] if artist["images"] else ""
                })

            entry = {
                "match_confidence": score,
                "media_type": MediaType.MUSIC,
                "playlist": playlist[:25],
                "playback": PlaybackType.AUDIO,
                # "skill_icon": self.skill_icon,
                "skill_id": "spotify.openvoiceos",
                "image": artist["images"][-1]["url"] if artist["images"] else "",
                "bg_image": artist["images"][0]["url"] if artist["images"] else "",
                "title": artist["name"] + " (Featured Tracks)"
            }
            yield entry


    def search_albums(query):
        score, data = spotify.query_album(query)

        for album in data["data"]["albums"]["items"]:

            uri = album["uri"]
            artist = album["artists"][0]
            playlist = []

            for t in spotify.tracks_from_album(uri):
                artist = t["artists"][0]
                playlist.append({
                    "title": t["name"],
                    "duration": t["duration_ms"] / 1000,
                    "artist": artist["name"],
                    "match_confidence": score,
                    "media_type": MediaType.MUSIC,
                    "uri": t["uri"],
                    "playback": PlaybackType.AUDIO,
                    # "skill_icon": self.skill_icon,
                    "skill_id": "spotify.openvoiceos",
                    "image": album["images"][-1]["url"] if album["images"] else "",
                    "bg_image": album["images"][0]["url"] if album["images"] else ""
                })

            entry = {
                "match_confidence": score,
                "media_type": MediaType.MUSIC,
                "playlist": playlist[:25],
                "playback": PlaybackType.AUDIO,
                # "skill_icon": self.skill_icon,
                "skill_id": "spotify.openvoiceos",
                "image": album["images"][-1]["url"] if album["images"] else "",
                "bg_image": album["images"][0]["url"] if album["images"] else "",
                "title": album["name"] + " (Full Album)"
            }
            yield entry


    def search_tracks(query):
        score, data = spotify.query_song(query)

        for track in data["data"]["tracks"]["items"]:
            album = track["album"]
            pprint(track)
            entry = {
                "title": track["name"],
                "duration": track["duration_ms"] / 1000,
                "match_confidence": score,
                "media_type": MediaType.MUSIC,
                "uri": track["uri"],
                "playback": PlaybackType.AUDIO,
                # "skill_icon": self.skill_icon,
                "skill_id": "spotify.openvoiceos",
                "image": album["images"][-1]["url"] if album["images"] else "",
                "bg_image": album["images"][0]["url"] if album["images"] else ""
            }
            yield entry


    def search_playlists(query):
        data, score = spotify.get_best_user_playlist(query)
        uri = data["uri"]
        playlist = []
        for t in spotify.tracks_from_playlist(uri)["items"]:
            t = t["track"]
            artist = t["artists"][0]
            playlist.append({
                "title": t["name"],
                "duration": t["duration_ms"] / 1000,
                "artist": artist["name"],
                "match_confidence": score,
                "media_type": MediaType.MUSIC,
                "uri": t["uri"],
                "playback": PlaybackType.AUDIO,
                # "skill_icon": self.skill_icon,
                "skill_id": "spotify.openvoiceos",
                "image": data["images"][-1]["url"] if data["images"] else "",
                "bg_image": data["images"][0]["url"] if data["images"] else ""
            })
        entry = {
            "match_confidence": score,
            "media_type": MediaType.MUSIC,
            "playlist": playlist[:25],
            "playback": PlaybackType.AUDIO,
            # "skill_icon": self.skill_icon,
            "skill_id": "spotify.openvoiceos",
            "image": data["images"][-1]["url"] if data["images"] else "",
            "bg_image": data["images"][0]["url"] if data["images"] else "",
            "title": data["name"]
        }
        yield entry
