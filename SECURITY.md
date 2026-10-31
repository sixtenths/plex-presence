# Security and privacy

Discord desktop mode uses local IPC to the signed-in desktop client and stores no Discord OAuth tokens. The bundled public application ID is not a credential or server/account identifier. The OAuth behavior described below applies to Always-on mode.

Do not post Plex tokens, Discord OAuth tokens, configuration files, or full authorization URLs in issues. Report a problem without credentials or account data first. For a sensitive vulnerability, use GitHub's private vulnerability reporting if enabled for this repository.

The desktop window uses native Qt widgets and calls the local application service directly. It does not embed a browser or WebView. Provider sign-in takes place in the system browser; passwords are not entered into the app. The authorization callback and optional headless administration endpoint bind only to 127.0.0.1. HTTP requests that modify settings require the expected Origin and a per-process CSRF token. Do not expose it using a reverse proxy or port forwarding to the internet. For a remote machine, use an SSH local tunnel.

Credentials are saved in the operating system's per-user application directory, with restrictive file permissions where supported. They are not encrypted with an OS keychain. Anyone who can read these files as your user can access the linked accounts. Keep the directory private and avoid syncing it to public storage.

Discord uses OAuth with PKCE, a public client, and the Social SDK presence scope. No Discord account password, client secret, or raw account token is requested. The presence scope includes additional permissions granted by Discord; review the consent screen before connecting. Disconnecting removes local tokens after clearing presence. Revoke the application in Discord's Authorized Apps settings to revoke its grant remotely.

Plex tokens are sent only to Plex account services and the chosen Plex server. Automatic server selection checks the server identity. An explicitly entered HTTP server URL sends its token without TLS on that network; use HTTPS or a trusted local network. Shared-server mode uses only the selected account/profile token to receive the server’s playback notifications; it does not request the owner-only session list. Plex enforces the token’s access scope. Explicit foreign user IDs in notifications are ignored. Home switching requires Plex authorization and the profile PIN where configured. PINs are used only for switching and are not saved; the initial login token is retained privately for future Home switches.

**Use your Plex artwork** is enabled by default for new installations and configurations without a saved value. Existing saved choices are preserved, including off. While artwork is enabled, this mode downloads the server's selected poster and uploads that image to Litterbox (or TempFile.org if Litterbox is unavailable) for one hour, then passes the public image URL through images.weserv.nl (`wsrv.nl`) for Discord. Anyone with its URL can view it; Discord and the image proxy may cache it beyond the host's expiry. Plex tokens and server addresses are never sent to these image services. Turn off **Use your Plex artwork** to use public-provider poster URLs without uploading the server's selected image, or disable **Show artwork** entirely to avoid artwork requests. There is no telemetry or central Plex Presence backend.

The no-client Discord publishing endpoint is undocumented and may change or stop working. This project is experimental and cannot guarantee ongoing access or service availability.

## App updates

Update checks are initiated by **Check for updates** in the desktop window. The app requests stable-release metadata and downloads from the official `sixtenths/plex-presence` GitHub repository. These requests do not include Plex or Discord tokens. There are no automatic startup checks or unattended updates, and an update check alone does not authorize installation. The user must choose **Download and install**.

The downloaded package must match the release's published SHA-256 checksum before installation. The helper validates the staged download again and waits for the running app to finish shutting down before replacement. These checks protect download integrity; they are not code signing, notarization, or a guarantee against a compromised release publisher.

Windows self-updates require an installed copy with its uninstaller present and use the project's installer. macOS/Linux updates require a writable app folder and parent directory, and retain the previous app folder as a recovery copy during replacement. Settings must be stored outside the app folder. The restarted app uses the same settings directory and local port, preserving account configuration. Source copies can check versions and open the release page but are not replaced by the updater.

## Windows uninstall

Starting with v0.3.2, confirming uninstall permanently deletes the current user's default Plex Presence data folder, including locally saved Plex/Discord credentials, preferences, and update downloads. The installed app also records custom data directories it uses, so uninstall can remove its known files there without deleting unrelated files. Custom folders used only by an older version must be opened once with v0.3.2 or later to register them. Running instances are asked to quit before cleanup, and a failure stops uninstallation so it can be retried. Cancellation before confirming uninstall does not erase data. Normal installation and self-updates preserve data.
