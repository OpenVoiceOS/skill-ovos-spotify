from os.path import join, dirname
from typing import Iterable

from ovos_skill_spotify.spotify import SpotifyClient
from ovos_utils import classproperty
from ovos_utils.log import LOG
from ovos_utils.process_utils import RuntimeRequirements
from ovos_utils.ocp import MediaType, PlaybackType, MediaEntry, Playlist
from ovos_workshop.decorators.ocp import ocp_search
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill


class SpotifySkill(OVOSCommonPlaybackSkill):
    def __init__(self, *args, **kwargs):
        self.spotify = SpotifyClient()
        super().__init__(supported_media=[MediaType.GENERIC, MediaType.MUSIC],
                         skill_icon=join(dirname(__file__), "spotify.png"),
                         skill_voc_filename="spotify_skill",
                         *args, **kwargs, )
        if not self.has_configured_players():
            LOG.warning("SPOTIFY NOT YET CONFIGURED!")

    @classproperty
    def runtime_requirements(self):
        return RuntimeRequirements(internet_before_load=True,
                                   requires_internet=True)

    def has_configured_players(self) -> bool:
        backends = self.config_core.get("Audio", {}).get("backends", {})
        configured_devices = [d["identifier"] for d in backends.values()
                              if "spotify" in d.get("type", "other") and d.get('active', True)]
        if not configured_devices:
            LOG.warning("no spotify devices configured in mycroft.conf")
            return False

        for d in self.spotify.devices:
            if d["name"] in configured_devices:
                return True
        LOG.warning("no configured spotify devices appear to be online")
        return False

    def search_artists(self, query) -> Iterable[Playlist]:
        score, data = self.spotify.query_artist(query)
        if not data:
            return

        for artist in data["data"]["artists"]["items"]:
            uri = artist["uri"]
            playlist = Playlist(
                title=artist["name"] + " (Featured Tracks)",
                image=artist["images"][-1]["url"] if artist["images"] else "",
                match_confidence=score,
                media_type=MediaType.MUSIC,
                playback=PlaybackType.AUDIO_SERVICE,
                skill_id=self.skill_id,
                skill_icon=self.skill_icon
            )
            for t in self.spotify.tracks_from_artist(uri):
                playlist.append(MediaEntry(media_type=MediaType.MUSIC,
                                           uri=t["uri"],
                                           title=t["name"],
                                           playback=PlaybackType.AUDIO_SERVICE,
                                           image=artist["images"][-1]["url"] if artist["images"] else "",
                                           skill_id=self.skill_id,
                                           artist=artist["name"],
                                           match_confidence=min(100, score),
                                           length=t["duration_ms"] / 1000,
                                           skill_icon=self.skill_icon))
                if len(playlist) > 25:
                    break
            yield playlist

    def search_albums(self, query) -> Iterable[Playlist]:
        score, data = self.spotify.query_album(query)
        if not data:
            return

        for album in data["data"]["albums"]["items"]:
            uri = album["uri"]
            playlist = Playlist(
                title=album["name"] + " (Full Album)",
                image=album["images"][-1]["url"] if album["images"] else "",
                match_confidence=score,
                media_type=MediaType.MUSIC,
                playback=PlaybackType.AUDIO_SERVICE,
                skill_id=self.skill_id,
                skill_icon=self.skill_icon
            )
            for t in self.spotify.tracks_from_album(uri):
                artist = t["artists"][0]
                playlist.append(MediaEntry(media_type=MediaType.MUSIC,
                                           uri=t["uri"],
                                           title=t["name"],
                                           playback=PlaybackType.AUDIO_SERVICE,
                                           image=album["images"][-1]["url"] if album["images"] else "",
                                           skill_id=self.skill_id,
                                           artist=artist["name"],
                                           match_confidence=min(100, score),
                                           length=t["duration_ms"] / 1000,
                                           skill_icon=self.skill_icon))
                if len(playlist) > 25:
                    break
            yield playlist

    def search_tracks(self, query) -> Iterable[MediaEntry]:
        score, data = self.spotify.query_song(query)
        if not data:
            return

        for track in data["data"]["tracks"]["items"]:
            album = track["album"]
            yield MediaEntry(media_type=MediaType.MUSIC,
                             uri=track["uri"],
                             title=track["name"],
                             playback=PlaybackType.AUDIO_SERVICE,
                             image=album["images"][-1]["url"] if album["images"] else "",
                             skill_id=self.skill_id,
                             # artist=ch.artist,
                             match_confidence=min(100, score),
                             length=track["duration_ms"] / 1000,
                             skill_icon=self.skill_icon)

    def search_playlists(self, query) -> Iterable[Playlist]:
        data, score = self.spotify.get_best_user_playlist(query)
        if not data:
            return
        uri = data["uri"]
        playlist = Playlist(
            title=data["name"],
            image=data["images"][-1]["url"] if data["images"] else "",
            match_confidence=score,
            media_type=MediaType.MUSIC,
            playback=PlaybackType.AUDIO_SERVICE,
            skill_id=self.skill_id,
            skill_icon=self.skill_icon
        )
        for t in self.spotify.tracks_from_playlist(uri)["items"]:
            t = t["track"]
            artist = t["artists"][0]
            playlist.append(MediaEntry(media_type=MediaType.MUSIC,
                                       uri=t["uri"],
                                       title=t["name"],
                                       playback=PlaybackType.AUDIO_SERVICE,
                                       image=data["images"][-1]["url"] if data["images"] else "",
                                       skill_id=self.skill_id,
                                       artist=artist["name"],
                                       match_confidence=min(100, score),
                                       length=t["duration_ms"] / 1000,
                                       skill_icon=self.skill_icon))
            if len(playlist) > 25:
                break
        yield playlist

    # multiple decorators are used to execute the search in parallel
    @ocp_search()
    def search_spotify_artist(self, phrase, media_type=MediaType.GENERIC):
        if not self.has_configured_players():
            return []
        base_score = 0
        if self.voc_match(phrase, "Spotify"):
            base_score = 30
        if media_type == MediaType.MUSIC:
            base_score += 15
        phrase = self.remove_voc(phrase, "Spotify")
        for res in self.search_artists(phrase):
            res.match_confidence += base_score
            res.match_confidence = min(100, res.match_confidence)
            yield res

    @ocp_search()
    def search_spotify_album(self, phrase, media_type=MediaType.GENERIC):
        if not self.has_configured_players():
            return []
        base_score = 0
        if self.voc_match(phrase, "Spotify"):
            base_score = 30
        if media_type == MediaType.MUSIC:
            base_score += 15
        phrase = self.remove_voc(phrase, "Spotify")
        try:
            for res in self.search_albums(phrase):
                res.match_confidence += base_score
                res.match_confidence = min(100, res.match_confidence)
                yield res
        except Exception as e:
            LOG.exception(f"Spotify Error: {e}")

    @ocp_search()
    def search_spotify_tracks(self, phrase, media_type=MediaType.GENERIC):
        if not self.has_configured_players():
            return []
        base_score = 0
        if self.voc_match(phrase, "Spotify"):
            base_score = 30
        if media_type == MediaType.MUSIC:
            base_score += 15
        phrase = self.remove_voc(phrase, "Spotify")
        try:
            for res in self.search_tracks(phrase):
                res.match_confidence += base_score
                res.match_confidence = min(100, res.match_confidence)
                yield res
        except Exception as e:
            LOG.exception(f"Spotify Error: {e}")

    @ocp_search()
    def search_spotify_playlists(self, phrase, media_type=MediaType.GENERIC):
        if not self.has_configured_players():
            return []
        base_score = 0
        if self.voc_match(phrase, "Spotify"):
            base_score = 30
        if media_type == MediaType.MUSIC:
            base_score += 15
        phrase = self.remove_voc(phrase, "Spotify")
        try:
            for res in self.search_playlists(phrase):
                res.match_confidence += base_score
                res.match_confidence = min(100, res.match_confidence)
                yield res
        except Exception as e:
            LOG.exception(f"Spotify Error: {e}")


