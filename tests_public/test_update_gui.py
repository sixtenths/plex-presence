import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMainWindow
    from plex_presence.update_gui import UpdateDialog
except ImportError:
    UpdateDialog = None


@unittest.skipIf(UpdateDialog is None, "Qt desktop dependency is not installed")
class UpdateDialogTests(unittest.TestCase):
    def setUp(self):
        self.qt = QApplication.instance() or QApplication([])
        self.qt.setQuitOnLastWindowClosed(False)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.owner = SimpleNamespace(window=QMainWindow(), closed=False, preferences_dirty=False,
            service=SimpleNamespace(directory=Path(self.directory.name), port=8765,
                stop=threading.Event(), wake=threading.Event()), _external=Mock(), update_process=None)
        self.manager = Mock()
        self.manager.last_result.return_value = {}
        self.release = {"current_version":"0.3.0", "latest_version":"0.4.0", "available":True,
            "notes":"A useful improvement.\n<untrusted>plain text</untrusted>", "release_url":"https://github.com/sixtenths/plex-presence/releases/tag/v0.4.0",
            "asset_name":"PlexPresence-Setup-windows-x64.exe", "asset_size":40 * 1048576,
            "can_install":True, "reason":""}
        self.manager.check.return_value = self.release
        self.dialog = UpdateDialog(self.owner, self.manager)
        self.dialog.timer.stop()
        self.addCleanup(self.close)

    def close(self):
        self.dialog.shutdown(wait=True)
        self.dialog.deleteLater()
        self.owner.window.deleteLater()

    def drain_until(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition():
            self.qt.processEvents()
            self.dialog._drain()
            if time.monotonic() > deadline:
                self.fail("Update UI did not complete its operation")
            threading.Event().wait(.01)

    def checked(self):
        self.dialog.check()
        self.drain_until(lambda: self.dialog.operation is None)

    def test_checks_only_on_request_and_uses_a_worker_thread(self):
        self.manager.check.assert_not_called()
        worker_threads = []
        self.manager.check.side_effect = lambda: worker_threads.append(threading.get_ident()) or self.release
        self.checked()
        self.assertNotEqual(worker_threads, [threading.get_ident()])
        self.assertIn("0.4.0", self.dialog.heading.text())
        self.assertFalse(self.dialog.install_button.isHidden())
        self.assertTrue(self.dialog.release_button.isHidden())
        self.assertIn("<untrusted>plain text</untrusted>", self.dialog.notes.toPlainText())
        self.assertFalse(self.dialog.notes.openExternalLinks())

    def test_current_version_has_no_install_action(self):
        self.manager.check.return_value = dict(self.release, available=False)
        self.checked()
        self.assertEqual(self.dialog.heading.text(), "You're up to date")
        self.assertTrue(self.dialog.install_button.isHidden())
        self.assertTrue(self.dialog.release_button.isHidden())

    def test_source_install_opens_only_the_official_release_page(self):
        self.manager.check.return_value = dict(self.release, can_install=False, reason="This is a source checkout.")
        self.checked()
        self.assertTrue(self.dialog.install_button.isHidden())
        self.assertFalse(self.dialog.release_button.isHidden())
        self.dialog.release_button.click()
        self.owner._external.assert_called_once_with("https://github.com/sixtenths/plex-presence/releases/latest")

    def test_check_failure_is_visible_and_retry_is_available(self):
        self.manager.check.side_effect = ValueError("GitHub could not be reached.")
        self.checked()
        self.assertIn("GitHub could not be reached", self.dialog.feedback.text())
        self.assertTrue(self.dialog.check_button.isEnabled())
        self.assertFalse(self.owner.service.stop.is_set())

    def test_previous_install_failure_remains_visible_after_checking(self):
        self.manager.last_result.return_value = {"schema":1, "state":"failed", "message":"The previous app was restored."}
        other = UpdateDialog(self.owner, self.manager)
        other.timer.stop()
        try:
            other._checked(self.release)
            self.assertFalse(other.previous_result.isHidden())
            self.assertIn("previous app was restored", other.previous_result.text())
        finally:
            other.shutdown(wait=True)
            other.deleteLater()

    def test_dirty_preferences_can_cancel_without_downloading(self):
        self.checked()
        self.owner.preferences_dirty = True
        with patch.object(self.dialog, "_confirm_discard", return_value=False) as confirm:
            self.dialog.install()
        confirm.assert_called_once()
        self.manager.download.assert_not_called()
        self.assertFalse(self.owner.service.stop.is_set())

    def test_dirty_preferences_confirmation_defaults_to_cancel(self):
        self.owner.preferences_dirty = True
        with patch("plex_presence.update_gui.QMessageBox") as box:
            prompt = box.return_value
            update, cancel = Mock(), Mock()
            prompt.addButton.side_effect = [update, cancel]
            prompt.clickedButton.return_value = cancel
            self.assertFalse(self.dialog._confirm_discard())
            prompt.setDefaultButton.assert_called_once_with(cancel)
            prompt.setEscapeButton.assert_called_once_with(cancel)

    def test_download_can_be_cancelled_without_installing(self):
        entered = threading.Event()
        def download(_release, progress_callback, cancel_event):
            entered.set()
            cancel_event.wait(2)
            raise ValueError("Cancelled")
        self.manager.download.side_effect = download
        self.checked()
        self.dialog.install()
        self.assertTrue(entered.wait(1))
        self.dialog.cancel_download()
        self.drain_until(lambda: self.dialog.operation is None)
        self.assertEqual(self.dialog.heading.text(), "Download cancelled")
        self.manager.prepare_install.assert_not_called()
        self.manager.launch_install.assert_not_called()
        self.assertFalse(self.owner.service.stop.is_set())

    def test_closing_during_download_cancels_then_hides(self):
        self.dialog.show()
        entered = threading.Event()
        def download(_release, progress_callback, cancel_event):
            entered.set()
            cancel_event.wait(2)
            raise ValueError("Cancelled")
        self.manager.download.side_effect = download
        self.checked()
        self.dialog.install()
        self.assertTrue(entered.wait(1))
        self.dialog.reject()
        self.assertTrue(self.dialog.close_requested)
        self.drain_until(lambda: self.dialog.operation is None)
        self.assertFalse(self.dialog.isVisible())
        self.manager.launch_install.assert_not_called()

    def test_verification_failure_keeps_the_current_app_running(self):
        self.manager.download.side_effect = ValueError("The download did not match its checksum.")
        self.checked()
        self.dialog.install()
        self.drain_until(lambda: self.dialog.operation is None)
        self.assertIn("checksum", self.dialog.feedback.text())
        self.manager.launch_install.assert_not_called()
        self.assertFalse(self.owner.service.stop.is_set())

    def test_shutdown_happens_only_after_the_helper_is_ready(self):
        launch_entered, ready = threading.Event(), threading.Event()
        process = Mock()
        self.manager.download.return_value = Path("verified-installer.exe")
        self.manager.prepare_install.return_value = Path("plan.json")
        def launch(_plan):
            launch_entered.set()
            ready.wait(2)
            return process
        self.manager.launch_install.side_effect = launch
        self.checked()
        self.dialog.install()
        self.assertTrue(launch_entered.wait(1))
        self.assertFalse(self.owner.service.stop.is_set())
        self.dialog.cancel_download()
        self.assertFalse(self.dialog.cancel_event.is_set())
        ready.set()
        self.drain_until(self.owner.service.stop.is_set)
        self.assertIs(self.owner.update_process, process)
        self.assertTrue(self.owner.service.wake.is_set())
        process.stdin.close.assert_not_called()

    def test_app_shutdown_cancels_a_pending_download(self):
        entered = threading.Event()
        def download(_release, progress_callback, cancel_event):
            entered.set()
            cancel_event.wait(2)
            raise ValueError("Cancelled")
        self.manager.download.side_effect = download
        self.checked()
        self.dialog.install()
        self.assertTrue(entered.wait(1))
        self.dialog.shutdown(wait=True)
        self.assertTrue(self.dialog.cancel_event.is_set())
        self.manager.launch_install.assert_not_called()

