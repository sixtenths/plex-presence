"""Fetch one user's Plex playback and format a Discord activity.

Only the Python standard library is required. Plex credentials travel in a
request header and are never included in an activity or error message.
"""

import json
import math
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener
from .network import NoRedirect
import xml.etree.ElementTree as ET


class PlexError(RuntimeError):
    """A safe, user-readable Plex polling failure without response contents."""


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_STATE_ORDER = {"playing": 0, "buffering": 1, "paused": 2}
_MEDIA_TYPES = {"movie", "episode", "track"}


def _object(value):
    """Plex sometimes serializes a single child as a one-element list."""
    if isinstance(value, list):
        value = value[0] if value else None
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) else ""


def _integer(value):
    try:
        if isinstance(value, bool):
            return 0
        return max(0, min(int(value), 2**53 - 1))
    except (TypeError, ValueError, OverflowError):
        return 0


def _label(value):
    """Keep Discord strings bounded and remove control characters."""
    return " ".join(_text(value).split())[:128]


class PlexWatcher:
    """Poll sessions, restricting activity to a configured Plex user.

    When ``plex_user_id`` is supplied it is the authoritative filter, useful
    when Plex exposes a display name instead of the account username.
    Otherwise ``User.title`` must equal ``plex_username`` ignoring case.
    """

    def __init__(self, config):
        raw_url = _text(config.get("plex_url")).strip()
        try:
            parts = urlsplit(raw_url)
            valid = (
                parts.scheme in ("http", "https")
                and parts.hostname
                and parts.port != 0
                and parts.username is None
                and parts.password is None
                and not parts.query
                and not parts.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("plex_url must be an HTTP(S) server URL without credentials, a query, or a fragment") from None
        self._sessions_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") + "/status/sessions", "", "")
        )
        self._token = _text(config.get("plex_token")).strip()
        if not self._token or "\r" in self._token or "\n" in self._token:
            raise ValueError("plex_token must be a nonempty single-line string")
        self._username = _text(config.get("plex_username", "")).strip().casefold()
        user_id = config.get("plex_user_id")
        self._user_id = str(user_id).strip() if user_id is not None else ""
        if not self._username and not self._user_id:
            raise ValueError("plex_username or plex_user_id is required")
        try:
            self.poll_interval = float(config.get("poll_interval", 15))
            self._timeout = float(config.get("plex_timeout", 10))
        except (TypeError, ValueError):
            raise ValueError("Plex poll interval and timeout must be positive numbers") from None
        if not all(math.isfinite(n) and n > 0 for n in (self.poll_interval, self._timeout)):
            raise ValueError("Plex poll interval and timeout must be positive numbers")
        self._selected_session = None
        self._status = "idle"

    @property
    def status(self):
        """Safe playback status; never contains tokens, URLs, or user data."""
        return self._status

    def _fetch(self):
        request = Request(
            self._sessions_url,
            headers={
                "Accept": "application/json",
                "X-Plex-Token": self._token,
                "X-Plex-Product": "Plex Discord Presence",
            },
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=self._timeout) as response:
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if error.code in (401, 403):
                message = "Plex rejected the token or denied access"
            else:
                message = "Plex returned an HTTP error"
            raise PlexError(message) from None
        except (URLError, OSError, HTTPException, ValueError):
            raise PlexError("Plex is unreachable; check that the server is running and reachable") from None
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise PlexError("Plex sessions response exceeded the size limit")
        return payload

    @staticmethod
    def _parse(payload):
        try:
            raw = payload.decode("utf-8-sig").lstrip()
            if raw.startswith("<"):
                if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
                    raise ValueError("Unsupported XML declaration")
                root = ET.fromstring(raw)
                if root.tag != "MediaContainer":
                    raise ValueError("Unexpected XML root")
                sessions = []
                for child in root:
                    if child.tag not in ("Video", "Track"):
                        continue
                    item = dict(child.attrib)
                    for tag in ("User", "Player", "Session"):
                        node = child.find(tag)
                        item[tag] = dict(node.attrib) if node is not None else {}
                    sessions.append(item)
                return sessions
            document = json.loads(raw)
            if not isinstance(document, dict) or not isinstance(document.get("MediaContainer"), dict):
                raise ValueError("Unexpected JSON root")
            container = document["MediaContainer"]
            sessions = container.get("Metadata", [])
            if isinstance(sessions, dict):
                sessions = [sessions]
            if not isinstance(sessions, list):
                raise ValueError("Unexpected session list")
            return [item for item in sessions if isinstance(item, dict)]
        except (UnicodeError, ValueError, TypeError, ET.ParseError):
            raise PlexError("Plex returned an invalid sessions response") from None

    def _matches(self, item):
        user = _object(item.get("User"))
        if self._user_id:
            return str(user.get("id", "")) == self._user_id
        return _text(user.get("username") or user.get("title")).casefold() == self._username

    @staticmethod
    def _session_id(item):
        player = _object(item.get("Player"))
        session = _object(item.get("Session"))
        for source, value in (
            ("session", item.get("sessionKey")),
            ("session-id", session.get("id")),
            ("player", player.get("machineIdentifier")),
        ):
            if value is not None and str(value):
                return source, str(value)
        return "media", str(item.get("ratingKey") or item.get("key") or item.get("title") or "")

    @staticmethod
    def _activity(item, playback_state):
        kind = item["type"]
        title = _label(item.get("title"))
        activity = {"name": title or "Plex", "type": 2 if kind == "track" else 3, "status_display_type": 0}
        poster = item.get("ratingKey")
        if kind == "episode":
            poster = item.get("grandparentRatingKey")
            activity["name"] = _label(item.get("grandparentTitle")) or title or "Plex"
            activity["details"] = _label(item.get("grandparentTitle")) or title or "TV episode"
            season, episode = _integer(item.get("parentIndex")), _integer(item.get("index"))
            number = "S{:02d}E{:02d}".format(season, episode) if item.get("index") is not None else "Episode"
            state = number + (" · " + title if title else "")
        elif kind == "track":
            poster = item.get("parentRatingKey")
            activity["details"] = title or "Music"
            state = _label(item.get("grandparentTitle")) or _label(item.get("originalTitle")) or "Music"
        else:
            activity["details"] = title or "Movie"
            year = _integer(item.get("year"))
            state = "Movie" + (" · " + str(year) if year else "")
        if poster is not None and str(poster).isascii() and str(poster).isdigit():
            activity["assets"] = {"large_image": "plex-poster:" + str(poster)}
        if playback_state != "playing":
            suffix = " · " + playback_state.capitalize()
            activity["state"] = state[:128 - len(suffix)] + suffix
        else:
            activity["state"] = state[:128]
            duration = _integer(item.get("duration"))
            offset = _integer(item.get("viewOffset"))
            if duration:
                offset = min(offset, duration)
            now = int(time.time() * 1000)
            activity["timestamps"] = {"start": now - offset}
            if duration:
                activity["timestamps"]["end"] = now + duration - offset
        return activity

    def poll(self):
        """Return the user's selected activity, None if idle, or raise PlexError."""
        try:
            sessions = self._parse(self._fetch())
        except PlexError:
            self._status = "unavailable"
            raise
        eligible = []
        for item in sessions:
            state = _text(_object(item.get("Player")).get("state")).casefold()
            if self._matches(item) and item.get("type") in _MEDIA_TYPES and state in _STATE_ORDER:
                eligible.append((item, state))
        if not eligible:
            self._selected_session = None
            self._status = "idle"
            return None
        item, state = min(
            eligible,
            key=lambda pair: (
                _STATE_ORDER[pair[1]],
                self._session_id(pair[0]) != self._selected_session,
                self._session_id(pair[0]),
            ),
        )
        self._selected_session = self._session_id(item)
        self._status = state
        return self._activity(item, state)
