import re
import time
from functools import wraps

import requests
import spotipy
from ovos_backend_client.api import OAuthApi
from ovos_utils.log import LOG
from ovos_utils.ocp import PlaybackType, MediaType
from ovos_utils.parse import match_one, fuzzy_match
from requests.exceptions import HTTPError
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
        self.access_token = None
        self.expiration_time = None
        self.get_access_token()

    @staticmethod
    def get_token():
        """ Get token with a single retry."""
        retry = False
        d = None
        try:
            d = OAuthApi().get_oauth_token(OAUTH_TOKEN_ID)
        except HTTPError as e:
            if e.response.status_code == 404:  # Token doesn't exist
                raise SpotifyNotAuthorizedError
            if e.response.status_code == 401:  # Device isn't paired
                raise SpotifyNotAuthorizedError
            else:
                retry = True
        if retry:
            d = OAuthApi().get_oauth_token(OAUTH_TOKEN_ID)
        if not d:
            raise SpotifyNotAuthorizedError
        return d

    def get_access_token(self, force=False):
        if (not self.access_token or time.time() > self.expiration_time or force):
            d = self.get_token()
            self.access_token = d['access_token']
            # get expiration time from message, if missing assume 1 hour
            self.expiration_time = d.get('expiration') or time.time() + 3600
        return self.access_token


