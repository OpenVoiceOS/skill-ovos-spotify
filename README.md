# OVOS Spotify skill

OCP skill for spotify

This skill requires additional setup and components

## Setup

Install and configure the companion plugin [ovos-media-plugin-spotify](https://github.com/OpenVoiceOS/ovos-media-plugin-spotify)

`pip install ovos-media-plugin-spotify`

this skill only handles the voice search, plugin is needed to handle playback of spotify uris provided by this skill

## Oauth

Currently Oauth needs to be performed manually

after installing the plugin run `ovos-spotify-oauth` on the command line and follow the instructions

```
$ ovos-spotify-oauth
This script creates the token information needed for running spotify
        with a set of personal developer credentials.

        It requires the user to go to developer.spotify.com and set up a
        developer account, create an "Application" and make sure to whitelist
        "https://localhost:8888".

        After you have done that enter the information when prompted and follow
        the instructions given.
        
YOUR CLIENT ID: xxxxx
YOUR CLIENT SECRET: xxxxx
Go to the following URL: https://accounts.spotify.com/authorize?client_id=xxx&response_type=code&redirect_uri=https%3A%2F%2Flocalhost%3A8888&scope=user-library-read+streaming+playlist-read-private+user-top-read+user-read-playback-state
Enter the URL you were redirected to: https://localhost:8888/?code=.....
ocp_spotify oauth token saved
```

## Examples 

* "play heavy metal"
* "play motorhead"


## Credits

- [@forslund](https://github.com/forslund)
