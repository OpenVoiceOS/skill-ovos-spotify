# Ovos Spotify skill

WIP! This is intended to work in tandem with the ovos spotify audio backend

The skill handles searching and interacting with spotify while the backend handles the playback.

## Current Status

- Local auth is still used instead of using the standard OVOS systems
- Only artist queries return the expected format for the audio backend

## Install

1. cd to the ovos-core folder and activate any used venv

2. Install the skill using pip

```
pip install --pre git+https://github.com/forslund/ovos-spotify-skill.git
```

3. Run the spotify-skill authentication

```
python -m skill_spotify.auth
```

4. Configure the spotify-audio-backend

For details see ovos-phal and the ovos backedn for registering oauth for ovos.
