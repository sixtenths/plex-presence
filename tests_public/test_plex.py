import io
import json
from http.client import IncompleteRead
from unittest import TestCase, main
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from plex_presence.plex import PlexError, PlexWatcher


CONFIG = {
    "plex_url": "http://192.0.2.10:32400/",
    "plex_token": "test-secret-token",
    "plex_username": "viewer",
}


def movie(session="1", user="viewer", state="playing", **extra):
    return dict({
        "sessionKey": session, "ratingKey": "123", "type": "movie",
        "title": "Arrival", "year": 2016, "duration": 600000,
        "viewOffset": 120000, "User": {"title": user, "id": "7"},
        "Player": {"state": state},
    }, **extra)


def payload(*sessions):
    return json.dumps({"MediaContainer": {"Metadata": list(sessions)}}).encode()


class PlexWatcherTests(TestCase):
    def setUp(self):
        self.watcher = PlexWatcher(CONFIG)
        self.clock = patch("plex_presence.plex.time.time", return_value=1700000000)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def poll_bytes(self, raw, watcher=None):
        with patch("urllib.request.OpenerDirector.open", return_value=io.BytesIO(raw)) as request:
            result = (watcher or self.watcher).poll()
        return result, request

    def test_json_filters_exact_user_and_uses_header_token(self):
        activity, request = self.poll_bytes(payload(
            movie("0", user="someone_else", title="Private movie"),
            movie("1", user="VIEWER"),
            movie("2", user="viewer-extra"),
        ))
        self.assertEqual(activity["details"], "Arrival")
        self.assertEqual(activity["type"], 3)
        self.assertEqual(activity["timestamps"], {
            "start": 1699999880000, "end": 1700000480000,
        })
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, "http://192.0.2.10:32400/status/sessions")
        self.assertEqual(req.get_header("X-plex-token"), CONFIG["plex_token"])
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertNotIn(CONFIG["plex_token"], str(activity))
        self.assertEqual(self.watcher.status, "playing")

    def test_xml_episode_and_user_filter(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <MediaContainer size="2">
          <Video type="movie" title="Other movie" sessionKey="0">
            <User title="Other"/><Player state="playing"/>
          </Video>
          <Video type="episode" title="The Test" grandparentTitle="Example Show"
                 parentIndex="2" index="3" duration="2400000" viewOffset="1000" sessionKey="1">
            <User title="viewer"/><Player state="playing"/>
          </Video>
        </MediaContainer>'''
        activity, _ = self.poll_bytes(xml)
        self.assertEqual(activity["details"], "Example Show")
        self.assertEqual(activity["state"], "S02E03 · The Test")
        self.assertEqual(activity["timestamps"]["end"] - activity["timestamps"]["start"], 2400000)

    def test_music_list_children_and_paused(self):
        track = movie(type="track", title="Example Song", grandparentTitle="Artist",
                      User=[{"title": "viewer"}], Player=[{"state": "paused"}])
        activity, _ = self.poll_bytes(payload(track))
        self.assertEqual(activity["type"], 2)
        self.assertEqual(activity["details"], "Example Song")
        self.assertEqual(activity["state"], "Artist · Paused")
        self.assertNotIn("timestamps", activity)
        self.assertEqual(self.watcher.status, "paused")

    def test_selection_is_stable_and_prefers_active_playback(self):
        activity, _ = self.poll_bytes(payload(movie("2", title="Second")))
        self.assertEqual(activity["details"], "Second")
        activity, _ = self.poll_bytes(payload(movie("1", title="First"), movie("2", title="Second")))
        self.assertEqual(activity["details"], "Second")
        activity, _ = self.poll_bytes(payload(movie("1", title="First"), movie("2", title="Next item", ratingKey="456")))
        self.assertEqual(activity["details"], "Next item")
        activity, _ = self.poll_bytes(payload(movie("1", title="First"), movie("2", state="paused", title="Second")))
        self.assertEqual(activity["details"], "First")
        activity, _ = self.poll_bytes(payload(movie("1", state="paused"), movie("3", state="buffering", title="Buffering")))
        self.assertEqual(activity["details"], "Buffering")
        self.assertNotIn("timestamps", activity)

    def test_paused_and_stopped_clear_timestamps_or_activity(self):
        activity, _ = self.poll_bytes(payload(movie(state="paused")))
        self.assertIn("Paused", activity["state"])
        self.assertNotIn("timestamps", activity)
        activity, _ = self.poll_bytes(payload(movie(state="stopped")))
        self.assertIsNone(activity)
        self.assertEqual(self.watcher.status, "idle")

    def test_progress_is_clamped_and_numeric_strings_supported(self):
        activity, _ = self.poll_bytes(payload(movie(duration="10000", viewOffset="25000")))
        self.assertIsNone(activity)
        activity, _ = self.poll_bytes(payload(movie(duration="10000", viewOffset=-800)))
        self.assertEqual(activity["timestamps"], {"start": 1700000000000, "end": 1700000010000})
        activity, _ = self.poll_bytes(payload(movie(duration=None, viewOffset="invalid")))
        self.assertEqual(activity["timestamps"], {"start": 1700000000000})

    def test_seek_updates_timestamps(self):
        first, _ = self.poll_bytes(payload(movie(viewOffset=1000)))
        later, _ = self.poll_bytes(payload(movie(viewOffset=60000)))
        self.assertEqual(first["timestamps"]["start"] - later["timestamps"]["start"], 59000)

    def test_user_id_is_authoritative_when_configured(self):
        watcher = PlexWatcher(dict(CONFIG, plex_user_id=7))
        activity, _ = self.poll_bytes(payload(movie(user="Display Name")), watcher)
        self.assertEqual(activity["details"], "Arrival")
        activity, _ = self.poll_bytes(payload(movie(User={"title": "viewer", "id": "8"})), watcher)
        self.assertIsNone(activity)

    def test_idle_never_falls_back_to_other_users(self):
        for entries in ([], [movie(user="other")], [movie(User={})]):
            with self.subTest(entries=entries):
                activity, _ = self.poll_bytes(payload(*entries))
                self.assertIsNone(activity)
                self.assertEqual(self.watcher.status, "idle")

    def test_http_and_network_errors_are_sanitized(self):
        secret_url = "http://example.invalid/?X-Plex-Token=" + CONFIG["plex_token"]
        errors = [
            URLError(secret_url),
            IncompleteRead(CONFIG["plex_token"].encode()),
            HTTPError(secret_url, 401, CONFIG["plex_token"], {}, None),
            HTTPError(secret_url, 503, CONFIG["plex_token"], {}, None),
        ]
        for error in errors:
            with self.subTest(error_type=type(error).__name__), patch("urllib.request.OpenerDirector.open", side_effect=error):
                with self.assertRaises(PlexError) as caught:
                    self.watcher.poll()
                self.assertNotIn(CONFIG["plex_token"], str(caught.exception))
                self.assertNotIn(secret_url, str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertEqual(self.watcher.status, "unavailable")

    def test_malformed_responses_are_sanitized(self):
        for raw in (b'bad test-secret-token', b'<broken', b'[]', b'{"MediaContainer": {"Metadata": 2}}'):
            with self.subTest(raw=raw), patch("urllib.request.OpenerDirector.open", return_value=io.BytesIO(raw)):
                with self.assertRaisesRegex(PlexError, "invalid sessions response") as caught:
                    self.watcher.poll()
                self.assertNotIn(CONFIG["plex_token"], str(caught.exception))

    def test_response_limits_and_xml_entities_are_rejected(self):
        raw = b'<!DOCTYPE MediaContainer [<!ENTITY secret "test-secret-token">]><MediaContainer/>'
        with patch("urllib.request.OpenerDirector.open", return_value=io.BytesIO(raw)):
            with self.assertRaisesRegex(PlexError, "invalid sessions response"):
                self.watcher.poll()
        with patch("urllib.request.OpenerDirector.open", return_value=io.BytesIO(b" " * (2 * 1024 * 1024 + 1))):
            with self.assertRaisesRegex(PlexError, "size limit"):
                self.watcher.poll()

    def test_long_paused_label_preserves_playback_state(self):
        activity, _ = self.poll_bytes(payload(movie(type="track", state="paused", grandparentTitle="A" * 200)))
        self.assertLessEqual(len(activity["state"]), 128)
        self.assertTrue(activity["state"].endswith("Paused"))

    def test_config_errors_do_not_echo_credentials(self):
        for url in ("http://user:test-secret-token@example.com", "http://example.com/?token=test-secret-token", "not a url"):
            with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                PlexWatcher(dict(CONFIG, plex_url=url))
            self.assertNotIn(CONFIG["plex_token"], str(caught.exception))


if __name__ == "__main__":
    main()
