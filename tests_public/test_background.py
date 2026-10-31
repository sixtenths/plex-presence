import sys
import unittest
from unittest.mock import Mock, patch
from plex_presence.__main__ import main
from plex_presence.tray import start_tray


class BackgroundTests(unittest.TestCase):
    def test_shortcut_reopens_running_gui(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"app_id":"plex-presence"}'
        with patch.object(sys, "argv", ["plex-presence"]), patch("plex_presence.__main__.InstanceLock", side_effect=OSError), patch("plex_presence.__main__.urlopen", return_value=response), patch("plex_presence.__main__.webbrowser.open") as browser:
            self.assertEqual(main(), 0)
            browser.assert_called_once_with("http://127.0.0.1:8765")

    def test_background_start_never_opens_browser_on_conflict(self):
        with patch.object(sys, "argv", ["plex-presence", "--no-browser"]), patch("plex_presence.__main__.InstanceLock", side_effect=OSError), patch("plex_presence.__main__.webbrowser.open") as browser:
            self.assertEqual(main(), 1)
            browser.assert_not_called()

    def test_windows_tray_controls(self):
        fake = Mock()
        service = Mock(origin="http://127.0.0.1:8765")
        with patch("plex_presence.tray.sys.platform", "win32"), patch.dict(sys.modules, {"pystray":fake}), patch("plex_presence.tray.threading.Thread"), patch("plex_presence.tray.webbrowser.open") as browser:
            # Pillow is only a runtime dependency on Windows; skip this UI-specific test elsewhere.
            try:
                import PIL
            except ImportError:
                self.skipTest("Windows tray requires Pillow")
            icon = start_tray(service)
            self.assertIsNotNone(icon)
            items = fake.MenuItem.call_args_list
            items[0].args[1]()
            browser.assert_called_once_with(service.origin)
            items[1].args[1]()
            service.stop.set.assert_called_once()
            service.wake.set.assert_called_once()
