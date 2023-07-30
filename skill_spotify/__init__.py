# Copyright 2023 Åke Forslund
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import random
import re
import time

from enum import Enum
from socket import gethostname

from rapidfuzz import fuzz
import spotipy

from adapt.intent import IntentBuilder
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill, \
    MediaType, PlaybackType, ocp_search, MatchConfidence, ocp_play
from ovos_utils import classproperty
from ovos_utils.process_utils import RuntimeRequirements

from .exceptions import (NoSpotifyDevicesError,
                         PlaylistNotFoundError,
                         SpotifyNotAuthorizedError)

from .spotify import (SpotifyConnect,
                      get_album_info, get_artist_info, get_song_info,
                      get_show_info, load_local_credentials)


class DeviceType(Enum):
    MYCROFT = 1
    DEFAULT = 2
    DESKTOP = 3
    FIRSTBEST = 4
    NOTFOUND = 5

# Return value definition indication nothing was found
# (confidence None, data None)
NOTHING_FOUND = (None, 0.0)

# Confidence levels for generic play handling
DIRECT_RESPONSE_CONFIDENCE = 0.8

MATCH_CONFIDENCE = 0.5


def best_result(results):
    """Return best result from a list of result tuples.

    Arguments:
        results (list): list of spotify result tuples

    Returns:
        Best match in list
    """
    if len(results) == 0:
        return NOTHING_FOUND
    else:
        results.reverse()
        return sorted(results, key=lambda x: x[0])[-1]


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
    return max(fuzz.ratio(best, query),
               fuzz-ratio(best_stripped, query))


def fuzzy_match(compare, input_string):
    return fuzz.ratio(compare, input_string)


def match_one(query, choices):
    selected = process.extractOne(query, choices, fuzz.WRatio)
    if selected:
        return selected[0]
    else:
        return None


