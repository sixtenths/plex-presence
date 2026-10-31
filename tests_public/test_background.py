import io
import json
import sys
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
from plex_presence.__main__ import main, reopen_running
from plex_presence.tray import start_tray


class BackgroundTests(unittest.TestCase):
    def test_shortcut_requests_existing_native_window_with_csrf(self):
        status = {"app_id":"plex-presence", "desktop_window":True, "csrf":"LOCAL_CSRF"}
        with patch("plex_presence.__main__.urlopen", side_effect=[io.BytesIO(json.dumps(status).encode()), io.BytesIO(b'{"ok":true}')]) as http, patch("webbrowser.open") as browser:
            self.assertTrue(reopen_running(8790))
        self.assertEqual(http.call_args_list[0].args[0], "http://127.0.0.1:8790/api/window/status")
        request = http.call_args_list[1].args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8790/api/window/show")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-csrf-token"), "LOCAL_CSRF")
        self.assertEqual(request.get_header("Origin"), "http://127.0.0.1:8790")
        browser.assert_not_called()

    def test_shortcut_rejects_headless_or_unrelated_server(self):
        for status in ({"app_id":"plex-presence", "desktop_window":False}, {"app_id":"another-app", "desktop_window":True}):
            with self.subTest(status=status), patch("plex_presence.__main__.urlopen", return_value=io.BytesIO(json.dumps(status).encode())) as http:
                self.assertFalse(reopen_running(8765))
                self.assertEqual(http.call_count, 1)

    def test_background_duplicate_does_not_reveal_window(self):
        status = {"app_id":"plex-presence", "desktop_window":True}
        with patch("plex_presence.__main__.urlopen", return_value=io.BytesIO(json.dumps(status).encode())) as http:
            self.assertTrue(reopen_running(8765, show=False))
            self.assertEqual(http.call_count, 1)
        with patch.object(sys, "argv", ["plex-presence", "--background"]), patch("plex_presence.__main__.InstanceLock", side_effect=OSError), patch("plex_presence.__main__.reopen_running", return_value=True) as reopen:
            self.assertEqual(main(), 0)
            reopen.assert_called_once_with(8765, show=False)

    def test_shortcut_launch_reopens_running_native_process(self):
        with patch.object(sys, "argv", ["plex-presence"]), patch("plex_presence.__main__.InstanceLock", side_effect=OSError), patch("plex_presence.__main__.reopen_running", return_value=True) as reopen:
            self.assertEqual(main(), 0)
            reopen.assert_called_once_with(8765, show=True)

    def test_headless_conflict_never_requests_native_window(self):
        with patch.object(sys, "argv", ["plex-presence", "--no-browser"]), patch("plex_presence.__main__.InstanceLock", side_effect=OSError), patch("plex_presence.__main__.reopen_running") as reopen, patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(main(), 1)
            reopen.assert_not_called()

    def run_isolated_main(self, arguments, native_error=None):
        service, server, lock, tray, constructor = Mock(), Mock(), Mock(), Mock(), Mock()
        service.stop.wait.return_value = True
        if native_error:
            constructor.side_effect = native_error
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["plex-presence", *arguments]))
            stack.enter_context(patch.dict(sys.modules, {"plex_presence.native_gui":SimpleNamespace(NativeApp=constructor)}))
            stack.enter_context(patch("plex_presence.__main__.InstanceLock", return_value=lock))
            stack.enter_context(patch("plex_presence.__main__.Service", return_value=service))
            stack.enter_context(patch("plex_presence.__main__.make_server", return_value=server))
            stack.enter_context(patch("plex_presence.__main__.threading.Thread"))
            stack.enter_context(patch("plex_presence.__main__.signal.signal"))
            tray_factory = stack.enter_context(patch("plex_presence.__main__.start_tray", return_value=tray))
            browser = stack.enter_context(patch("webbrowser.open"))
            stack.enter_context(patch("sys.stdout", new=io.StringIO()))
            stack.enter_context(patch("sys.stderr", new=io.StringIO()))
            result = main()
            browser.assert_not_called()
        return result, service, server, lock, tray, constructor, tray_factory

    def test_normal_and_background_launch_use_native_window(self):
        for arguments, hidden in (([], False), (["--background"], True)):
            with self.subTest(arguments=arguments):
                result, service, server, lock, tray, constructor, factory = self.run_isolated_main(arguments)
                self.assertEqual(result, 0)
                constructor.assert_called_once_with(service, start_hidden=hidden)
                constructor.return_value.run.assert_called_once()
                factory.assert_called_once_with(service)
                service.stop.set.assert_called_once()
                server.shutdown.assert_called_once()
                tray.stop.assert_called_once()
                lock.close.assert_called_once()

    def test_headless_launch_does_not_create_native_window_or_tray(self):
        result, service, server, lock, tray, constructor, factory = self.run_isolated_main(["--no-browser"])
        self.assertEqual(result, 0)
        constructor.assert_not_called()
        factory.assert_not_called()
        service.stop.wait.assert_called_once_with(0.5)
        server.shutdown.assert_called_once()
        lock.close.assert_called_once()

    def test_native_start_failure_releases_resources(self):
        result, service, server, lock, tray, constructor, factory = self.run_isolated_main([], RuntimeError("Qt unavailable"))
        self.assertEqual(result, 1)
        server.server_close.assert_called_once()
        lock.close.assert_called_once()
        factory.assert_not_called()
        constructor.return_value.run.assert_not_called()

    def test_background_and_headless_options_are_mutually_exclusive(self):
        with patch.object(sys, "argv", ["plex-presence", "--background", "--no-browser"]), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit) as stopped:
            main()
        self.assertEqual(stopped.exception.code, 2)

    def test_windows_tray_controls_signal_native_process(self):
        fake, pillow, service = Mock(), Mock(), Mock()
        with patch("plex_presence.tray.sys.platform", "win32"), patch.dict(sys.modules, {"pystray":fake, "PIL":pillow}), patch("plex_presence.tray.threading.Thread"), patch("webbrowser.open") as browser:
            self.assertIsNotNone(start_tray(service))
            items = fake.MenuItem.call_args_list
            items[0].args[1]()
            service.show_window.set.assert_called_once()
            service.stop.set.assert_not_called()
            items[1].args[1]()
            service.stop.set.assert_called_once()
            service.wake.set.assert_called_once()
            browser.assert_not_called()
