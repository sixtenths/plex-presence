"""Playback notifications authenticated as a shared-server user, not its owner."""
import copy
import json
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import websocket
from .network import request_json, server_url
from .plex import PlexWatcher, PlexError, _integer


def connect_notifications(config):
    p = urlsplit(server_url(config["plex_url"]))
    url = urlunsplit(("wss" if p.scheme == "https" else "ws", p.netloc, "/:/websockets/notifications", "", ""))
    # Never forward a user's credentials to an HTTP redirect target.
    connection = websocket.create_connection(url, timeout=4, redirect_limit=0,
        header={"X-Plex-Token": config["plex_token"],
                "X-Plex-Client-Identifier": config.get("plex_client_identifier", "plex-presence")},
        suppress_origin=True, enable_multithread=True)
    if connection.getstatus() != 101:
        connection.close()
        raise OSError("Plex did not accept the notification connection")
    return connection


class NotificationWatcher(PlexWatcher):
    STALE_SECONDS = 75

    def __init__(self, config):
        super().__init__(config)
        self.config = dict(config)
        self.events = {}
        self.metadata = {}
        self.guard = threading.Lock()
        self.closed = threading.Event()
        self.thread = None
        self.socket = None
        self.connected = False
        self.revision = 0

    def accept(self, message):
        if not isinstance(message, dict):
            return
        container = message.get("NotificationContainer", message)
        if not isinstance(container, dict) or container.get("type") != "playing":
            return
        rows = container.get("PlaySessionStateNotification", [])
        if not isinstance(rows, list):
            return
        with self.guard:
            for event in rows:
                if not isinstance(event, dict):
                    continue
                session = str(event.get("sessionKey", ""))
                if not session or len(session) > 128:
                    continue
                # Some servers include an explicit account id as extra evidence.
                user_id = event.get("userID", event.get("userId"))
                expected = self.config.get("plex_account_user_id")
                if user_id is not None and expected and str(user_id) != str(expected):
                    continue
                state = event.get("state")
                if state not in ("playing", "paused", "buffering", "stopped"):
                    continue
                if state == "stopped":
                    self.events.pop(session, None)
                else:
                    key = str(event.get("ratingKey", ""))
                    if not re.fullmatch(r"[0-9]{1,20}", key):
                        continue
                    self.events[session] = (dict(event, ratingKey=key), time.monotonic())
                    if len(self.events) > 32:
                        self.events.pop(next(iter(self.events)))
                self.revision += 1

    def _listen(self):
        while not self.closed.is_set():
            connection = None
            try:
                connection = connect_notifications(self.config)
                with self.guard:
                    if self.closed.is_set():
                        return
                    self.socket = connection
                    self.connected = True
                while not self.closed.is_set():
                    try:
                        raw = connection.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        break
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError("Notification too large")
                    self.accept(json.loads(raw))
            except (OSError, ValueError, websocket.WebSocketException):
                pass  # Credentials and third-party errors must not enter logs.
            finally:
                if connection:
                    connection.close()
                with self.guard:
                    self.connected = False
                    self.socket = None
                    self.events.clear()
                    self.revision += 1
            self.closed.wait(5)

    def close(self):
        self.closed.set()
        with self.guard:
            connection = self.socket
            self.events.clear()
        if connection:
            connection.close()

    def poll(self):
        if self.closed.is_set():
            self._status = "idle"
            return None
        if self.thread is None:
            self.thread = threading.Thread(target=self._listen, name="plex-notifications", daemon=True)
            self.thread.start()
        with self.guard:
            now = time.monotonic()
            self.events = {key: value for key, value in self.events.items() if now - value[1] < self.STALE_SECONDS}
            revision = self.revision
            if not self.connected:
                self._status = "unavailable"
                return None
            playing = [(key, event, seen) for key, (event, seen) in self.events.items() if event["state"] == "playing"]
            if not playing:
                self._status = "paused" if any(e[0]["state"] == "paused" for e in self.events.values()) else "idle"
                return None
            session, event, seen = min(playing, key=lambda x: (x[0] != self._selected_session, x[0]))
        key = event["ratingKey"]
        try:
            if key not in self.metadata:
                result = request_json(self.config["plex_url"] + "/library/metadata/" + key,
                                      headers={"X-Plex-Token": self.config["plex_token"]}, timeout=4)
                rows = result["MediaContainer"]["Metadata"]
                item = rows[0] if isinstance(rows, list) else rows
                if not isinstance(item, dict) or item.get("type") not in ("movie", "episode", "track"):
                    raise ValueError("Unsupported media")
                if len(self.metadata) >= 32:
                    self.metadata.pop(next(iter(self.metadata)))
                self.metadata[key] = item
            item = copy.deepcopy(self.metadata[key])
            item["viewOffset"] = _integer(event.get("viewOffset")) + max(0, int((time.monotonic() - seen) * 1000))
            duration = _integer(item.get("duration"))
            if duration and item["viewOffset"] >= duration - 250:
                self._status = "idle"
                return None
            activity = self._activity(item, "playing")
            if item.get("type") == "episode":
                label = self._anime.label(item.get("grandparentRatingKey"), item.get("parentIndex"), item.get("index"))
                if label:
                    activity["state"] = (label + " · " + str(item.get("title", "")))[:128]
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            self._status = "unavailable"
            raise PlexError("Could not read this user's playback metadata") from None
        with self.guard:
            if revision != self.revision or not self.connected or self.closed.is_set():
                self._status = "idle"
                return None
        self._selected_session = session
        self._status = "playing"
        return activity
