"""Bounded requests with no credential forwarding through redirects."""
import json
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OSError("Redirects are not allowed for authenticated requests.")


def server_url(value):
    value = str(value).strip().rstrip("/")
    try:
        p = urlsplit(value)
        valid = p.scheme in ("http", "https") and p.hostname and not (
            p.username or p.password or p.query or p.fragment or p.path)
        valid = valid and (p.port is None or 1 <= p.port <= 65535)
    except ValueError:
        valid = False
    if not valid or any(ord(c) <= 32 for c in value) or "\\" in value:
        raise ValueError("Enter a server address such as http://localhost:32400, without a path or token.")
    return value


def request_json(url, *, headers=None, data=None, method=None, timeout=6):
    req = Request(url, headers=dict(headers or {}, Accept="application/json"), data=data, method=method)
    with build_opener(NoRedirect()).open(req, timeout=timeout) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Response was too large.")
    return json.loads(raw) if raw else {}
