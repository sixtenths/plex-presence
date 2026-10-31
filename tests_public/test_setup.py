import hashlib
import base64
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from plex_presence.__main__ import reopen_running
from plex_presence.oauth import DiscordAuth
from plex_presence.plex_account import PlexAccount
from plex_presence.network import NoRedirect, server_url
from plex_presence.server import make_server
from plex_presence.service import Service
from plex_presence.storage import save_private, read_json
from plex_presence.plex import PlexError


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = DiscordAuth("http://127.0.0.1:8765/callback")

    def test_pkce_and_csrf_are_bound_to_one_use(self):
        url = self.auth.begin("123456789012345678")
        q = parse_qs(urlsplit(url).query)
        p = self.auth.pending
        expected = base64.urlsafe_b64encode(hashlib.sha256(p["verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(q["code_challenge"], [expected])
        self.assertEqual(q["scope"], ["openid sdk.social_layer_presence"])
        token = {"access_token":"TEST_ACCESS", "refresh_token":"TEST_REFRESH", "expires_in":60, "scope":"openid sdk.social_layer_presence"}
        with patch("plex_presence.oauth.request_json", return_value=token):
            client, value = self.auth.finish({"state":[p["state"]], "code":["TEST_CODE"]})
        self.assertEqual(client, "123456789012345678")
        self.assertIn("expires_at", value)
        with self.assertRaises(ValueError):
            self.auth.finish({"state":[p["state"]], "code":["TEST_CODE"]})

    def test_wrong_state_does_not_exchange_code(self):
        self.auth.begin("123456789012345678")
        with patch("plex_presence.oauth.request_json") as request, self.assertRaises(ValueError):
            self.auth.finish({"state":["wrong"], "code":["sensitive"]})
        request.assert_not_called()

    def test_error_descriptions_are_not_exposed(self):
        self.auth.begin("123456789012345678")
        with self.assertRaises(ValueError) as caught:
            self.auth.finish({"state":[self.auth.pending["state"]], "error":["invalid_scope"], "error_description":["secret value"]})
        self.assertIn("Social SDK", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))


class PlexAccountTests(unittest.TestCase):
    def setUp(self):
        self.account = PlexAccount("unique-install-id")

    def test_pin_sign_in_keeps_token_in_backend(self):
        with patch("plex_presence.plex_account.request_json", side_effect=[{"id":12,"code":"TEST_PIN"},
                {"authToken":"TEST_PRIVATE"}, {"username":"viewer"}]) as http:
            url = self.account.begin("http://127.0.0.1:8765")
            value = self.account.poll()
        self.assertIn("app.plex.tv/auth", url)
        self.assertNotIn("TEST_PRIVATE", url)
        self.assertEqual(value, {"token":"TEST_PRIVATE", "username":"viewer", "id":""})
        self.assertIsNone(self.account.pending)
        self.assertEqual(http.call_args.kwargs["headers"]["X-Plex-Token"], "TEST_PRIVATE")

    def test_server_identity_must_match_discovered_resource(self):
        resource = {"owned":True, "clientIdentifier":"correct", "accessToken":"TEST_PRIVATE", "name":"My server", "connections":[{"uri":"http://192.0.2.2:32400"}]}
        with patch("plex_presence.plex_account.request_json", return_value={"MediaContainer":{"machineIdentifier":"wrong"}}):
            with self.assertRaises(ValueError):
                self.account.choose(resource)
        with patch("plex_presence.plex_account.request_json", side_effect=[{"MediaContainer":{"machineIdentifier":"correct"}}, {"MediaContainer":{}}]):
            result = self.account.choose(resource)
        self.assertEqual(result["plex_token"], "TEST_PRIVATE")

    def test_no_redirects_and_unsafe_urls(self):
        with self.assertRaises(OSError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")
        for value in ("file:///tmp/test", "http://user:secret@example.com", "http://example.com?token=secret", "http://example.com/path", "http://example.com:99999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server_url(value)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = Service(self.directory.name)
        blocker = patch("plex_presence.desktop.DesktopPresence._connect", side_effect=OSError("test: Discord offline"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def test_fresh_install_is_empty_and_unique(self):
        status = self.service.status()
        self.assertFalse(status["plex_connected"])
        self.assertFalse(status["discord_connected"])
        self.assertEqual(status["settings"]["discord_client_id"], "")
        self.assertEqual(status["settings"]["plex_url"], "")
        first = self.service.config["plex_client_identifier"]
        self.assertEqual(first, Service(self.directory.name).config["plex_client_identifier"])
        with tempfile.TemporaryDirectory() as other:
            self.assertNotEqual(first, Service(other).config["plex_client_identifier"])

    def test_public_status_never_exposes_tokens(self):
        self.service.config.update(plex_token="PRIVATE_PLEX", plex_account_token="PRIVATE_ACCOUNT")
        save_private(self.service.token_path, {"access_token":"PRIVATE_DISCORD"})
        text = json.dumps(self.service.status())
        for secret in ("PRIVATE_PLEX", "PRIVATE_ACCOUNT", "PRIVATE_DISCORD"):
            self.assertNotIn(secret, text)

    def test_window_show_requires_native_window_and_sets_event(self):
        self.assertFalse(self.service.status()["desktop_window"])
        self.assertFalse(self.service.status()["window_visible"])
        with self.assertRaises(ValueError):
            self.service.action("/api/window/show", {})
        self.assertFalse(self.service.show_window.is_set())
        self.service.desktop_window = True
        self.assertEqual(self.service.action("/api/window/show", {}), {"ok":True})
        self.assertTrue(self.service.show_window.is_set())
        self.assertFalse(self.service.stop.is_set())

    def test_discovery_only_returns_public_labels(self):
        self.service.config["plex_account_token"] = "PRIVATE_ACCOUNT"
        with patch.object(self.service.account, "resources", return_value=[{"clientIdentifier":"server1","name":"Cinema","owned":True,"accessToken":"PRIVATE_SERVER","connections":[{"uri":"http://192.0.2.2"}]}]):
            result = self.service.action("/api/plex/servers", {})
        self.assertEqual(result, {"servers":[{"id":"server1","name":"Cinema","owned":True}]})
        with self.assertRaises(ValueError):
            self.service.action("/api/plex/select", {"server_id":"injected"})

    def test_paused_stopped_and_unreachable_clear_presence(self):
        publisher = Mock()
        publisher.sync.return_value = {"state":"idle"}
        self.service.publisher = publisher
        self.service.artwork = Mock()
        for state in ("paused", "buffering", "idle"):
            self.service.watcher = Mock(status=state)
            self.service.watcher.poll.return_value = {"details":"Movie", "timestamps":{"start":1}}
            self.service.tick()
            publisher.sync.assert_called_with(None)
            self.assertIsNone(self.service.snapshot["activity"])
        self.service.watcher.poll.side_effect = PlexError("unavailable")
        self.service.tick()
        publisher.sync.assert_called_with(None)
        self.service.artwork.enrich.assert_not_called()

    def test_settings_change_during_poll_cannot_publish_old_playback(self):
        self.service.publisher = Mock()
        watcher = Mock(status="playing")
        def poll():
            self.service.generation += 1
            return {"details":"Old playback"}
        watcher.poll.side_effect = poll
        self.service.watcher = watcher
        self.service.config["artwork_enabled"] = False
        self.service.tick()
        self.service.publisher.sync.assert_not_called()

    def test_playing_artwork_and_progress_preferences(self):
        self.service.watcher = Mock(status="playing")
        self.service.watcher.poll.return_value = {"details":"Movie","timestamps":{"start":1},"assets":{"large_image":"plex-poster:5"}}
        self.service.publisher = Mock()
        self.service.publisher.sync.return_value = {"state":"active"}
        self.service.config.update(artwork_enabled=False, show_progress=False)
        self.service.tick()
        self.service.publisher.sync.assert_called_with({"details":"Movie"})

    def test_switch_accounts_requires_disconnect(self):
        self.service.action("/api/discord/mode", {"mode":"headless"})
        save_private(self.service.token_path, {"access_token":"PRIVATE"})
        with self.assertRaises(ValueError):
            self.service.action("/api/discord/connect", {"client_id":"123456789012345678"})
        self.service.action("/api/discord/disconnect", {})
        self.assertFalse(self.service.token_path.exists())


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = Service(self.directory.name, port=0)
        self.server = make_server(self.service)
        self.service.port = self.server.server_port
        self.service.origin = f"http://127.0.0.1:{self.service.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_gui_assets_and_safe_status(self):
        for path in ("/", "/app.js", "/style.css", "/api/status"):
            with urlopen(self.service.origin + path) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_native_auth_return_shows_completion_not_browser_settings(self):
        self.service.desktop_window = True
        for path in ("/?plex=1", "/?discord=1"):
            with self.subTest(path=path):
                self.service.show_window.clear()
                with urlopen(self.service.origin + path) as response:
                    body = response.read()
                    self.assertIn(b"Return to Plex Presence", body)
                    self.assertNotIn(b"Choose your Plex", body)
                    self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
                self.assertTrue(self.service.show_window.is_set())
        self.service.show_window.clear()
        with urlopen(self.service.origin + "/") as response:
            self.assertIn(b"Return to Plex Presence", response.read())
        self.assertFalse(self.service.show_window.is_set())

    def test_discord_callback_routes_to_native_or_headless_completion(self):
        for native in (True, False):
            with self.subTest(native=native), patch.object(self.service, "finish_oauth") as finish:
                self.service.desktop_window = native
                self.service.show_window.clear()
                with urlopen(self.service.origin + "/callback?state=test-state&code=test-code") as response:
                    body = response.read()
                    self.assertEqual(response.url, self.service.origin + "/?discord=1")
                finish.assert_called_once_with({"state":["test-state"], "code":["test-code"]})
                self.assertIn(b"Return to Plex Presence" if native else b"Choose your Plex", body)
                self.assertEqual(self.service.show_window.is_set(), native)

    def test_window_show_endpoint_preserves_csrf_guard(self):
        self.service.desktop_window = True
        headers = {"Content-Type":"application/json", "Origin":self.service.origin, "X-CSRF-Token":"wrong"}
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.service.origin + "/api/window/show", data=b"{}", headers=headers))
        self.assertEqual(caught.exception.code, 403)
        self.assertFalse(self.service.show_window.is_set())
        headers["X-CSRF-Token"] = self.service.csrf
        with urlopen(Request(self.service.origin + "/api/window/show", data=b"{}", headers=headers)) as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertTrue(self.service.show_window.is_set())

    def test_shortcut_reopens_while_presence_service_is_busy(self):
        self.service.desktop_window = True
        with self.service.lock:
            self.assertTrue(reopen_running(self.service.port))
        self.assertTrue(self.service.show_window.is_set())
        self.assertFalse(self.service.stop.is_set())

    def test_host_origin_and_csrf_guards(self):
        for headers in ({"Host":"evil.example"}, {"Origin":"https://evil.example"}, {"X-CSRF-Token":"wrong"}):
            base = {"Content-Type":"application/json", "Origin":self.service.origin, "X-CSRF-Token":self.service.csrf}
            base.update(headers)
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.service.origin + "/api/quit", data=b"{}", headers=base))
            self.assertEqual(caught.exception.code, 403)
        self.assertFalse(self.service.stop.is_set())

    def test_valid_quit_and_bounded_request(self):
        headers = {"Content-Type":"application/json", "Origin":self.service.origin, "X-CSRF-Token":self.service.csrf}
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.service.origin + "/api/quit", data=b" " * 20000, headers=headers))
        self.assertEqual(caught.exception.code, 400)
        with urlopen(Request(self.service.origin + "/api/quit", data=b"{}", headers=headers)) as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertTrue(self.service.stop.is_set())


if __name__ == "__main__":
    unittest.main()
