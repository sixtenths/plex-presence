"""Anime episode labels in Plex's regular-season order; specials stay separate."""
from collections import OrderedDict
import copy
import json
import re
import time
from http.client import HTTPException
from urllib.request import Request, HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OSError("Metadata redirects are not allowed")


def number(value):
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


class AnimeEpisodes:
    def __init__(self, config):
        self.base = str(config.get("plex_url", "")).rstrip("/")
        self.token = config.get("plex_token", "")
        self.cache = OrderedDict()

    def _get(self, path):
        request = Request(self.base + path, headers={"Accept": "application/json", "X-Plex-Token": self.token})
        with build_opener(NoRedirect()).open(request, timeout=4) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Metadata too large")
        container = json.loads(raw)["MediaContainer"]
        rows = container.get("Metadata", [])
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ValueError("Invalid metadata")
        if number(container.get("totalSize")) > len(rows):
            raise ValueError("Incomplete episode list")
        return rows

    def _show(self, show_id):
        now = time.monotonic()
        cached = self.cache.get(show_id)
        if cached and cached[0] > now:
            return cached[1]
        result = None
        ttl = 60
        try:
            rows = self._get("/library/metadata/" + show_id)
            show = rows[0] if rows else {}
            tags = show.get("Genre", []) + (show.get("Label") or [])
            anime = any(str(tag.get("tag", "")).strip().casefold() == "anime" for tag in tags)
            if anime:
                episodes = self._get("/library/metadata/" + show_id + "/allLeaves")
                counts, explicit, indices = {}, {}, {}
                for item in episodes:
                    season, index = number(item.get("parentIndex")), number(item.get("index"))
                    if season and index:
                        counts[season] = max(counts.get(season, 0), index)
                        indices.setdefault(season, set()).add(index)
                        absolute = number(item.get("absoluteIndex"))
                        if absolute:
                            explicit[(season, index)] = absolute
                later = [(s, e) for s, values in indices.items() if s > 1 for e in values]
                continuing = sum(e > max((v for k, v in counts.items() if k < s), default=0) for s, e in later)
                already_absolute = 1 in counts and bool(later) and continuing / len(later) >= 0.8
                result = (counts, explicit, already_absolute)
            ttl = 3600
        except (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError):
            pass  # Keep the original label if metadata cannot be verified.
        self.cache[show_id] = (now + ttl, result)
        self.cache.move_to_end(show_id)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return result

    def label(self, show_id, season, episode):
        show_id = str(show_id or "")
        if not re.fullmatch(r"[0-9]{1,20}", show_id):
            return None
        season, episode = number(season), number(episode)
        if not season or not episode:
            return None
        data = self._show(show_id)
        if data is None:
            return None
        counts, explicit, already_absolute = data
        absolute = explicit.get((season, episode))
        if not absolute and already_absolute:
            absolute = episode
        if not absolute:
            # Missing whole seasons cannot safely be treated as zero episodes.
            if season > 1000 or any(s not in counts for s in range(1, season)):
                return None
            absolute = sum(counts[s] for s in range(1, season)) + episode
        return f"Episode {absolute}"

    def enrich(self, activity):
        """Adapt DRPP's normal episode template using its show-poster marker."""
        marker = activity.get("assets", {}).get("large_image", "")
        show = re.fullmatch(r"plex-poster:([0-9]{1,20})", marker)
        state = re.fullmatch(r"S([0-9]+)E([0-9]+)(.*)", activity.get("state", ""))
        if not show or not state:
            return activity
        label = self.label(show[1], state[1], state[2])
        if not label:
            return activity
        result = copy.deepcopy(activity)
        result["state"] = (label + state[3])[:128]
        return result