if __name__ == "__main__":
    from ovos_utils.messagebus import FakeBus

    s = SpotifySkill(bus=FakeBus(), skill_id="skill-ovos-spotify.openvoiceos")

    for r in s.search_tracks("the gods made heavy metal"):
        print(r)
        # {'title': 'The Gods Made Heavy Metal', 'duration': 363.893, 'match_confidence': 1.0,
        # 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:2wE7eJkf3IvxKwqiK3UoiO',
        # 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851b2b3dc4550e94bb8d63154e0', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273b2b3dc4550e94bb8d63154e0'}
        break

    for r in s.search_artists("antónio variações"):
        print(r)
        # {'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'playlist': [
        # {'title': 'Canção de engate', 'duration': 253.413, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:6h6Lb92WzzhHXOpZ9GZ0nH', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': '... O corpo é que paga', 'duration': 192.493, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:3XGa0xYj3bmqRf183emc6q', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': 'Estou além', 'duration': 304.773, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:4wuaLybXodnM3oL8ij1Mq1', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': "É p'ra amanhã...", 'duration': 299.986, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:7sRnGu2NjQ9QhaCol6Se8j', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': 'Erva daninha alastrar', 'duration': 226.853, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:7fBPgbrV0H03gYfcVuB2py', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': 'Sempre ausente', 'duration': 348.813, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:5oy0JvT2QquyLJ58R7kdZN', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': 'Anjinho da guarda', 'duration': 293.786, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:6oH64m4fiR2AetZSTjQgL6', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': 'Dar e receber', 'duration': 251.146, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:6hK6SPGR1OAteYhOlxe2SV', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': 'Perdi a memória', 'duration': 252.826, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1iqhganS2IBobi0Jl8mE8A', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'},
        # {'title': 'Povo que lavas no rio', 'duration': 367.88, 'artist': 'António Variações', 'match_confidence': 0.9823529411764705, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1j8HJBjuwSbKqb2clUJJDu', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f'}],
        # 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d00004851ead7ced710473af58baa35d3', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b273ae673fecee9af1ae7844a20f', 'title': 'António Variações (Featured Tracks)'}
        break

    for r in s.search_playlists("heavy metal"):
        print(r)
        # {'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>,  'playlist': [
        # {'title': 'Too Far Gone?', 'duration': 273.693, 'artist': 'Metallica', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:2ZPnedMaS4W1de05Xz18hF', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Panic Attack', 'duration': 325.987, 'artist': 'Judas Priest', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1yEz9RflAoVrjAbCLnEI35', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Nobody', 'duration': 353.986, 'artist': 'Avenged Sevenfold', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:4tjTsxTBcacHcx0AvWERLE', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Night Rider', 'duration': 194.586, 'artist': 'Hellryder', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:0MGLlKw16k1qGlpb69xDZ1', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Wireless', 'duration': 281.38, 'artist': 'Within Temptation', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:671W1lZGS9LcyzkMCRK3ve', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'The Motherload', 'duration': 299.786, 'artist': 'Mastodon', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:6EF0xhfKtQNqUPz2mnE5BD', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Seven Headed Whore', 'duration': 180.173, 'artist': 'Iced Earth', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1hMe8GvsGxd2Z442FDVg5Y', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Blacksong', 'duration': 336.813, 'artist': 'Jorn', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:0cBRw4lIhWudd9hblZ9MlD', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Punishment', 'duration': 284.066, 'artist': 'Biohazard', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:3eI2C0gohXlg4AsavdYSiz', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Meet Your Maker', 'duration': 237.3, 'artist': 'In Flames', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:5qZMqZspIglsy4SJxtJt0S', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Pull Me Under', 'duration': 493.933, 'artist': 'Dream Theater', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:5CPXR6lDTvngxtmMZxnWmC', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'While We Serve', 'duration': 362.5, 'artist': 'Orbit Culture', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:3LmcjJ7e4tlRqwYs2VNRq0', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Blood and Thunder', 'duration': 228.586, 'artist': 'Mastodon', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:3jagGO7eHHuaD53ibehkux', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Halo', 'duration': 195.653, 'artist': 'Soil', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1QixTwDZCcfBzA7QMyont0', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Rebellion (The Clans Are Marching) - Remastered Version', 'duration': 244.76, 'artist': 'Grave Digger', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1P5B3ARqjPkB9yCNPZq9jV', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': "Nobody's Fool", 'duration': 287.4, 'artist': 'Cinderella', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:01Q4wU19hamqnhNjtuvTyI', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'I Want Out', 'duration': 280.213, 'artist': 'Helloween', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:5ObemyuL5dhT67hyz1iWlA', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': "The Dirt I'm Buried In", 'duration': 244.597, 'artist': 'Avatar', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:4OjAxkNVwleUdCUS098eBw', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Mean, Green, Killing Machine', 'duration': 449.786, 'artist': 'Overkill', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:0mIUS9GApGWW8wGxNI0CPE', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'My Will to Live', 'duration': 320.06, 'artist': 'Obituary', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:4sAWFLOCxVfyBvZq9dWy0s', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Tears of a Mandrake', 'duration': 431.693, 'artist': 'Edguy', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:2soaqea9OtrB2gD36zNF6s', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Witch', 'duration': 219.947, 'artist': 'Islander', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:5MSdmihYEiWhtegR9Ji6mA', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Blow Your Trumpets Gabriel', 'duration': 265.44, 'artist': 'Behemoth', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:07zQA8UkyxpmnRhPjHOVOB', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Master of Confusion', 'duration': 295.28, 'artist': 'Gamma Ray', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:5r1pLWSHgVAwmoDcSJ4ogL', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'},
        # {'title': 'Armed To The Teeth', 'duration': 266.014, 'artist': 'Annihilator', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:4MbJlBkNTEXGJ0sL8U5ABN', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e'}],
        # 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'bg_image': 'https://i.scdn.co/image/ab67706f00000003b1b4e9154f2606ba5af3c68e', 'title': 'Heavy Metal'}
        break

    for r in s.search_albums("bad magic"):
        print(r)
        # 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'playlist': [
        # {'title': 'Victory Or Die', 'duration': 188.44, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:2CiA531WXraGbovitMCPM1', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Thunder & Lightning', 'duration': 186.013, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:6qcdpwb8HuELdtekAB4v4c', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Fire Storm Hotel', 'duration': 215.093, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1G2RgfdIIAeyyRjUO5maoA', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Shoot Out All of Your Lights', 'duration': 194.666, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:359H2iOWyP91OjGWK1zORm', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'The Devil', 'duration': 173.493, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:4CuFM8DyCpHKqc0vVCAwKn', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Electricity', 'duration': 136.866, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1KYfhcM7K7Ahs6opoG4cd0', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Evil Eye', 'duration': 140.333, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1jsZ5wEBDUioHIWvOmHP4g', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Teach Them How To Bleed', 'duration': 192.973, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:7dlacZhVi5ZlxyiA4EnOrf', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Till The End', 'duration': 245.08, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:1n89RaAdJ19i3vZHwhcHVu', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Tell Me Who To Kill', 'duration': 177.386, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:4fVXkg9h6WPdMbh4yTz7zC', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Choking On Your Screams', 'duration': 212.533, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:182aPqtajSuosO9o1hxwUA', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'When The Sky Comes Looking For You', 'duration': 178.2, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:7eQC1gaRWWK1bZI4i6ddne', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'},
        # {'title': 'Sympathy For The Devil', 'duration': 324.426, 'artist': 'Motörhead', 'match_confidence': 1.0, 'media_type': <MediaType.MUSIC: 2>, 'uri': 'spotify:track:5Yql4ooghbDqwXIvCGXsdx', 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500'}],
        # 'playback': <PlaybackType.AUDIO_SERVICE: 2>, 'skill_icon': '/home/miro/PycharmProjects/OCP_sprint/skills/skill-ovos-spotify/skill_spotify/res/logo.png', 'skill_id': 'skill-ovos-spotify.openvoiceos', 'image': 'https://i.scdn.co/image/ab67616d0000485135b663121cfffd62c0b83500', 'bg_image': 'https://i.scdn.co/image/ab67616d0000b27335b663121cfffd62c0b83500', 'title': 'Bad Magic (Full Album)'}
        break