def status_info(status):
    """Return track, artist, album tuple from spotify status.

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


class SkillSpotify(OVOSCommonPlaybackSkill):

    """Spotify control through the Spotify Connect API."""

    def __init__(self):
        super().__init__('SkillSpotify')
        self.index = 0
        self.spotify = None
        self.process = None
        self.device_name = None
        self.dev_id = None
        self.idle_count = 0
        self.ducking = False
        self.is_player_remote = False   # when dev is remote control instance
        self.mouth_text = None

        self.__device_list = None
        self.__devices_fetched = 0
        self.OAUTH_ID = 1
        enclosure_config = self.config_core.get('enclosure')
        self.platform = enclosure_config.get('platform', 'unknown')
        self.DEFAULT_VOLUME = 80 if self.platform == 'mycroft_mark_1' else 100
        self._playlists = None
        self.saved_tracks = None
        self.regexes = {}
        self.last_played_type = None  # The last uri type that was started
        self.is_playing = False
        self.__saved_tracks_fetched = 0

    @classproperty
    def runtime_requirements(self):
        return RuntimeRequirements(internet_before_load=True,
                                   network_before_load=True,
                                   gui_before_load=False,
                                   requires_internet=True,
                                   requires_network=True,
                                   requires_gui=False,
                                   no_internet_fallback=False,
                                   no_network_fallback=False,
                                   no_gui_fallback=True)

    def translate_regex(self, regex):
        if regex not in self.regexes:
            path = self.find_resource(regex + '.regex')
            if path:
                with open(path) as f:
                    string = f.read().strip()
                self.regexes[regex] = string
        return self.regexes[regex]

    def initialize(self):
        # Make sure the spotify login scheduled event is shutdown
        super().initialize()
        if not self.spotify:
            try:
                self.load_credentials()
            except Exception as e:
                self.log.info('Credentials could not be fetched. '
                               '({})'.format(repr(e)))

        if self.spotify:
            # Refresh saved tracks
            # We can't get this list when the user asks because it takes
            # too long and causes
            # mycroft-playback-control.mycroftai:PlayQueryTimeout
            self.refresh_saved_tracks()

    def load_local_creds(self):
        try:
            creds = load_local_credentials('cazed')
            spotify = SpotifyConnect(client_credentials_manager=creds)
        except Exception:
            self.log.exception('Couldn\'t fetch credentials')
            spotify = None
        return spotify

    def load_credentials(self):
        """Retrieve credentials and connect to spotify.

        This will load local credentials if available otherwise fetching
        remote settings from mycroft backend will be attempted.

        NOTE: the remote fetching is only a preparation for the future and
        will always fail at the moment.
        """
        self.spotify = self.load_local_creds()
        if self.spotify:
            # Spotify connection worked, prepare for usage
            # TODO: Repeat occasionally on failures?
            # If not able to authorize, the method will be repeated after 60
            # seconds
            self.create_intents()

    def failed_auth(self):
        if 'user' not in self.settings:
            self.log.error('Settings hasn\'t been received yet')
            self.speak_dialog('NoSettingsReceived')
        elif not self.settings.get("user"):
            self.log.error('User info has not been set.')
            # Assume this is initial setup
            self.speak_dialog('NotConfigured')
        else:
            # Assume password changed or there is a typo
            self.log.error('User info has been set but Auth failed.')
            self.speak_dialog('NotAuthorized')

    ######################################################################
    # Handle auto ducking when listener is started.

    def handle_listener_started(self, message):
        """Handle auto ducking when listener is started.

        The ducking is enabled/disabled using the skill settings on home.

        TODO: Evaluate the Idle check logic
        """
        if (self.spotify.is_playing() and self.is_player_remote and
                self.settings.get('use_ducking', False)):
            self.__pause()
            self.ducking = True

            # Start idle check
            self.idle_count = 0
            self.cancel_scheduled_event('IdleCheck')
            self.schedule_repeating_event(self.check_for_idle, None,
                                          1, name='IdleCheck')

    def check_for_idle(self):
        """Repeating event checking for end of auto ducking."""
        if not self.ducking:
            self.cancel_scheduled_event('IdleCheck')
            return

        active = self.enclosure.display_manager.get_active()
        if not active == '' or active == 'SpotifySkill':
            # No activity, start to fall asleep
            self.idle_count += 1

            if self.idle_count >= 5:
                # Resume playback after 5 seconds of being idle
                self.cancel_scheduled_event('IdleCheck')
                self.ducking = False
                self.resume()
        else:
            self.idle_count = 0

    @ocp_search()
    def match_query_phrase(self, phrase, media_type):
        """Handler for common play framework Query."""
        self.log.info(f'Searching spotify for {phrase}')
        # Not ready to play
        if not self.spotify:
            self.log.info('Spotify is not available to search')
            if 'spotify' in phrase:
                return [{
                    "match_confidence": level,
                    "media_type": MediaType.MUSIC,
                    "playback": PlaybackType.AUDIO,
                    "skill_icon": self.skill_icon,
                }]
            else:
                return None

        spotify_specified = 'spotify' in phrase
        bonus = 0.1 if spotify_specified else 0.0
        phrase = re.sub(self.translate_regex('on_spotify'), '', phrase,
                        re.IGNORECASE)

        confidence, data = self.continue_playback(phrase, bonus)
        if not data:
            confidence, data = self.specific_query(phrase, bonus)
            if not data:
                confidence, data = self.generic_query(phrase, bonus)

        if data:
            self.log.info('Spotify confidence: {}'.format(confidence))
            self.log.info('              data: {}'.format(data))

            if data.get('type') in ['saved_tracks', 'album', 'artist',
                                    'track', 'playlist', 'show']:
                if spotify_specified:
                    # " play great song on spotify'
                    level = 100
                else:
                    level = confidence
            elif data.get('type') == 'continue':
                if spotify_specified > 0:
                    # "resume playback on spotify"
                    level = 100
                else:
                    # "resume playback"
                    level = 70
                    phrase += ' on spotify'
            else:
                self.log.warning('Unexpected spotify type: '
                                 '{}'.format(data.get('type')))
                level = 70
            from pprint import pprint
            pprint(data)
            response = [{
                "match_confidence": level,
                "uri": f"spotify://{data['data']['uri']}",
                "media_type": MediaType.MUSIC,
                "playback": PlaybackType.AUDIO,
                "skill_icon": self.skill_icon,
            }]
            pprint(response)
            return response
        else:
            self.log.debug('Couldn\'t find anything to play on Spotify')

    def continue_playback(self, phrase, bonus):
        if phrase.strip() == 'spotify':
            return (1.0,
                    {
                        'data': None,
                        'name': None,
                        'type': 'continue'
                    })
        else:
            return NOTHING_FOUND

    def specific_query(self, phrase, bonus):
        """
        Check if the phrase can be matched against a specific spotify request.

        This includes asking for saved items, playlists, albums, podcasts,
        artists or songs.

        Arguments:
            phrase (str): Text to match against
            bonus (float): Any existing match bonus

        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        # Check if saved
        match = re.match(self.translate_regex('saved_songs'), phrase,
                         re.IGNORECASE)
        if match and self.saved_tracks:
            return (1.0, {'data': None,
                          'type': 'saved_tracks'})

        # Check if playlist
        match = re.match(self.translate_regex('playlist'), phrase,
                         re.IGNORECASE)
        if match:
            return self.query_playlist(match.groupdict()['playlist'])

        # Check album
        match = re.match(self.translate_regex('album'), phrase,
                         re.IGNORECASE)
        if match:
            bonus += 0.1
            album = match.groupdict()['album']
            return self.query_album(album, bonus)

        # Check artist
        match = re.match(self.translate_regex('artist'), phrase,
                         re.IGNORECASE)
        if match:
            artist = match.groupdict()['artist']
            return self.query_artist(artist, bonus)
        match = re.match(self.translate_regex('song'), phrase,
                         re.IGNORECASE)
        if match:
            song = match.groupdict()['track']
            return self.query_song(song, bonus)

        # Check if podcast
        match = re.match(self.translate_regex('podcast'), phrase,
                         re.IGNORECASE)
        if match:
            return self.query_show(match.groupdict()['podcast'])

        return NOTHING_FOUND

    def generic_query(self, phrase, bonus):
        """Check for a generic query, not asking for any special feature.

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
        self.log.info('Handling "{}" as a genric query...'.format(phrase))
        results = []

        self.log.info('Checking users playlists')
        playlist, conf = self.get_best_user_playlist(phrase)
        if playlist:
            uri = self.playlists[playlist]
            data = {
                        'data': uri,
                        'name': playlist,
                        'type': 'playlist'
                   }
        if conf and conf > DIRECT_RESPONSE_CONFIDENCE:
            return (conf, data)
        elif conf and conf > MATCH_CONFIDENCE:
            results.append((conf, data))

        # Check for artist
        self.log.info('Checking artists')
        conf, data = self.query_artist(phrase, bonus)
        if conf and conf > DIRECT_RESPONSE_CONFIDENCE:
            return conf, data
        elif conf and conf > MATCH_CONFIDENCE:
            results.append((conf, data))

        # Check for track
        self.log.info('Checking tracks')
        conf, data = self.query_song(phrase, bonus)
        if conf and conf > DIRECT_RESPONSE_CONFIDENCE:
            return conf, data
        elif conf and conf > MATCH_CONFIDENCE:
            results.append((conf, data))

        # Check for album
        self.log.info('Checking albums')
        conf, data = self.query_album(phrase, bonus)
        if conf and conf > DIRECT_RESPONSE_CONFIDENCE:
            return conf, data
        elif conf and conf > MATCH_CONFIDENCE:
            results.append((conf, data))

        # Check for public playlist
        self.log.info('Checking tracks')
        conf, data = self.get_best_public_playlist(phrase)
        if conf and conf > DIRECT_RESPONSE_CONFIDENCE:
            return conf, data
        elif conf and conf > MATCH_CONFIDENCE:
            results.append((conf, data))

        return best_result(results)

    def query_artist(self, artist, bonus=0.0):
        """Try to find an artist.

        Arguments:
            artist (str): Artist to search for
            bonus (float): Any bonus to apply to the confidence

        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        bonus += 0.1
        for _ in range(5):
            try:
                data = self.spotify.search(artist, type='artist')
                break
            except Exception:
                sleep(0.05)
        else:
            self.log.warning("Search failed")
            return NOTHING_FOUND
        if data and data['artists']['items']:
            best = data['artists']['items'][0]
            confidence = fuzzy_match(best['name'], artist.lower()) + bonus
            confidence = min(confidence, 100)
            from pprint import pprint
            pprint(best)
            return (confidence,
                    {
                        'data': best,
                        'name': None,
                        'type': 'artist'
                    })
        else:
            return NOTHING_FOUND

    def query_album(self, album, bonus):
        """Try to find an album.

        Searches Spotify by album and artist if available.

        Arguments:
            album (str): Album to search for
            bonus (float): Any bonus to apply to the confidence

        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        data = None
        by_word = ' {} '.format(self.translate('by'))
        if len(album.split(by_word)) > 1:
            album, artist = album.split(by_word)
            album_search = '*{}* artist:{}'.format(album, artist)
            bonus += 0.1
        else:
            album_search = album
        data = self.spotify.search(album_search, type='album')
        if data and data['albums']['items']:
            best = data['albums']['items'][0]['name'].lower()
            confidence = best_confidence(best, album)
            # Also check with parentheses removed for example
            # "'Hello Nasty ( Deluxe Version/Remastered 2009" as "Hello Nasty")
            confidence = min(confidence + bonus, 1.0)
            self.log.info((album, best, confidence))
            return (confidence,
                    {
                        'data': data,
                        'name': None,
                        'type': 'album'
                    })
        return NOTHING_FOUND

    def query_playlist(self, playlist):
        """Try to find a playlist.

        First searches the users playlists, then tries to find a public
        one.

        Arguments:
            playlist (str): Playlist to search for

        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        result, conf = self.get_best_user_playlist(playlist)
        if playlist and conf > 0.5:
            uri = self.playlists[result]
            return (conf, {'data': uri,
                           'name': playlist,
                           'type': 'playlist'})
        else:
            return self.get_best_public_playlist(playlist)

    def query_show(self, podcast):
        """Try to find a podcast.

        First searches the users playlists, then tries to find a public
        one.

        Arguments:
            podcast (str): Playlist to search for

        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        data = self.spotify.search(podcast, type='show')
        if data and data['shows']['items']:
            best = data['shows']['items'][0]['name'].lower()
            confidence = best_confidence(best, podcast)
            return (confidence, {'data': data, 'type': 'show'})

    def query_song(self, song, bonus):
        """Try to find a song.

        Searches Spotify for song and artist if provided.

        Arguments:
            song (str): Song to search for
            bonus (float): Any bonus to apply to the confidence

        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        data = None
        by_word = ' {} '.format(self.translate('by'))
        if len(song.split(by_word)) > 1:
            song, artist = song.split(by_word)
            song_search = '*{}* artist:{}'.format(song, artist)
        else:
            song_search = song

        data = self.spotify.search(song_search, type='track')
        if data and len(data['tracks']['items']) > 0:
            tracks = [(best_confidence(d['name'], song), d)
                      for d in data['tracks']['items']]
            tracks.sort(key=lambda x: x[0])
            tracks.reverse()  # Place best matches first
            # Find pretty similar tracks to the best match
            tracks = [t for t in tracks if t[0] > tracks[0][0] - 0.1]
            # Sort remaining tracks by popularity
            tracks.sort(key=lambda x: x[1]['popularity'])
            self.log.debug([(t[0], t[1]['name'], t[1]['artists'][0]['name'])
                            for t in tracks])
            data['tracks']['items'] = [tracks[-1][1]]
            return (tracks[-1][0] + bonus,
                    {'data': data, 'name': None, 'type': 'track'})
        else:
            return NOTHING_FOUND

    def create_intents(self):
        """Setup the spotify intents."""
        intent = IntentBuilder('').require('Spotify').require('Search') \
                                  .require('For')
        self.register_intent(intent, self.search_spotify)
        self.register_intent_file('ShuffleOn.intent', self.shuffle_on)
        self.register_intent_file('ShuffleOff.intent', self.shuffle_off)
        self.register_intent_file('WhatSong.intent', self.song_info)
        self.register_intent_file('WhatAlbum.intent', self.album_info)
        self.register_intent_file('WhatArtist.intent', self.artist_info)
        time.sleep(0.5)

    def enable_playing_intents(self):
        self.enable_intent('WhatSong.intent')
        self.enable_intent('WhatAlbum.intent')
        self.enable_intent('WhatArtist.intent')

    def disable_playing_intents(self):
        self.disable_intent('WhatSong.intent')
        self.disable_intent('WhatAlbum.intent')
        self.disable_intent('WhatArtist.intent')

    @property
    def playlists(self):
        """Playlists, cached for 5 minutes."""
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

    def refresh_saved_tracks(self):
        """Saved tracks are cached for 4 hours."""
        if not self.spotify:
            return []
        now = time.time()
        if (not self.saved_tracks or
                (now - self.__saved_tracks_fetched > 4 * 60 * 60)):
            saved_tracks = []
            offset = 0
            while True:
                batch = self.spotify.current_user_saved_tracks(50, offset)
                for item in batch.get('items', []):
                    saved_tracks.append(item['track'])
                offset += 50
                if not batch['next']:
                    break

            self.saved_tracks = saved_tracks
            self.__saved_tracks_fetched = now

    @property
    def devices(self):
        """Devices, cached for 60 seconds."""
        if not self.spotify:
            return []  # No connection, no devices
        now = time.time()
        if not self.__device_list or (now - self.__devices_fetched > 60):
            self.__device_list = self.spotify.get_devices()
            self.__devices_fetched = now
        return self.__device_list

    def device_by_name(self, name):
        """Get a Spotify devices from the API.

        Arguments:
            name (str): The device name (fuzzy matches)
        Returns:
            (dict) None or the matching device's description
        """
        devices = self.devices
        if devices and len(devices) > 0:
            # Otherwise get a device with the selected name
            devices_by_name = {d['name'].lower(): d for d in devices}
            key, confidence = match_one(name, list(devices_by_name.keys()))
            if confidence > 0.5:
                return devices_by_name[key]
        return None

    def get_default_device(self):
        """Get preferred playback device."""
        if self.spotify:
            # When there is an active Spotify device somewhere, use it
            if (self.devices and len(self.devices) > 0 and
                    self.spotify.is_playing()):
                for dev in self.devices:
                    if dev['is_active']:
                        self.log.info('Playing on an active device '
                                      '[{}]'.format(dev['name']))
                        return dev  # Use this device

            # No playing device found, use the default Spotify device
            default_device = self.settings.get('default_device', '')
            dev = None
            device_type = DeviceType.NOTFOUND
            if default_device:
                dev = self.device_by_name(default_device)
                self.is_player_remote = True
                device_type = DeviceType.DEFAULT
            # if not set or missing try playing on this device
            if not dev:
                dev = self.device_by_name(self.device_name or '')
                self.is_player_remote = False
                device_type = DeviceType.MYCROFT
            # if not check if a desktop spotify client is playing
            if not dev:
                dev = self.device_by_name(gethostname())
                self.is_player_remote = False
                device_type = DeviceType.DESKTOP

            # use first best device if none of the prioritized works
            if not dev and len(self.devices) > 0:
                dev = self.devices[0]
                self.is_player_remote = True  # ?? Guessing it is remote
                device_type = DeviceType.FIRSTBEST

            if dev and not dev['is_active']:
                self.spotify.transfer_playback(dev['id'], False)
            self.log.info('Device detected: {}'.format(device_type))
            return dev

        return None

    def get_best_user_playlist(self, playlist):
        """Get best playlist matching the provided name

        Arguments:
            playlist (str): Playlist name

        Returns: ((str)best match, (float)confidence)
        """
        playlists = list(self.playlists.keys())
        if len(playlists) > 0:
            # Only check if the user has playlists
            key, confidence = match_one(playlist.lower(), playlists)
            if confidence > 0.7:
                return key, confidence
        return NOTHING_FOUND

    def get_best_public_playlist(self, playlist):
        data = self.spotify.search(playlist, type='playlist')
        if data and data['playlists']['items']:
            best = data['playlists']['items'][0]
            confidence = fuzzy_match(best['name'].lower(), playlist)
            if confidence > 0.7:
                return (confidence, {'data': best,
                                     'name': best['name'],
                                     'type': 'playlist'})
        return NOTHING_FOUND

    def search(self, query, search_type):
        """ Search for an album, playlist or artist.
        Arguments:
            query:       search query (album title, artist, etc.)
            search_type: whether to search for an 'album', 'artist',
                         'playlist', 'track', or 'genre'

            TODO: improve results of albums by checking artist
        """
        res = None
        if search_type == 'album' and len(query.split('by')) > 1:
            title, artist = query.split('by')
            result = self.spotify.search(title, type=search_type)
        else:
            result = self.spotify.search(query, type=search_type)

        if search_type == 'album':
            if len(result['albums']['items']) > 0:
                album = result['albums']['items'][0]
                self.log.info(album)
                res = album
        elif search_type == 'artist':
            self.log.info(result['artists'])
            if len(result['artists']['items']) > 0:
                artist = result['artists']['items'][0]
                self.log.info(artist)
                res = artist
        elif search_type == 'genre':
            self.log.debug("TODO! Genre")
        else:
            self.log.error('Search type {} not supported'.format(search_type))
            return

        return res

    def search_spotify(self, message):
        """ Intent handler for "search spotify for X". """

        try:
            dev = self.get_default_device()
            if not dev:
                raise NoSpotifyDevicesError

            utterance = message.data['utterance']
            if len(utterance.split(self.translate('ForAlbum'))) == 2:
                query = utterance.split(self.translate('ForAlbum'))[1].strip()
                data = self.spotify.search(query, type='album')
                self.play(dev, data=data, data_type='album')
            elif len(utterance.split(self.translate('ForArtist'))) == 2:
                query = utterance.split(self.translate('ForArtist'))[1].strip()
                data = self.spotify.search(query, type='artist')
                self.play(dev, data=data, data_type='artist')
            else:
                for_word = ' ' + self.translate('For')
                query = for_word.join(utterance.split(for_word)[1:]).strip()
                data = self.spotify.search(query, type='track')
                self.play(dev, data=data, data_type='track')
        except NoSpotifyDevicesError:
            self.log.error("Unable to get a default device while trying "
                           "to play something.")
            self.speak_dialog(
                'PlaybackFailed',
                {'reason': self.translate('NoDevicesAvailable')})
        except SpotifyNotAuthorizedError:
            self.speak_dialog(
                'PlaybackFailed',
                {'reason': self.translate('NotAuthorized')})
        except PlaylistNotFoundError:
            self.speak_dialog(
                'PlaybackFailed',
                {'reason': self.translate('PlaylistNotFound')})
        except Exception as e:
            self.speak_dialog('PlaybackFailed', {'reason': str(e)})

    def shuffle_on(self):
        """ Turn on shuffling """
        if self.spotify:
            self.spotify.shuffle(True)
        else:
            self.failed_auth()

    def shuffle_off(self):
        """ Turn off shuffling """
        if self.spotify:
            self.spotify.shuffle(False)
        else:
            self.failed_auth()

    def song_info(self, message):
        """ Speak song info. """
        status = self.spotify.status() if self.spotify else None
        # If playback might be happening on, or have been started from, another
        # device, update self.is_playing before proceeding
        if self.is_playing:
            song, artist, _ = status_info(status)
            self.speak_dialog('CurrentSong', {'song': song, 'artist': artist})
        else:
            self.speak_dialog('NothingPlaying')

    def album_info(self, message):
        """ Speak album info. """
        status = self.spotify.status() if self.spotify else None
        # If playback might be happening on, or have been started from, another
        # device, update self.is_playing before proceeding
        if self.is_playing:
            _, _, album = status_info(status)
            if self.last_played_type == 'album':
                self.speak_dialog('CurrentAlbum', {'album': album})
            else:
                self.speak_dialog('OnAlbum', {'album': album})
        else:
            self.speak_dialog('NothingPlaying')

    def artist_info(self, message):
        """ Speak artist info. """
        status = self.spotify.status() if self.spotify else None
        if status:
            # If playback might be happening on, or have been started from,
            # another device, update self.is_playing before proceeding
            if self.is_playing:
                _, artist, _ = status_info(status)
                self.speak_dialog('CurrentArtist', {'artist': artist})
            else:
                self.speak_dialog('NothingPlaying')

    @intent_handler(IntentBuilder('').require('Spotify').require('Device'))
    def list_devices(self, message):
        """ List available devices. """
        self.log.info(self.spotify)
        if self.spotify:
            devices = [d['name'] for d in self.spotify.get_devices()]
            if len(devices) == 1:
                self.speak(devices[0])
            elif len(devices) > 1:
                self.speak_dialog('AvailableDevices',
                                  {'devices': ' '.join(devices[:-1]) + ' ' +
                                              self.translate('And') + ' ' +
                                              devices[-1]})
            else:
                self.speak_dialog('NoDevicesAvailable')
        else:
            self.failed_auth()

    @intent_handler(IntentBuilder('').require('Transfer').require('Spotify')
                                     .require('ToDevice'))
    def transfer_playback(self, message):
        """ Move playback from one device to another. """
        if self.spotify and self.spotify.is_playing():
            dev = self.device_by_name(message.data['ToDevice'])
            if dev:
                self.log.info('Transfering playback to {}'.format(dev['name']))
                self.spotify.transfer_playback(dev['id'])
                # If mycroft is allowed to control playback started elsewhere,
                # update dev_id when playback is transferred between devices
            else:
                self.speak_dialog('DeviceNotFound',
                                  {'name': message.data['ToDevice']})
        elif not self.spotify:
            self.failed_auth()
        else:
            self.speak_dialog('NothingPlaying')

    def _shutdown(self):
        """ Remove the monitor at shutdown. """
        self.cancel_scheduled_event('SpotifyLogin')

        # Do normal shutdown procedure
        super().shutdown(self)
