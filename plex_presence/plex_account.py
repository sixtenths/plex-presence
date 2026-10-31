"""Plex PIN sign-in and server discovery. Tokens never enter browser responses."""
import time
import json
import re
import xml.etree.ElementTree as ET
from urllib.request import Request, build_opener
from urllib.parse import urlencode
from .network import request_json, server_url, NoRedirect
from .plex import PlexWatcher
from .notifications import connect_notifications
from websocket import WebSocketException


def truth(value):
    return value is True or str(value).lower() in ("1", "true")


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
        return {"token": token, "username": name, "id": str(account.get("id", ""))}

    def _home_request(self, token, path, pin=None):
        url = "https://plex.tv/api/home/users" + path
        if pin:
            url += "?" + urlencode({"pin": pin})
        req = Request(url, headers=self.headers(token), data=b"" if pin is not None else None,
                      method="POST" if pin is not None else "GET")
        try:
            with build_opener(NoRedirect()).open(req, timeout=6) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ValueError()
            return ET.fromstring(raw)
        except (OSError, ValueError, ET.ParseError):
            raise ValueError("Plex Home access failed. Check your account and profile PIN, then retry.") from None

    def home_users(self, token):
        root = self._home_request(token, "")
        return [{"id": str(row.get("id", "")), "name": row.get("title") or row.get("username") or "Plex profile",
                 "protected": truth(row.get("protected"))} for row in root.findall("User")
                if re.fullmatch(r"[0-9]+", row.get("id", ""))]

    def switch_home(self, token, user_id, pin):
        if not re.fullmatch(r"[0-9]{1,20}", str(user_id)):
            raise ValueError("Choose a Plex Home profile.")
        if pin and not re.fullmatch(r"[0-9]{4}", str(pin)):
            raise ValueError("Enter the profile's four-digit PIN.")
        root = self._home_request(token, "/" + str(user_id) + "/switch", str(pin or ""))
        switched = root.get("authenticationToken")
        if not switched:
            raise ValueError("Plex did not authorize this Home profile.")
        account = request_json("https://plex.tv/api/v2/user", headers=self.headers(switched))
        if str(account.get("id", "")) != str(user_id):
            raise ValueError("Plex returned a different profile. Sign in again.")
        return {"token": switched, "username": account.get("username") or account.get("title"), "id": str(user_id)}

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
                owned = truth(resource.get("owned"))
                if owned:
                    request_json(url + "/status/sessions", headers=self.headers(token), timeout=3)
                else:
                    request_json(url + "/library/sections", headers=self.headers(token), timeout=3)
                    connection = connect_notifications({"plex_url": url, "plex_token": token,
                                                        "plex_client_identifier": self.client_id})
                    connection.close()
                return {"plex_url": url, "plex_token": token, "server_name": resource.get("name", "Plex server"),
                        "server_id": resource.get("clientIdentifier", ""), "plex_source": "sessions" if owned else "notifications",
                        "plex_server_owned": owned}
            except (OSError, ValueError, TypeError, WebSocketException):
                continue
        raise ValueError("Cannot connect using this account. Check server access and connectivity. Shared servers must allow your account's playback notification connection.")

    def test_manual(self, url, token):
        url = server_url(url)
        if not isinstance(token, str) or not token.strip() or any(ord(c) < 32 for c in token):
            raise ValueError("Enter a valid Plex token.")
        request_json(url + "/status/sessions", headers=self.headers(token), timeout=4)
        identity = request_json(url + "/", headers=self.headers(token), timeout=4)
        return {"plex_url": url, "plex_token": token, "server_name": identity.get("MediaContainer", {}).get("friendlyName", "Plex server")}

    def users(self, config):
        if config.get("plex_source") == "notifications":
            return [config["plex_username"]]
        result = {str(config.get("plex_username", ""))}
        data = request_json(config["plex_url"] + "/status/sessions", headers=self.headers(config["plex_token"]))
        for item in data.get("MediaContainer", {}).get("Metadata", []):
            user = item.get("User", {})
            name = user.get("username") or user.get("title")
            if name:
                result.add(str(name))
        return sorted(x for x in result if x)
