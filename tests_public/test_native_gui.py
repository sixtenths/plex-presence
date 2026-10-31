from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from plex_presence.guide_content import GuideParser, load_guide

# Qt can abort before Python can catch an error if a display is unavailable.
# Render native widgets in memory for tests, without requiring a desktop session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from plex_presence.native_gui import NativeApp, playback_progress
except ImportError:
    NativeApp = None


class NativeGuideTests(unittest.TestCase):
    def test_bundled_guide_has_all_chapters_and_exact_callback(self):
        chapters = load_guide("http://127.0.0.1:9123")
        self.assertEqual(len(chapters), 12)
        self.assertTrue(all(title and body for title, body in chapters))
        text = "\n".join(body for _title, body in chapters)
        self.assertIn("http://127.0.0.1:9123/callback", text)
        self.assertNotIn("<section", text)
        self.assertIn("TempFile.org", text)

    def test_parser_excludes_surrounding_controls_and_preserves_links(self):
        parser = GuideParser("http://127.0.0.1:8765")
        parser.feed('<button>Ignore me</button><section data-guide-section><h2>A title</h2><p>Read <a href="https://example.org">help</a>.</p></section>')
        self.assertEqual(parser.chapters, [("A title", "Read help (https://example.org).")])


@unittest.skipIf(NativeApp is None, "Qt desktop dependency is not installed")
class NativeWindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = Mock(directory=Path(self.directory.name), origin="http://127.0.0.1:8765", stop=threading.Event(), wake=threading.Event(), show_window=threading.Event())
        self.app = NativeApp(self.service, start_hidden=True)
        self.app.timer.stop()
        self.addCleanup(self.close)

    def close(self):
        self.service.stop.set()
        self.app._pump()
        self.app.window.deleteLater()

    def test_background_window_and_reopen_event(self):
        self.assertFalse(self.service.window_visible)
        self.assertTrue(self.service.desktop_window)
        self.service.show_window.set()
        self.app.last_status_request = float("inf")
        self.app._pump()
        self.assertTrue(self.service.window_visible)
        self.assertFalse(self.service.show_window.is_set())
        self.app.hide()
        self.assertFalse(self.service.window_visible)
        self.assertFalse(self.service.stop.is_set())

    def test_guide_preference_is_separate_from_service_config(self):
        self.app._finish_guide()
        self.assertTrue((Path(self.directory.name) / "native-ui.json").is_file())
        self.service.save.assert_not_called()

    def test_form_actions_use_service_without_http_or_browser(self):
        self.app.username.setText("viewer")
        self.app.flags["enabled"].setChecked(False)
        with patch.object(self.app, "_action") as action, patch("plex_presence.native_gui.webbrowser.open") as browser:
            self.app._save_preferences()
            self.assertEqual(action.call_args.args[0], "/api/settings")
            self.assertEqual(action.call_args.args[1]["plex_username"], "viewer")
            self.assertFalse(action.call_args.args[1]["enabled"])
            browser.assert_not_called()

    def test_status_does_not_overwrite_unsaved_preferences(self):
        self.app.initialized = True
        self.app.username.setText("unsaved-user")
        value = {"settings":{"plex_username":"saved-user", "discord_mode":"desktop"}, "plex_connected":False,
                 "discord_connected":False, "plex":"idle", "discord":{"message":"Waiting"}, "activity":None}
        self.app._status(value)
        self.assertEqual(self.app.username.text(), "unsaved-user")

    def test_discovery_actions_preserve_unsaved_settings(self):
        self.app.settings_fill = False
        self.app.username.setText("unsaved-user")
        with patch.object(self.app, "_submit") as submit:
            self.app._action("/api/plex/users")
            submit.call_args.args[1]({"users":["another-user"]})
        self.assertFalse(self.app.settings_fill)
        self.assertEqual(self.app.username.text(), "unsaved-user")

    def test_failed_poster_fetch_retries_same_url_after_delay(self):
        self.app.initialized = True
        status = {"settings":{}, "plex_connected":False, "discord_connected":False, "plex":"playing",
                  "discord":{"message":"Sharing"}, "activity":{"assets":{"large_image":"https://wsrv.nl/?url=example.org/poster.png"}}}
        with patch.object(self.app, "_submit", return_value=True) as submit, patch("plex_presence.native_gui.time.monotonic", return_value=100):
            self.app._status(status)
            self.assertEqual(submit.call_count, 1)
            self.app._poster(None)
            self.app._status(status)
            self.assertEqual(submit.call_count, 1)
        with patch.object(self.app, "_submit", return_value=True) as submit, patch("plex_presence.native_gui.time.monotonic", return_value=131):
            self.app._status(status)
            self.assertEqual(submit.call_count, 1)

    def test_status_poll_is_submitted_to_worker_and_plain_text_is_preserved(self):
        self.app.last_status_request = 0
        with patch.object(self.app, "_submit", return_value=True) as submit:
            self.app._pump()
        self.assertEqual(submit.call_args.args[0], self.service.status)
        self.assertEqual(submit.call_args.kwargs["kind"], "status")
        self.service.status.assert_not_called()
        self.assertEqual(self.app.media_title.textFormat(), Qt.PlainText)

    def test_first_use_starts_with_welcome_and_guide_remains_native(self):
        self.assertEqual(self.app.stack.currentWidget(), self.app.pages["Overview"])
        self.app.show_guide()
        self.assertEqual(self.app.stack.currentWidget(), self.app.pages["Setup guide"])
        self.assertFalse(self.app.guide_text.openExternalLinks())
        self.assertFalse(self.app.guide_text.openLinks())

    def test_headless_controls_only_appear_in_headless_mode(self):
        self.app.mode.setCurrentIndex(0)
        self.assertTrue(self.app.headless.isHidden())
        self.assertFalse(self.app.desktop_help.isHidden())
        self.app.mode.setCurrentIndex(1)
        self.assertFalse(self.app.headless.isHidden())
        self.assertTrue(self.app.desktop_help.isHidden())

    def test_unrelated_mutation_preserves_preference_draft(self):
        status = {"settings":{"artwork_enabled":False}, "plex_connected":False, "discord_connected":False,
                  "plex":"idle", "discord":{"message":"Waiting"}, "activity":None}
        self.app._status(status)
        self.app.flags["artwork_enabled"].setChecked(True)
        with patch.object(self.app, "_submit") as submit:
            self.app._action("/api/discord/mode")
            submit.call_args.args[1]({"ok":True})
        self.app._status(status)
        self.assertTrue(self.app.flags["artwork_enabled"].isChecked())
        self.assertTrue(self.app.preferences_dirty)

    def test_edit_during_save_is_not_lost(self):
        self.app.flags["enabled"].setChecked(True)
        with patch.object(self.app, "_action") as action:
            self.app._save_preferences()
        self.app.flags["show_progress"].setChecked(True)
        action.call_args.args[2]({"ok":True})
        self.assertTrue(self.app.preferences_dirty)
        self.assertIn("newer changes", self.app.notice.text())

    def test_profile_switch_refreshes_username_without_losing_draft_artwork(self):
        status = {"settings":{"plex_username":"old-user", "artwork_enabled":False},
                  "plex_connected":True, "discord_connected":False, "plex":"idle",
                  "discord":{"message":"Waiting"}, "activity":None}
        self.app._status(status)
        self.app.flags["artwork_enabled"].setChecked(True)
        with patch.object(self.app, "_submit") as submit:
            self.app._action("/api/plex/profile")
            submit.call_args.args[1]({"ok":True})
        status["settings"]["plex_username"] = "new-profile"
        self.app._status(status)
        self.assertEqual(self.app.username.text(), "new-profile")
        self.assertTrue(self.app.flags["artwork_enabled"].isChecked())
        self.assertTrue(self.app.preferences_dirty)
        self.assertFalse(self.app.refresh_profile_username)

    def test_progress_requires_valid_real_timeline(self):
        self.assertIsNone(playback_progress({}))
        self.assertIsNone(playback_progress({"timestamps":{"start":100,"end":90}}, now=120))
        self.assertEqual(playback_progress({"timestamps":{"start":100,"end":300}}, now=150), (50, 200))
        self.assertEqual(playback_progress({"timestamps":{"start":1700000000000,"end":1700000060000}}, now=1700000030), (30, 60))

    def test_selected_posters_disclose_public_upload(self):
        self.app.flags["use_plex_artwork"].setChecked(True)
        self.assertFalse(self.app.artwork_disclosure.isHidden())
        self.assertIn("Images are public", self.app.artwork_disclosure.text())

    def test_disconnected_actions_are_contextual(self):
        status = {"settings":{}, "plex_connected":False, "discord_connected":False,
                  "plex":"idle", "discord":{"message":"Waiting"}, "activity":None}
        self.app._status(status)
        self.assertTrue(self.app.plex_disconnect_button.isHidden())
        self.assertTrue(self.app.plex_check_button.isHidden())
        self.assertFalse(self.app.select_server_button.isEnabled())
        status.update(plex_connected=True, plex_signin_pending=True)
        self.app._status(status)
        self.assertFalse(self.app.plex_disconnect_button.isHidden())
        self.assertFalse(self.app.plex_check_button.isHidden())

    def test_server_refresh_restores_saved_selection_without_reconnecting(self):
        servers = [{"id":"one", "name":"First", "owned":True},
                   {"id":"two", "name":"Shared", "owned":False}]
        with patch.object(self.app, "_action") as action:
            self.app._load_servers()
            action.call_args.kwargs["callback"]({"servers":servers, "selected_server_id":"two"})
        self.assertEqual(self.app.server_combo.currentData(), "two")
        self.assertEqual(action.call_count, 1)
        self.assertEqual(action.call_args.args[0], "/api/plex/servers")

    def test_server_refresh_connects_the_only_available_server(self):
        server = {"id":"shared", "name":"Shared cinema", "owned":False}
        with patch.object(self.app, "_action") as action:
            self.app._load_servers()
            action.call_args.kwargs["callback"]({"servers":[server], "selected_server_id":None})
        self.assertEqual(self.app.server_combo.currentData(), "shared")
        self.assertEqual(action.call_count, 2)
        self.assertEqual(action.call_args.args, ("/api/plex/select", {"server_id":"shared"}))

    def test_server_refresh_leaves_multiple_servers_for_the_user_to_choose(self):
        servers = [{"id":"one", "name":"Owned", "owned":True},
                   {"id":"two", "name":"Shared", "owned":False}]
        with patch.object(self.app, "_action") as action:
            self.app._load_servers()
            action.call_args.kwargs["callback"]({"servers":servers, "selected_server_id":None})
        self.assertEqual(self.app.server_combo.currentIndex(), -1)
        self.assertIsNone(self.app.server_combo.currentData())
        self.assertEqual(action.call_count, 1)
