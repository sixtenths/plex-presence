import unittest
from unittest.mock import patch
from plex_presence.playback import activity_finished
from test_plex import CONFIG, movie, payload
from plex_presence.plex import PlexWatcher


class PlaybackEndTests(unittest.TestCase):
    def test_expired_seconds_and_milliseconds(self):
        for end in (1700000000, 1700000000000):
            self.assertTrue(activity_finished({"timestamps": {"end": end}}, 1700000001))

    def test_last_millisecond_is_finished_but_real_playback_is_not(self):
        self.assertTrue(activity_finished({"timestamps": {"end": 1700000000001}}, 1700000000))
        self.assertFalse(activity_finished({"timestamps": {"end": 1700000001000}}, 1700000000))
        self.assertFalse(activity_finished({"details": "Live stream"}))

    def test_end_event_clears_and_replay_restores(self):
        watcher = PlexWatcher(CONFIG)
        for offset, active in ((120000, True), (599999, False), (600000, False), (0, True)):
            with patch.object(watcher, "_fetch", return_value=payload(movie(viewOffset=offset))):
                self.assertEqual(watcher.poll() is not None, active)

    def test_completed_session_does_not_hide_another_playing_session(self):
        watcher = PlexWatcher(CONFIG)
        with patch.object(watcher, "_fetch", return_value=payload(movie("1", viewOffset=599999), movie("2", title="Next movie"))):
            self.assertEqual(watcher.poll()["details"], "Next movie")
