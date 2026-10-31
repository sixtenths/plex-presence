# Security and privacy

Discord desktop mode uses local IPC to the signed-in desktop client and stores no Discord OAuth tokens. The bundled public application ID is not a credential or server/account identifier. The OAuth behavior described below applies to Always-on mode.

Do not post Plex tokens, Discord OAuth tokens, configuration files, or full authorization URLs in issues. Report a problem without credentials or account data first. For a sensitive vulnerability, use GitHub's private vulnerability reporting if enabled for this repository.

The GUI binds only to 127.0.0.1. Requests that modify settings require the expected Origin and a per-process CSRF token. Do not expose it using a reverse proxy or port forwarding to the internet. For a remote machine, use an SSH local tunnel.

Credentials are saved in the operating system's per-user application directory, with restrictive file permissions where supported. They are not encrypted with an OS keychain. Anyone who can read these files as your user can access the linked accounts. Keep the directory private and avoid syncing it to public storage.

Discord uses OAuth with PKCE, a public client, and the Social SDK presence scope. No Discord account password, client secret, or raw account token is requested. The presence scope includes additional permissions granted by Discord; review the consent screen before connecting. Disconnecting removes local tokens after clearing presence. Revoke the application in Discord's Authorized Apps settings to revoke its grant remotely.

Plex tokens are sent only to Plex account services and the chosen Plex server. Automatic server selection checks the server identity. An explicitly entered HTTP server URL sends its token without TLS on that network; use HTTPS or a trusted local network. Shared-server mode uses only the selected account/profile token to receive the server’s playback notifications; it does not request the owner-only session list. Plex enforces the token’s access scope. Explicit foreign user IDs in notifications are ignored. Home switching requires Plex authorization and the profile PIN where configured. PINs are used only for switching and are not saved; the initial login token is retained privately for future Home switches.

Default artwork sends a public poster URL to images.weserv.nl (`wsrv.nl`) and Discord. It does not upload Plex files. The optional **Use my selected Plex posters** setting instead downloads the server's selected poster and uploads that image to Litterbox for one hour. Anyone with its URL can view it; Discord and the image proxy may cache it beyond the host's expiry. Plex tokens and server addresses are never sent to either image service. Turn this setting off to avoid uploading selected artwork, or disable artwork entirely to avoid the image proxy. There is no telemetry or central Plex Presence backend.

The no-client Discord publishing endpoint is undocumented and may change or stop working. This project is experimental and cannot guarantee ongoing access or service availability.
