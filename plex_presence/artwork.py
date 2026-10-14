"""Resolve local Plex poster markers to existing public provider image URLs.

Only metadata is fetched from Plex. Public posters are fitted inside a square by
wsrv.nl so Discord's square artwork slot cannot crop their edges. The Plex token
is sent as a header to the configured origin, never in an image URL.
"""

from collections import OrderedDict
import copy
from http.client import HTTPException
import json
import re
import time
from urllib.error import URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def _origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("Invalid Plex origin")
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.lower(), port


class _PlexRedirectHandler(HTTPRedirectHandler):
    def __init__(self, origin):
        self.origin = origin

    def redirect_request(self, request, response, code, message, headers, newurl):
        try:
            allowed = _origin(newurl) == self.origin
        except ValueError:
            allowed = False
        if not allowed:
            raise URLError("Plex redirect changed origin")
        return super().redirect_request(request, response, code, message, headers, newurl)


class ArtworkResolver:
    TIMEOUT = 4
    MAX_RESPONSE = 2 * 1024 * 1024
    CACHE_LIMIT = 512
    SUCCESS_TTL = 3600
    FAILURE_TTL = 60
    CDN_HOSTS = frozenset({"image.tmdb.org", "metadata-static.plex.tv", "artworks.thetvdb.com"})
    MARKER = re.compile(r"plex-poster:([0-9]{1,20})\Z")

    def __init__(self, config):
        self._token = config.get("plex_token")
        self._base = str(config.get("plex_url", "")).rstrip("/")
        self._cache = OrderedDict()
        self._opener = None
        try:
            origin = _origin(self._base)
            parsed = urlsplit(self._base)
            if parsed.query or parsed.fragment or "?" in self._base or "#" in self._base:
                raise ValueError("Invalid Plex base URL")
            self._opener = build_opener(_PlexRedirectHandler(origin))
        except ValueError:
            pass  # Missing or invalid Plex configuration only disables artwork.

    @classmethod
    def _public_url(cls, value):
        if not isinstance(value, str) or len(value) > 2048:
            return None
        if any(ord(char) <= 32 for char in value) or "\\" in value or "?" in value or "#" in value:
            return None
        decoded = unquote(value).lower()
        if "x-plex-token" in decoded or "access_token" in decoded or "token=" in decoded:
            return None
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or parsed.hostname not in cls.CDN_HOSTS
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port is not None or not parsed.path or parsed.path == "/"):
                return None
        except ValueError:
            return None
        return value

    @staticmethod
    def _selected(value):
        return value is True or value == 1 or (isinstance(value, str) and value.lower() in ("1", "true"))

    @classmethod
    def _square_url(cls, value):
        # Validate again at the outbound boundary, including cached values. Only
        # existing public provider artwork goes to this image transformation CDN.
        if cls._public_url(value) is None:
            return None
        return "https://wsrv.nl/?" + urlencode({
            "url": value, "w": 256, "h": 256, "fit": "contain",
            "cbg": "00000000", "output": "png",
        })

    def _fetch(self, item_id):
        if self._opener is None or not isinstance(self._token, str) or not self._token:
            return None
        request = Request(self._base + "/library/metadata/" + item_id + "/posters", headers={
            "Accept": "application/json", "X-Plex-Token": self._token,
            "User-Agent": "PlexPresence/0.1",
        })
        with self._opener.open(request, timeout=self.TIMEOUT) as response:
            raw = response.read(self.MAX_RESPONSE + 1)
        if len(raw) > self.MAX_RESPONSE:
            raise ValueError("Plex poster metadata is too large")
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get("MediaContainer"), dict):
            return None
        entries = result["MediaContainer"].get("Metadata", [])
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            return None
        fallback = None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            candidate = next((url for key in ("ratingKey", "thumb", "key")
                              if (url := self._public_url(entry.get(key)))), None)
            if candidate:
                if self._selected(entry.get("selected")):
                    return candidate
                if fallback is None:
                    fallback = candidate
        return fallback

    def _resolve(self, item_id):
        now = time.monotonic()
        cached = self._cache.get(item_id)
        if cached is not None and cached[0] > now:
            self._cache.move_to_end(item_id)
            return cached[1]
        try:
            result = self._fetch(item_id)
        except (OSError, ValueError, TypeError, HTTPException):
            result = None
        ttl = self.SUCCESS_TTL if result else self.FAILURE_TTL
        self._cache[item_id] = (time.monotonic() + ttl, result)
        self._cache.move_to_end(item_id)
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)
        return result

    def enrich(self, activity):
        """Return a copy with poster markers resolved; unavailable images are omitted."""
        if activity is None:
            return None
        value = copy.deepcopy(activity)
        assets = value.get("assets")
        if not isinstance(assets, dict):
            return value
        for size in ("large", "small"):
            key = size + "_image"
            marker = assets.get(key)
            if not isinstance(marker, str) or not marker.startswith("plex-poster"):
                continue
            match = self.MARKER.fullmatch(marker)
            public_url = self._square_url(self._resolve(match.group(1))) if match else None
            if public_url:
                assets[key] = public_url
            else:
                assets.pop(key, None)
                assets.pop(size + "_text", None)
        if not assets:
            value.pop("assets", None)
        return value
