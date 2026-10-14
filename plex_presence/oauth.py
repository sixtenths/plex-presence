"""Discord authorization-code flow with PKCE; each install owns its tokens."""
import base64
import hashlib
import hmac
import math
import secrets
import time
from urllib.parse import urlencode
from .network import request_json


class DiscordAuth:
    def __init__(self, redirect_uri):
        self.redirect_uri = redirect_uri
        self.pending = None

    def begin(self, client_id):
        client_id = str(client_id).strip()
        if not client_id.isascii() or not client_id.isdigit() or not 16 <= len(client_id) <= 22:
            raise ValueError("Enter the numeric Application ID from your Discord Developer Portal.")
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        self.pending = {"state": state, "verifier": verifier, "client_id": client_id, "expires": time.monotonic() + 600}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return "https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": client_id, "response_type": "code", "redirect_uri": self.redirect_uri,
            "scope": "openid sdk.social_layer_presence", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "consent"})

    def finish(self, query):
        pending = self.pending
        state = query.get("state", [""])[0]
        if (not pending or pending["expires"] < time.monotonic() or not state.isascii()
                or not hmac.compare_digest(state, pending["state"])):
            raise ValueError("Sign-in expired or the security check failed. Connect Discord again.")
        self.pending = None
        if query.get("error"):
            if query["error"][0] == "invalid_scope":
                raise ValueError("Discord rejected the presence permission. Enable Social SDK for your application, then connect again.")
            raise ValueError("Discord sign-in was not completed. You can try again.")
        code = query.get("code", [""])[0]
        if not code or len(code) > 4096:
            raise ValueError("Discord did not return a valid authorization code.")
        try:
            tokens = request_json("https://discord.com/api/v10/oauth2/token", method="POST", timeout=15,
                data=urlencode({"client_id": pending["client_id"], "grant_type": "authorization_code",
                    "code": code, "redirect_uri": self.redirect_uri, "code_verifier": pending["verifier"]}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
        except (OSError, ValueError):
            raise ValueError("Discord could not exchange the sign-in code. Check Public Client and the exact redirect URL.") from None
        if (not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str)
                or not tokens.get("access_token") or not isinstance(tokens.get("refresh_token"), str) or not tokens.get("refresh_token")
                or "sdk.social_layer_presence" not in str(tokens.get("scope", "")).split()):
            raise ValueError("Discord did not grant the required presence permission.")
        try:
            duration = float(tokens.get("expires_in", 604800))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Discord returned an invalid token expiry.") from None
        tokens["expires_at"] = time.time() + duration
        tokens.pop("id_token", None)
        return pending["client_id"], tokens
