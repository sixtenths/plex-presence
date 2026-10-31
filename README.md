# Plex Presence

Show your Plex playback on your own Discord profile, with a local setup GUI and **no Discord desktop client required**. Runs on Windows, macOS, Linux, and a 64-bit Raspberry Pi.

[Download a release](https://github.com/sixtenths/plex-presence/releases) · [Build status](https://github.com/sixtenths/plex-presence/actions) · [Security and privacy](SECURITY.md)

![Local setup GUI](docs/setup.png)

**Experimental:** Discord's OAuth permission is documented, but the headless presence publishing endpoint is not a stable public API. Discord may change or disable it. Each person must create their own Discord application and enable Social SDK. This independent community project is inspired by [phin05/discord-rich-presence-plex](https://github.com/phin05/discord-rich-presence-plex).

## Features

- Sign in to Plex and choose your own server, or enter an address and token in Advanced setup.
- Select the Plex username to track and authorize your own Discord account.
- Show movies, episodes, and music, with optional public artwork and playback progress.
- Shows tagged **Anime** in Plex genres or labels use **Episode 42** instead of **S03E01**. Numbering follows Plex's regular-season order, excludes season-zero specials, and uses an explicit absolute index when available. It also detects shows whose episode indices already continue across seasons, avoiding double counting. Otherwise it adds the highest episode index in each earlier season; incomplete libraries or alternative ordering can differ from an external anime database. Unavailable metadata or missing whole earlier seasons retains the standard season/episode label.
- **Clear presence on pause, stop, buffering, or loss of server access.** Resume restores it. No paused activity or paused timer.
- Save settings locally and refresh Discord authorization automatically.
- Run on an always-on machine without leaving Discord open. The Plex server must still be running: a Pi cannot see playback from a powered-off PC.

Playback is polled roughly every five seconds. Network latency, Plex session reporting, Discord caching, and rate limits can delay updates. Disabling progress removes the supplied playback timestamps; Discord may still display its own activity duration.

## Download and run

Open [Releases](https://github.com/sixtenths/plex-presence/releases), choose the archive for your machine, and extract the **whole folder** before launching it. Downloads include Python.

| Platform | Download | Launch |
| --- | --- | --- |
| Windows x64 | `PlexPresence-windows-x64.zip` | `PlexPresence.exe` |
| macOS Apple Silicon | `PlexPresence-macos-arm64.zip` | `PlexPresence.app` |
| macOS Intel | `PlexPresence-macos-x64.zip` | `PlexPresence.app` |
| Linux / Raspberry Pi arm64 | `PlexPresence-linux-arm64.tar.gz` | `./PlexPresence/PlexPresence` |
| Linux x64 | `PlexPresence-linux-x64.tar.gz` | `./PlexPresence/PlexPresence` |

Builds are unsigned and macOS builds are not notarized. Windows SmartScreen or macOS Gatekeeper may require approval using the OS's normal per-app controls. Use only downloads from this repository. Running from source is also supported.

Native builds are tested on the current Windows GitHub runner, macOS 15 (both architectures), and Ubuntu 22.04 (both architectures). Older operating systems are not guaranteed. Linux binaries require glibc 2.35 or newer; Alpine/musl is unsupported. For older compatible Linux systems, use Python 3.11+ from source. No 32-bit Pi or Windows ARM native build is supplied.

The app opens `http://127.0.0.1:8765` in your default browser. Closing the browser leaves the watcher running. Use **Quit app** in the GUI to stop it and clear presence. Launch it again to resume with saved settings. Automatic launch at login is not configured for you.

## First-time setup

### 1. Plex

Click **Connect Plex**, sign in on Plex's website, and approve the connection. Return to the GUI, refresh the server list if needed, choose a server, and click **Use server**. The app verifies access to its playback sessions.

Enter the Plex username to track. **Find active users** suggests names from current playback; start a stream if a name is missing, or type the exact username yourself. A shared server can appear in discovery without granting permission to read sessions. The app cannot bypass Plex permissions.

Advanced setup accepts your server URL, token, and username. Prefer HTTPS. Local HTTP sends the token unencrypted on your network. Never put a token in the URL or a public issue.

### 2. Discord

1. Open the [Developer Portal](https://discord.com/developers/applications) and create your own application, for example **Plex**. Its name can appear on your profile.
2. Under **Social SDK → Getting Started**, complete the activation form with your own details and enable Social SDK. Otherwise authorization can fail with `invalid_scope`.
3. Under **OAuth2**, enable **Public Client** and save.
4. Add the exact redirect URL displayed in the GUI: normally `http://127.0.0.1:8765/callback`. Save it.
5. Copy the **Application ID** into the GUI. No client secret or bot token is needed.
6. Review the permission note and click **Connect Discord**. Check the signed-in Discord account and authorize the app.

Scopes are `openid sdk.social_layer_presence`, using PKCE. Discord bundles profile, social/friend, invite, and activity permissions into the presence scope; review the actual consent screen. This app uses authorization refresh and presence publishing. See [Discord's scope documentation](https://docs.discord.com/developers/discord-social-sdk/core-concepts/oauth2-scopes).

Every install has separate credentials. Nothing connects to the repository owner's Plex server, Discord app, or account. To switch Discord accounts, disconnect first and reconnect using the intended account in your browser. Disconnect removes local tokens; revoke the grant remotely in Discord's **Authorized Apps** settings.

### 3. Playback

Save your preferences and start Plex playback. The GUI shows the Plex state and whether Discord accepted an update. Pause or stop to clear it. Public posters are fitted inside a square canvas to preserve proportions; Discord controls its card layout.

## Run from source

Requires Python 3.11+ and Git. Runtime code uses the Python standard library.

```sh
git clone https://github.com/sixtenths/plex-presence.git
cd plex-presence
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe -m plex_presence
```

macOS / Linux (use `python3` for the initial venv command if needed):

```sh
.venv/bin/python -m pip install .
.venv/bin/python -m plex_presence
```

Alternatively, run `python run.py` directly from the checkout.

## Pi / headless setup

Start the native executable, or the Python entry point, with `--no-browser`:

```sh
python3 -m plex_presence --no-browser
```

From your desktop, forward the setup port over SSH using your own username and machine:

```sh
ssh -N -L 8765:127.0.0.1:8765 user@your-server
```

Open `http://127.0.0.1:8765` in your desktop browser. Keep the tunnel connected until Discord redirects back. The watcher must keep running on the remote machine after setup. For persistent startup, run the same command under your preferred service manager as your regular user.

Do not expose the setup port to the internet. If 8765 is busy, use `--port 8790`, forward 8790 at both ends, and save the new redirect URL in your Discord application. `--data-dir PATH` selects another configuration directory. Only one instance can use a given directory.

## Privacy and local files

| OS | Default data directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\PlexPresence` |
| macOS | `~/Library/Application Support/PlexPresence` |
| Linux | `$XDG_CONFIG_HOME/plex-presence`, or `~/.config/plex-presence` |

Credentials are local plaintext files protected by user/file permissions, not a keychain. Never share them. There is no telemetry or central project backend. Optional artwork sends public poster URLs through [images.weserv.nl](https://images.weserv.nl/); default mode does not upload Plex artwork. Enable **Use my selected Plex posters** to use the exact poster selected on your server: it uploads that image to Litterbox for one hour, then fits it through the image proxy. Anyone with the image URL can view it, and Discord/proxy caches may last longer. No Plex credentials or server addresses are shared. Disable artwork to avoid the proxy. If no safe public poster exists, presence works without an image.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `invalid_scope` | Enable Social SDK on your Discord app, then reconnect. |
| Code exchange failed | Public Client enabled, exact redirect saved, same port and app ID. |
| Server cannot connect | Reachability, TLS certificate, permission to read sessions. |
| No playback | Server on, correct server and exact Plex username, playback active. |
| Paused activity briefly lingers | Allow a poll and Discord refresh; check GUI retry status. |
| Cannot start | Port or data directory already in use, or file permissions. |
| Missing image | Artwork enabled and a supported public poster exists. |
| Discord asks to reconnect | Disconnect and authorize again; verify Social SDK is enabled. |

## Development

```sh
python -m unittest discover -s tests_public -v
python -m pip install pyinstaller==6.22.3
python packaging/build.py
python tests_public/smoke_bundle.py
```

Build on the target OS and architecture. GitHub Actions runs tests, packages each app, then launches each native bundle to test its GUI API and shutdown. Version tags publish prerelease downloads and SHA-256 checksums only after every native job passes. Tests use mocked account responses and never require live credentials.

## License

[GNU AGPL v3](LICENSE). See [NOTICE.md](NOTICE.md). Not affiliated with Plex or Discord.