def refresh_spotify_oauth(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except HTTPError as e:
            if e.response.status_code == 401:
                self.client_credentials_manager.get_access_token(force=True)
                return func(self, *args, **kwargs)
            else:
                raise

    return wrapper


class SpotifyConnect(spotipy.Spotify):
    """ Implement the Spotify Connect API.
    See:  https://developer.spotify.com/web-api/
    This class extends the spotipy.Spotify class with the refresh_auth decorator
    """

    @staticmethod
    def get_album_info(data):
        """ Get album info from data object.
        Arguments:
            data: data structure from spotify
        Returns: tuple with name, [artists], uri)
        """
        return (data['albums']['items'][0]['name'],
                [a['name'] for a in data['albums']['items'][0]['artists']],
                data['albums']['items'][0]['uri'])

    @staticmethod
    def get_artist_info(data):
        """ Get artist info from data object.
        Arguments:
            data: data structure from spotify
        Returns: tuple with name, uri)
        """
        return (data['artists']['items'][0]['name'],
                data['artists']['items'][0]['uri'])

    @staticmethod
    def get_song_info(data):
        """ Get song info from data object.
        Arguments:
            data: data structure from spotify
        Returns: tuple with name, [artists], uri)
        """
        return (data['tracks']['items'][0]['name'],
                [a['name'] for a in data['tracks']['items'][0]['artists']],
                data['tracks']['items'][0]['uri'])

    @staticmethod
    def status_info(status):
        """ Return track, artist, album tuple from spotify status.
            Arguments:
                status (dict): Spotify status info
            Returns:
                tuple (track, artist, album)
         """
        try:
            artist = status['item']['artists'][0]['name']
        except Exception:
            artist = 'unknown'
        try:
            track = status['item']['name']
        except Exception:
            track = 'unknown'
        try:
            album = status['item']['album']['name']
        except Exception:
            album = 'unknown'
        return track, artist, album

    @refresh_spotify_oauth
    def get_devices(self):
        """ Get a list of Spotify devices from the API.
        Returns:
            list of spotify devices connected to the user.
        """
        # TODO: Cache for a brief time
        devices = self.devices()
        return devices.get('devices', [])

    def get_device(self, dev_id):
        for d in self.get_devices():
            if d["id"] == dev_id:
                return d
        return None

    @refresh_spotify_oauth
    def status(self):
        """ Get current playback status (across the Spotify system) """
        return self.current_user_playing_track()

    @refresh_spotify_oauth
    def is_playing(self, device=None):
        """ Get playback state, either across Spotify or for given device.
        Args:
            device (int): device id to check, if None playback on any device
                          will be reported.
        Returns:
            True if specified device is playing
        """
        try:
            status = self.status()
            if not status['is_playing'] or device is None:
                return status['is_playing']

            # Verify it is playing on the given device
            dev = self.get_device(device)
            return dev and dev['is_active']
        except:
            # Technically a 204 return from status() request means 'no track'
            return False  # assume not playing

    @refresh_spotify_oauth
    def transfer_playback(self, device_id, force_play=True):
        """ Transfer playback to another device.
        Arguments:
            device_id (int):      transfer playback to this device
            force_play (boolean): true if playback should start after
                                  transfer
        """
        super().transfer_playback(device_id=device_id, force_play=force_play)

    @refresh_spotify_oauth
    def play(self, device, uris=None, context_uri=None):
        """ Start playback of tracks, albums or artist.
        Can play either a list of uris or a context_uri for things like
        artists and albums. Both uris and context_uri shouldn't be provided
        at the same time.
        Args:
            device (int):      device id to start playback on
            uris (list):       list of track uris to play
            context_uri (str): Spotify context uri for playing albums or
                               artists.
        """
        self.start_playback(device_id=device, uris=uris, context_uri=context_uri)

    @refresh_spotify_oauth
    def pause(self, device):
        """ Pause user's playback on device.
        Arguments:
            device_id: device to pause
        """
        self.pause_playback(device_id=device)

    @refresh_spotify_oauth
    def next(self, device):
        """ Skip track.
        Arguments:
            device_id: device id for playback
        """
        self.next_track(device_id=device)

    @refresh_spotify_oauth
    def prev(self, device):
        """ Move back in playlist.
        Arguments
            device_id: device target for playback
        """
        self.previous_track(device_id=device)

    @refresh_spotify_oauth
    def volume(self, device, volume):
        """ Set volume of device:
        Parameters:
            device: device id
            volume: volume in percent
        """
        super().volume(volume_percent=volume, device_id=device)

    @refresh_spotify_oauth
    def shuffle(self, state):
        """ Toggle shuffling
            Parameters:
                state: Shuffle state
        """
        super().shuffle(state)  # TODO pass device_id

    @refresh_spotify_oauth
    def repeat(self, state):
        """ Toggle repeat
        state:
            track - will repeat the current track.
            context - will repeat the current context.
            off - will turn repeat off.

            Parameters:
                state: Shuffle state
        """
        super().repeat(state)  # TODO pass device_id


class SpotifyClient:
    # Return value definition indication nothing was found
    # (confidence None, data None)
    NOTHING_FOUND = (None, 0.0)
    # Confidence levels for generic play handling
    DIRECT_RESPONSE_CONFIDENCE = 0.8

    MATCH_CONFIDENCE = 0.5

    def __init__(self):
        self._spotify = None
        self.__playlists_fetched = 0
        self._playlists = None

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
    def best_confidence(title, query):
        """Find best match for a title against a query.
        Some titles include ( Remastered 2016 ) and similar info. This method
        will test the raw title and a version that has been parsed to remove
        such information.
        Arguments:
            title: title name from spotify search
            query: query from user
        Returns:
            (float) best condidence
        """
        best = title.lower()
        best_stripped = re.sub(r'(\(.+\)|-.+)$', '', best).strip()
        return max(fuzzy_match(best, query),
                   fuzzy_match(best_stripped, query))

    def load_credentials(self):
        """ Retrieve credentials from the backend and connect to Spotify """
        try:
            creds = OVOSSpotifyCredentials()
            self._spotify = SpotifyConnect(client_credentials_manager=creds)
        except(HTTPError, SpotifyNotAuthorizedError):
            LOG.error('Couldn\'t fetch spotify credentials')

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

    def generic_query(self, phrase, bonus=0):
        """ Check for a generic query, not asking for any special feature.
            This will try to parse the entire phrase in the following order
            - As a user playlist
            - As an album
            - As a track
            - As a public playlist
            Arguments:
                phrase (str): Text to match against
                bonus (float): Any existing match bonus
            Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        LOG.info('Handling "{}" as a genric query...'.format(phrase))
        results = []
        data = {}
        LOG.info('Checking users playlists')
        playlist, conf = self.get_best_user_playlist(phrase)
        if playlist:
            uri = self.playlists[playlist]
            data = {
                'data': uri,
                'name': playlist,
                'type': 'playlist'
            }
        if conf and conf > SpotifyClient.DIRECT_RESPONSE_CONFIDENCE:
            return (conf, data)
        elif conf and conf > SpotifyClient.MATCH_CONFIDENCE:
            results.append((conf, data))

        # Check for artist
        LOG.info('Checking artists')
        conf, data = self.query_artist(phrase, bonus=0)
        if conf and conf > SpotifyClient.DIRECT_RESPONSE_CONFIDENCE:
            return conf, data
        elif conf and conf > SpotifyClient.MATCH_CONFIDENCE:
            results.append((conf, data))

        # Check for track
        LOG.info('Checking tracks')
        conf, data = self.query_song(phrase, bonus=0)
        if conf and conf > SpotifyClient.DIRECT_RESPONSE_CONFIDENCE:
            return conf, data
        elif conf and conf > SpotifyClient.MATCH_CONFIDENCE:
            results.append((conf, data))

        # Check for album
        LOG.info('Checking albums')
        conf, data = self.query_album(phrase, bonus=0)
        if conf and conf > SpotifyClient.DIRECT_RESPONSE_CONFIDENCE:
            return conf, data
        elif conf and conf > SpotifyClient.MATCH_CONFIDENCE:
            results.append((conf, data))

        # Check for public playlist
        LOG.info('Checking public playlists')
        conf, data = self.get_best_public_playlist(phrase)
        if conf and conf > SpotifyClient.DIRECT_RESPONSE_CONFIDENCE:
            return conf, data
        elif conf and conf > SpotifyClient.MATCH_CONFIDENCE:
            results.append((conf, data))

        return self.best_result(results)

    def query_artist(self, artist, bonus=0.0):
        """Try to find an artist.
            Arguments:
                artist (str): Artist to search for
                bonus (float): Any bonus to apply to the confidence
            Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        bonus += 0.1
        data = self.spotify.search(artist, type='artist')
        if data and data['artists']['items']:
            best = data['artists']['items'][0]['name']
            confidence = fuzzy_match(best, artist.lower()) + bonus
            confidence = min(confidence, 1.0)
            return (confidence,
                    {
                        'data': data,
                        'name': None,
                        'type': 'artist'
                    })
        else:
            return SpotifyClient.NOTHING_FOUND

    def query_album(self, album, bonus=0):
        """ Try to find an album.
            Searches Spotify by album and artist if available.
            Arguments:
                album (str): Album to search for
                bonus (float): Any bonus to apply to the confidence
            Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        # TODO localize
        by_word = ' by '
        if len(album.split(by_word)) > 1:
            album, artist = album.split(by_word)
            album_search = '*{}* artist:{}'.format(album, artist)
            bonus += 0.1
        else:
            album_search = album
        data = self.spotify.search(album_search, type='album')
        if data and data['albums']['items']:
            best = data['albums']['items'][0]['name'].lower()
            confidence = self.best_confidence(best, album)
            # Also check with parentheses removed for example
            # "'Hello Nasty ( Deluxe Version/Remastered 2009" as "Hello Nasty")
            confidence = min(confidence + bonus, 1.0)
            LOG.info((album, best, confidence))
            return (confidence,
                    {
                        'data': data,
                        'name': None,
                        'type': 'album'
                    })
        return SpotifyClient.NOTHING_FOUND

    def query_song(self, song, bonus=0):
        """ Try to find a song.
            Searches Spotify for song and artist if provided.
            Arguments:
                song (str): Song to search for
                bonus (float): Any bonus to apply to the confidence
            Returns: Tuple with confidence and data or NOTHING_FOUND
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
            LOG.debug([(t[0], t[1]['name'], t[1]['artists'][0]['name'])
                       for t in tracks])
            data['tracks']['items'] = [tracks[-1][1]]
            return (tracks[-1][0] + bonus,
                    {'data': data, 'name': None, 'type': 'track'})
        else:
            return SpotifyClient.NOTHING_FOUND

    def get_best_user_playlist(self, playlist):
        """ Get best playlist matching the provided name
        Arguments:
            playlist (str): Playlist name
        Returns: ((str)best match, (float)confidence)
        """
        playlists = self.playlists
        if len(playlists) > 0:
            # Only check if the user has playlists
            key, confidence = match_one(playlist.lower(), playlists)
            if confidence > 0.7:
                return key, confidence
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

