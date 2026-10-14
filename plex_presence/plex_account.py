"""Plex PIN sign-in and server discovery. Tokens never enter browser responses."""
import time
from urllib.parse import urlencode
from .network import request_json, server_url
from .plex import PlexWatcher


class PlexAccount:
    def __init__(self, client_id):
        self.client_id = client_id
        self.pending = None

    def headers(self, token=None):
        value = {"X-Plex-Client-Identifier": self.client_id, "X-Plex-Product": "Plex Presence",
                 "X-Plex-Version": "0.1.0"}
        if token:
            value["X-Plex-Token"] = token
        return value

    def begin(self, origin):
        pin = request_json("https://plex.tv/api/v2/pins?strong=true", headers=self.headers(), data=b"", method="POST")
        if not isinstance(pin, dict) or not pin.get("id") or not pin.get("code"):
            raise ValueError("Plex did not return a sign-in code.")
        self.pending = {"id": str(pin["id"]), "code": str(pin["code"]), "expires": time.monotonic() + 600}
        return "https://app.plex.tv/auth#?" + urlencode({"clientID": self.client_id, "code": pin["code"],
            "context[device][product]": "Plex Presence", "forwardUrl": origin + "/?plex=1"})

    def poll(self):
        pin = self.pending
        if not pin or pin["expires"] < time.monotonic():
            self.pending = None
            raise ValueError("Plex sign-in expired. Connect again.")
        value = request_json("https://plex.tv/api/v2/pins/" + pin["id"] + "?" + urlencode({"code": pin["code"]}),
                             headers=self.headers())
        token = value.get("authToken")
        if not token:
            return None
        account = request_json("https://plex.tv/api/v2/user", headers=self.headers(token))
        name = account.get("username") or account.get("title")
        if not name:
            raise ValueError("Could not read the Plex account name.")
        self.pending = None
        return {"token": token, "username": name}

    def resources(self, token):
        value = request_json("https://plex.tv/api/v2/resources?includeHttps=1&includeRelay=1",
                             headers=self.headers(token))
        if not isinstance(value, list):
            raise ValueError("Plex returned an invalid server list.")
        return [r for r in value if isinstance(r, dict) and "server" in r.get("provides", "").split(",")]

    def choose(self, resource):
        token = resource.get("accessToken")
        if not isinstance(token, str) or not token:
            raise ValueError("This server did not provide access. Ask its owner for access.")
        connections = sorted(resource.get("connections", []),
                             key=lambda x: (not x.get("local", False), x.get("relay", False), x.get("protocol") != "https"))
        for connection in connections:
            try:
                url = server_url(connection.get("uri", ""))
                identity = request_json(url + "/identity", headers=self.headers(token), timeout=3)
                if identity.get("MediaContainer", {}).get("machineIdentifier") != resource.get("clientIdentifier"):
                    continue
                request_json(url + "/status/sessions", headers=self.headers(token), timeout=3)
                return {"plex_url": url, "plex_token": token, "server_name": resource.get("name", "Plex server"),
                        "server_id": resource.get("clientIdentifier", "")}
            except (OSError, ValueError, TypeError):
                continue
        raise ValueError("Cannot reach this server's playback sessions. It must be online and your account must be allowed to read sessions. Try a direct address in Advanced setup.")

    def test_manual(self, url, token):
        url = server_url(url)
        if not isinstance(token, str) or not token.strip() or any(ord(c) < 32 for c in token):
            raise ValueError("Enter a valid Plex token.")
        request_json(url + "/status/sessions", headers=self.headers(token), timeout=4)
        identity = request_json(url + "/", headers=self.headers(token), timeout=4)
        return {"plex_url": url, "plex_token": token, "server_name": identity.get("MediaContainer", {}).get("friendlyName", "Plex server")}

    def users(self, config):
        result = {str(config.get("plex_username", ""))}
        data = request_json(config["plex_url"] + "/status/sessions", headers=self.headers(config["plex_token"]))
        for item in data.get("MediaContainer", {}).get("Metadata", []):
            user = item.get("User", {})
            name = user.get("username") or user.get("title")
            if name:
                result.add(str(name))
        return sorted(x for x in result if x)
