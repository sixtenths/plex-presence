import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from plex_presence.discord_presence import DiscordPresence, _ApiError


class DiscordPresenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "tokens.json"
        self.path.write_text(json.dumps({"access_token": "SECRET", "refresh_token": "REFRESH",
                                        "expires_at": 9999999999}), encoding="utf-8")
        self.presence = DiscordPresence({"discord_client_id": "12345"}, self.path)
        self.presence._http = Mock(return_value={"token": "SESSION"})

    def test_create_uses_authenticated_app_and_milliseconds(self):
        original = {"details": "Episode", "timestamps": {"end": 1792000000}}
        status = self.presence.sync(original)
        sent = self.presence._http.call_args.args[1]["activities"][0]
        self.assertEqual(status["state"], "active")
        self.assertEqual(sent["application_id"], "12345")
        self.assertEqual(sent["timestamps"]["end"], "1792000000000")
        self.assertEqual(original["timestamps"]["end"], 1792000000)
        self.assertNotIn("SECRET", json.dumps(status))

    def test_unchanged_presence_heartbeats_and_rotates_session(self):
        with patch("plex_presence.discord_presence.time.time", return_value=1000):
            self.presence.sync({"details": "Episode"})
        self.presence._http.reset_mock()
        with patch("plex_presence.discord_presence.time.time", return_value=1010):
            self.presence.sync({"details": "Episode"})
        self.presence._http.assert_not_called()
        self.presence._http.return_value = {"token": "ROTATED"}
        with patch("plex_presence.discord_presence.time.time", return_value=1301):
            self.presence.sync({"details": "Episode"})
        self.assertEqual(self.presence._http.call_args.args[1]["token"], "SESSION")
        self.assertEqual(json.loads(self.path.read_text())["headless_session_token"], "ROTATED")

    def test_clear_after_restart_deletes_saved_session(self):
        self.presence.sync({"details": "Episode"})
        restarted = DiscordPresence({"discord_client_id": "12345"}, self.path)
        restarted._http = Mock(return_value={})
        self.assertEqual(restarted.sync(None)["state"], "idle")
        restarted._http.assert_called_once_with("/users/@me/headless-sessions/delete", {"token": "SESSION"})
        self.assertNotIn("headless_session_token", json.loads(self.path.read_text()))

    def test_pause_removes_timer_resume_restores_it_and_stop_skips_publish_throttle(self):
        playing = {"details": "Movie", "state": "Comedy",
                   "timestamps": {"start": 1792000000000, "end": 1792006000000}}
        paused = {"details": "Movie", "state": "Paused · Comedy"}
        with patch("plex_presence.discord_presence.time.time", return_value=1000):
            self.presence.sync(playing)
        with patch("plex_presence.discord_presence.time.time", return_value=1006):
            self.presence.sync(paused)
        published = self.presence._http.call_args.args[1]["activities"][0]
        self.assertEqual(published["state"], "Paused · Comedy")
        self.assertNotIn("timestamps", published)
        with patch("plex_presence.discord_presence.time.time", return_value=1012):
            self.presence.sync(playing)
        self.assertIn("timestamps", self.presence._http.call_args.args[1]["activities"][0])
        with patch("plex_presence.discord_presence.time.time", return_value=1012.1):
            self.assertEqual(self.presence.sync(None)["state"], "idle")
        self.presence._http.assert_called_with("/users/@me/headless-sessions/delete", {"token": "SESSION"})
        self.assertNotIn("headless_session_token", json.loads(self.path.read_text()))

    def test_refresh_persists_rotated_tokens_before_publishing(self):
        self.path.write_text(json.dumps({"access_token": "OLD", "refresh_token": "REFRESH", "expires_at": 0}))
        self.presence._http.side_effect = [
            {"access_token": "NEW", "refresh_token": "ROTATED", "expires_in": 604800}, {"token": "SESSION"}]
        self.assertEqual(self.presence.sync({"details": "Episode"})["state"], "active")
        self.assertTrue(self.presence._http.call_args_list[0].kwargs["form"])
        self.assertEqual(json.loads(self.path.read_text())["refresh_token"], "ROTATED")

    def test_rate_limit_does_not_drop_session_or_leak_body(self):
        self.presence._http.side_effect = _ApiError(429, {"retry_after": 20, "message": "SECRET"})
        with patch("plex_presence.discord_presence.time.time", return_value=1000):
            status = self.presence.sync({"details": "Episode"})
        with patch("plex_presence.discord_presence.time.time", return_value=1005):
            self.presence.sync({"details": "Episode"})
        self.assertEqual(self.presence._http.call_count, 1)
        self.assertEqual(status["state"], "retrying")
        self.assertNotIn("SECRET", json.dumps(status))

    def test_expired_session_recreates(self):
        self.presence.sync({"details": "Episode"})
        self.presence._last_sent = 0
        self.presence._http.side_effect = [_ApiError(400, {}), {"token": "RECREATED"}]
        self.assertEqual(self.presence.sync({"details": "Other"})["state"], "active")
        self.assertNotIn("token", self.presence._http.call_args.args[1])

    def test_invalid_refresh_needs_login(self):
        self.path.write_text(json.dumps({"access_token": "OLD", "refresh_token": "REFRESH", "expires_at": 0}))
        self.presence._http.side_effect = _ApiError(400, {"error": "invalid_grant"})
        self.assertEqual(self.presence.sync({"details": "Episode"})["state"], "needs_login")

    def test_unauthorized_access_token_refreshes_once(self):
        self.presence._http.side_effect = [
            _ApiError(401, {}),
            {"access_token": "NEW", "refresh_token": "ROTATED", "expires_in": 600},
            {"token": "SESSION"},
        ]
        self.assertEqual(self.presence.sync({"details": "Episode"})["state"], "active")
        self.assertEqual(self.presence._http.call_count, 3)

    def test_failed_delete_keeps_token_for_retry(self):
        self.presence.sync({"details": "Episode"})
        self.presence._http.side_effect = OSError("SECRET failure")
        self.assertEqual(self.presence.sync(None)["state"], "error")
        self.assertEqual(json.loads(self.path.read_text())["headless_session_token"], "SESSION")
        self.assertNotIn("SECRET", json.dumps(self.presence.status))

    def test_application_cache_retry_is_not_success(self):
        self.presence._http.side_effect = _ApiError(202, {"retry_after": 4000})
        with patch("plex_presence.discord_presence.time.time", return_value=1000):
            self.assertEqual(self.presence.sync({"details": "Episode"})["state"], "retrying")
        self.assertEqual(self.presence._retry_at, 1004)
        self.assertNotIn("headless_session_token", json.loads(self.path.read_text()))

    def test_malformed_retry_delay_does_not_crash_worker(self):
        self.presence._http.side_effect = _ApiError(429, {"retry_after": "bad"})
        self.assertEqual(self.presence.sync({"details": "Episode"})["state"], "retrying")


if __name__ == "__main__":
    unittest.main()
