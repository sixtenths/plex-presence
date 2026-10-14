"""Experimental Discord headless presence using an explicitly authorized OAuth app.

The OAuth scope is documented, but headless-sessions is not a public stable API.
No Discord user-account token or desktop client is used. Call sync periodically.
"""

import copy
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from .network import NoRedirect


class _ApiError(Exception):
    def __init__(self, status, data):
        self.status = status
        self.data = data if isinstance(data, dict) else {}
        super().__init__(f"Discord request failed (HTTP {status})")


class DiscordPresence:
    API = "https://discord.com/api/v10"
    HEARTBEAT = 300

    def __init__(self, config, token_path):
        self.config = config
        self.token_path = Path(token_path)
        self._tokens = {}
        self._last_activity = None
        self._last_sent = 0
        self._retry_at = 0
        self._status = {"state": "not_connected", "message": "Connect Discord in setup.", "experimental": True}

    @property
    def status(self):
        return dict(self._status)

    def _state(self, state, message):
        self._status = {"state": state, "message": message, "experimental": True}

    def _load(self):
        if not self.token_path.exists():
            self._tokens = {}
            return
        self._tokens = json.loads(self.token_path.read_text(encoding="utf-8"))
        if not isinstance(self._tokens, dict):
            raise ValueError("Invalid token file")

    def _save(self):
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".discord-", dir=self.token_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._tokens, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.token_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _http(self, path, payload, *, form=False):
        data = urlencode(payload).encode() if form else json.dumps(payload).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
                   "User-Agent": "PlexPresence/0.1"}
        if not form:
            headers["Authorization"] = "Bearer " + self._tokens["access_token"]
        request = Request(self.API + path, data=data, headers=headers, method="POST")
        try:
            with build_opener(NoRedirect).open(request, timeout=15) as response:
                raw = response.read(1024 * 1024)
                result = json.loads(raw) if raw else {}
                if response.status == 202:
                    raise _ApiError(202, result)
                return result
        except HTTPError as error:
            try:
                result = json.loads(error.read(1024 * 1024))
            except (ValueError, OSError):
                result = {}
            raise _ApiError(error.code, result) from None

    def _refresh(self):
        refresh_token = self._tokens.get("refresh_token")
        if not refresh_token:
            raise _ApiError(401, {})
        result = self._http("/oauth2/token", {
            "client_id": str(self.config["discord_client_id"]),
            "grant_type": "refresh_token", "refresh_token": refresh_token,
        }, form=True)
        if not isinstance(result, dict) or not result.get("access_token"):
            raise ValueError("Invalid OAuth response")
        result["expires_at"] = time.time() + float(result.get("expires_in", 604800))
        self._tokens.update(result)
        self._save()  # Refresh tokens rotate; persist before the next request.

    def _post(self, path, body):
        if float(self._tokens.get("expires_at", 0)) <= time.time() + 60:
            self._refresh()
        try:
            return self._http(path, body)
        except _ApiError as error:
            if error.status != 401:
                raise
            self._refresh()
            return self._http(path, body)

    def _activity(self, activity):
        value = copy.deepcopy(activity)
        value["application_id"] = str(self.config["discord_client_id"])
        value.setdefault("name", "Plex")
        value.setdefault("type", 3)
        value["platform"] = "desktop"
        value["supported_platforms"] = ["desktop"]
        for key in ("start", "end"):
            if key in value.get("timestamps", {}):
                stamp = float(value["timestamps"][key])
                value["timestamps"][key] = str(int(stamp * 1000 if stamp < 100_000_000_000 else stamp))
        return value

    def sync(self, activity_or_none):
        """Publish or clear presence. Returns a safe status dict; retries on later calls.

        Tokens use the normal OAuth JSON keys, plus absolute expires_at in seconds.
        Input timestamps may be seconds or milliseconds. Caller serializes sync calls.
        """
        now = time.time()
        if now < self._retry_at:
            return self.status
        try:
            self._load()
            if not self.config.get("discord_client_id") or not self._tokens.get("access_token"):
                self._state("not_connected", "Connect Discord in setup.")
                return self.status
            session = self._tokens.get("headless_session_token")
            if activity_or_none is None:
                if session:
                    try:
                        self._post("/users/@me/headless-sessions/delete", {"token": session})
                    except _ApiError as error:
                        if error.status not in (400, 404):
                            raise
                    self._tokens.pop("headless_session_token", None)
                    self._save()
                self._last_activity = None
                self._last_sent = 0
                self._state("idle", "Connected; waiting for Plex playback.")
                return self.status
            activity = self._activity(activity_or_none)
            if session and activity == self._last_activity and now - self._last_sent < self.HEARTBEAT:
                self._state("active", "Discord presence is active.")
                return self.status
            if self._last_sent and now - self._last_sent < 5:
                return self.status
            body = {"activities": [activity]}
            if session:
                body["token"] = session
            try:
                result = self._post("/users/@me/headless-sessions", body)
            except _ApiError as error:
                if not session or error.status not in (400, 404):
                    raise
                # A saved session may have expired while the service was stopped.
                result = self._post("/users/@me/headless-sessions", {"activities": [activity]})
            if not isinstance(result, dict) or not result.get("token"):
                raise ValueError("Invalid session response")
            self._tokens["headless_session_token"] = result["token"]
            self._save()
            self._last_activity = activity
            self._last_sent = now
            self._state("active", "Discord presence is active.")
        except _ApiError as error:
            self._retry_at = now + 30
            if error.status in (202, 429):
                try:
                    delay = float(error.data.get("retry_after", 4000 if error.status == 202 else 5))
                    if not math.isfinite(delay):
                        raise ValueError("Invalid retry delay")
                except (TypeError, ValueError):
                    delay = 4000 if error.status == 202 else 5
                # Application-cache 202 responses conventionally use milliseconds.
                if error.status == 202:
                    delay /= 1000
                self._retry_at = now + max(1, delay)
                self._state("retrying", "Discord asked us to retry; waiting automatically.")
            elif error.status == 401 or error.data.get("error") in ("invalid_grant", "invalid_client"):
                self._retry_at = now + 60
                self._state("needs_login", "Discord authorization expired or was rejected. Reconnect in setup.")
            elif error.status == 403:
                self._state("needs_setup", "Discord denied presence access. Enable Social SDK and reconnect.")
            else:
                self._state("error", f"Discord request failed (HTTP {error.status}); will retry.")
        except (OSError, URLError, ValueError, KeyError, TypeError):
            self._retry_at = now + 30
            self._state("error", "Could not update Discord; check network and setup. Will retry.")
        return self.status
