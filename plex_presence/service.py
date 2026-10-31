"""Application service: personal configuration, polling and account linking."""
import copy
import secrets
import threading
import time
from pathlib import Path
from .artwork import ArtworkResolver
from .selected_artwork import SelectedArtworkResolver
from .discord_presence import DiscordPresence
from .oauth import DiscordAuth
from .plex import PlexWatcher, PlexError
from .plex_account import PlexAccount, truth
from .notifications import NotificationWatcher
from .playback import activity_finished
from .storage import read_json, save_private


class Service:
    def __init__(self, directory, port=8765):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.directory / "config.json"
        self.token_path = self.directory / "discord-tokens.json"
        self.config = read_json(self.config_path)
        for key in ("enabled", "artwork_enabled", "show_progress"):
            self.config.setdefault(key, True)
        self.config.setdefault("use_plex_artwork", False)
        self.config.setdefault("plex_client_identifier", secrets.token_hex(16))
        save_private(self.config_path, self.config)
        self.port = port
        self.origin = f"http://127.0.0.1:{port}"
        self.csrf = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.generation = 0
        self.account = PlexAccount(self.config["plex_client_identifier"])
        self.oauth = DiscordAuth(self.origin + "/callback")
        self.resources = {}
        self.home_profiles = {}
        self.auth_error = ""
        self.publisher = self.watcher = self.artwork = None
        self.snapshot = {"plex": "setup_required", "discord": {"state": "not_connected", "message": "Connect Discord to start."}, "activity": None}
        self.reconfigure()

    def reconfigure(self):
        self.generation += 1
        if isinstance(self.watcher, NotificationWatcher):
            self.watcher.close()
        self.watcher = self.artwork = None
        if all(self.config.get(k) for k in ("plex_url", "plex_token", "plex_username")):
            watcher = NotificationWatcher if self.config.get("plex_source") == "notifications" else PlexWatcher
            self.watcher = watcher(dict(self.config, plex_timeout=4, poll_interval=5))
            self.artwork = (SelectedArtworkResolver if self.config.get("use_plex_artwork") else ArtworkResolver)(self.config)
        if self.config.get("discord_client_id") and self.token_path.exists():
            self.publisher = DiscordPresence(self.config, self.token_path)
        self.wake.set()

    def status(self):
        with self.lock:
            return dict(copy.deepcopy(self.snapshot), csrf=self.csrf, redirect_uri=self.oauth.redirect_uri,
                plex_connected=bool(self.config.get("plex_account_token") or self.config.get("plex_token")),
                discord_connected=self.token_path.exists(), plex_signin_pending=bool(self.account.pending),
                settings={k: self.config.get(k, "") for k in ("server_name", "plex_url", "plex_username", "discord_client_id",
                                                              "enabled", "artwork_enabled", "show_progress", "use_plex_artwork",
                                                              "plex_profile_name", "plex_source")},
                auth_error=self.auth_error, experimental=True)

    def save(self):
        save_private(self.config_path, self.config)
        self.reconfigure()

    def clear_presence(self):
        if self.publisher:
            self.snapshot["discord"] = self.publisher.sync(None)
        self.snapshot["activity"] = None

    def action(self, path, data):
        with self.lock:
            if path == "/api/plex/connect":
                return {"url": self.account.begin(self.origin)}
            if path == "/api/plex/poll":
                account = self.account.poll()
                if not account:
                    return {"connected": False}
                self.clear_presence()
                for key in ("plex_url", "plex_token", "server_name", "server_id", "plex_user_id", "plex_source", "plex_server_owned"):
                    self.config.pop(key, None)
                self.config.update(plex_account_token=account["token"], plex_root_token=account["token"],
                    plex_account_user_id=account.get("id", ""), plex_profile_name=account["username"], plex_username=account["username"])
                self.resources = {}
                self.home_profiles = {}
                self.save()
                return {"connected": True}
            if path == "/api/plex/home":
                token = self.config.get("plex_root_token") or self.config.get("plex_account_token")
                if not token:
                    raise ValueError("Sign in to Plex first.")
                profiles = self.account.home_users(token)
                self.home_profiles = {p["id"]: p for p in profiles}
                return {"profiles": profiles}
            if path == "/api/plex/profile":
                profile = self.home_profiles.get(str(data.get("profile_id", "")))
                if not profile:
                    raise ValueError("Load your Home profiles and choose one.")
                pin = str(data.get("pin", ""))
                if profile["protected"] and not pin:
                    raise ValueError("This profile requires its PIN.")
                root = self.config.get("plex_root_token") or self.config.get("plex_account_token")
                account = self.account.switch_home(root, profile["id"], pin)
                self.clear_presence()
                for key in ("plex_url", "plex_token", "server_name", "server_id", "plex_user_id", "plex_source", "plex_server_owned"):
                    self.config.pop(key, None)
                self.config.update(plex_root_token=root, plex_account_token=account["token"],
                    plex_account_user_id=account["id"], plex_profile_name=account["username"], plex_username=account["username"])
                self.resources = {}
                self.save()
                return {"ok": True}
            if path == "/api/plex/servers":
                token = self.config.get("plex_account_token")
                if not token:
                    raise ValueError("Sign in to Plex first, or use Advanced setup.")
                values = self.account.resources(token)
                self.resources = {str(r["clientIdentifier"]): r for r in values if r.get("clientIdentifier")}
                return {"servers": [{"id": k, "name": r.get("name", "Plex server"), "owned": truth(r.get("owned"))} for k, r in self.resources.items()]}
            if path == "/api/plex/select":
                resource = self.resources.get(str(data.get("server_id", "")))
                if resource is None:
                    raise ValueError("Refresh the server list and choose a server.")
                values = self.account.choose(resource)
                self.clear_presence()
                self.config.update(values)
                if values.get("plex_source") == "notifications":
                    self.config["plex_username"] = self.config.get("plex_profile_name") or self.config["plex_username"]
                    self.config.pop("plex_user_id", None)
                self.save()
                return {"ok": True}
            if path == "/api/plex/manual":
                username = str(data.get("username", "")).strip()
                if not username or len(username) > 128:
                    raise ValueError("Enter the Plex username to track.")
                values = self.account.test_manual(data.get("url", ""), data.get("token", ""))
                self.clear_presence()
                for key in ("plex_user_id", "server_id", "plex_source", "plex_server_owned"):
                    self.config.pop(key, None)
                self.config.update(values, plex_username=username)
                self.save()
                return {"ok": True}
            if path == "/api/plex/users":
                if not self.watcher:
                    raise ValueError("Choose a server first.")
                return {"users": self.account.users(self.config)}
            if path == "/api/settings":
                username = str(data.get("plex_username", self.config.get("plex_username", ""))).strip()
                if not username or len(username) > 128:
                    raise ValueError("Enter the Plex username to track.")
                if self.config.get("plex_source") == "notifications" and username != self.config.get("plex_username"):
                    raise ValueError("Shared-server mode follows the signed-in profile. Switch Plex Home profile to change users.")
                for key in ("enabled", "artwork_enabled", "show_progress", "use_plex_artwork"):
                    if key in data and not isinstance(data[key], bool):
                        raise ValueError("Invalid setting value.")
                self.clear_presence()
                self.config["plex_username"] = username
                self.config.pop("plex_user_id", None)
                for key in ("enabled", "artwork_enabled", "show_progress", "use_plex_artwork"):
                    if key in data:
                        self.config[key] = data[key]
                self.save()
                return {"ok": True}
            if path == "/api/discord/connect":
                if self.token_path.exists():
                    raise ValueError("Disconnect the current Discord account before linking another.")
                self.auth_error = ""
                return {"url": self.oauth.begin(data.get("client_id", ""))}
            if path == "/api/discord/disconnect":
                self.clear_presence()
                state = self.snapshot["discord"].get("state")
                if self.publisher and state not in ("idle", "not_connected", "needs_login"):
                    raise ValueError("Discord could not clear the current activity. Retry disconnect when the connection recovers.")
                self.token_path.unlink(missing_ok=True)
                self.publisher = None
                self.oauth.pending = None
                self.config.pop("discord_client_id", None)
                self.snapshot["discord"] = {"state": "not_connected", "message": "Discord disconnected."}
                self.save()
                return {"ok": True}
            if path == "/api/plex/disconnect":
                self.clear_presence()
                for key in list(self.config):
                    if (key.startswith("plex_") and key != "plex_client_identifier") or key in ("server_name", "server_id"):
                        self.config.pop(key, None)
                self.resources = {}
                self.home_profiles = {}
                self.account.pending = None
                self.save()
                return {"ok": True}
            if path == "/api/quit":
                self.stop.set()
                self.wake.set()
                return {"ok": True}
            raise ValueError("Unknown action.")

    def finish_oauth(self, query):
        with self.lock:
            try:
                client_id, tokens = self.oauth.finish(query)
                if self.token_path.exists():
                    raise ValueError("A Discord account is already connected. Disconnect it first.")
                save_private(self.token_path, tokens)
                self.config["discord_client_id"] = client_id
                self.auth_error = ""
                self.save()
            except ValueError as error:
                self.auth_error = str(error)

    def tick(self):
        with self.lock:
            generation = self.generation
            watcher, artwork = self.watcher, self.artwork
            enabled = self.config.get("enabled", True)
            use_artwork = self.config.get("artwork_enabled", True)
            show_progress = self.config.get("show_progress", True)
        activity = None
        state = "disabled" if not enabled else "setup_required"
        if watcher and enabled:
            try:
                activity = watcher.poll()
                state = watcher.status
                # Discord can display its own elapsed timer even with no supplied
                # timestamps, so paused playback is deliberately hidden.
                if state != "playing":
                    activity = None
            except PlexError:
                state = "unavailable"
        if activity:
            if use_artwork:
                activity = artwork.enrich(activity)
            else:
                activity.pop("assets", None)
            if activity_finished(activity):
                activity = None
                state = "idle"
            elif not show_progress:
                activity.pop("timestamps", None)
        with self.lock:
            if generation != self.generation:
                self.wake.set()
                return
            discord = self.publisher.sync(activity) if self.publisher else {"state": "not_connected", "message": "Connect Discord to start."}
            self.snapshot = {"plex": state, "discord": discord, "activity": activity, "checked_at": int(time.time())}

    def run(self):
        while not self.stop.is_set():
            self.wake.clear()
            try:
                self.tick()
            except Exception:
                with self.lock:
                    self.snapshot["discord"] = {"state": "error", "message": "An update failed. Retrying automatically."}
            self.wake.wait(5)
        with self.lock:
            if isinstance(self.watcher, NotificationWatcher):
                self.watcher.close()
            self.clear_presence()
