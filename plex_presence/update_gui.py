"""User-controlled native update checks and verified installation handoff."""
from concurrent.futures import CancelledError, ThreadPoolExecutor
import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from . import __version__


RELEASES_URL = "https://github.com/sixtenths/plex-presence/releases/latest"


class _Cancelled(Exception):
    pass


class UpdateDialog(QDialog):
    """All network and preparation work runs off the Qt thread."""
    def __init__(self, owner, manager=None):
        super().__init__(owner.window)
        self.owner = owner
        self.error_types = (ValueError,)
        try:
            from .updates import UpdateError, UpdateManager
            self.error_types = (UpdateError, ValueError)
        except ImportError:
            if manager is None:
                raise
        self.manager = manager if manager is not None else UpdateManager(owner.service.directory, owner.service.port)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-updates")
        self.results = queue.Queue()
        self.cancel_event = threading.Event()
        self.committed = threading.Event()
        self.operation = None
        self.release = None
        self.close_requested = False
        self.shutting_down = False
        self.setWindowTitle("Plex Presence updates")
        self.setWindowIcon(owner.window.windowIcon())
        self.setWindowModality(Qt.WindowModal)
        self.resize(640, 590)
        self.setMinimumSize(520, 470)
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain)
        self.timer.start(80)
        previous = self.manager.last_result() if hasattr(self.manager, "last_result") else None
        if isinstance(previous, dict) and previous.get("state") in ("installed", "failed"):
            self.previous_result.setText("Last update: " + str(previous.get("message", ""))[:500])
            self.previous_result.setStyleSheet("color: #ffb4b8" if previous["state"] == "failed" else "color: #aaa6c4")
            self.previous_result.show()

    @staticmethod
    def _label(text, style=""):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setObjectName(style)
        return label

    @staticmethod
    def _button(text, callback, style=""):
        button = QPushButton(text)
        button.setObjectName(style)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(self._label("App updates", "PageTitle"))
        self.version = self._label("Installed version " + __version__, "Muted")
        layout.addWidget(self.version)
        self.previous_result = self._label("", "Notice")
        self.previous_result.hide()
        layout.addWidget(self.previous_result)
        card = QFrame()
        card.setObjectName("Card")
        card.setStyleSheet("QFrame#Card { background: #252631; border-radius: 14px; }")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)
        self.heading = self._label("Keep Plex Presence up to date", "CardTitle")
        self.description = self._label("Check GitHub for the latest stable version. Updates keep your accounts and preferences.", "Muted")
        card_layout.addWidget(self.heading)
        card_layout.addWidget(self.description)
        layout.addWidget(card)
        self.notes_title = self._label("What's new", "Muted")
        layout.addWidget(self.notes_title)
        self.notes = QTextBrowser()
        self.notes.setOpenLinks(False)
        self.notes.setOpenExternalLinks(False)
        self.notes.setMinimumHeight(110)
        self.notes.setAccessibleName("Release notes")
        self.notes.setPlainText("Release notes appear here after checking for updates.")
        layout.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.feedback = self._label("Nothing installs until you choose Download and install.", "Notice")
        self.feedback.setMinimumHeight(34)
        layout.addWidget(self.feedback)
        footer = QWidget()
        row = QHBoxLayout(footer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.close_button = self._button("Close", self.reject)
        self.cancel_button = self._button("Cancel download", self.cancel_download)
        self.cancel_button.hide()
        self.check_button = self._button("Check for updates", self.check)
        self.install_button = self._button("Download and install", self.install, "Primary")
        self.install_button.hide()
        self.release_button = self._button("Open GitHub release", lambda: self.owner._external(RELEASES_URL), "Primary")
        self.release_button.hide()
        row.addWidget(self.close_button)
        row.addWidget(self.cancel_button)
        row.addStretch()
        row.addWidget(self.check_button)
        row.addWidget(self.install_button)
        row.addWidget(self.release_button)
        layout.addWidget(footer)

    def _error_color(self, error):
        self.feedback.setStyleSheet("color: #ffb4b8" if error else "color: #aaa6c4")

    def _start_work(self, operation, function):
        if self.operation or self.shutting_down:
            return False
        self.operation = operation
        self.close_requested = False
        self.cancel_event.clear()
        self.committed.clear()
        self._error_color(False)
        self.check_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.release_button.hide()
        self.cancel_button.setVisible(operation == "install")
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.show()

        def run():
            try:
                return function(), None
            except _Cancelled:
                return None, "cancelled"
            except Exception as error:
                if self.cancel_event.is_set() and not self.committed.is_set():
                    return None, "cancelled"
                message = str(error)[:500] if isinstance(error, self.error_types) else "The update could not be completed. Check your connection and try again."
                return None, message

        future = self.executor.submit(run)
        def completed(done):
            try:
                value, error = done.result()
            except CancelledError:
                value, error = None, "cancelled"
            self.results.put(("finished", operation, value, error))
        future.add_done_callback(completed)
        return True

    def check(self):
        if self._start_work("check", self.manager.check):
            self.heading.setText("Checking for updates…")
            self.description.setText("Looking for the latest stable release on GitHub.")
            self.feedback.setText("Your current app keeps running while we check.")

    def _confirm_discard(self):
        if not self.owner.preferences_dirty:
            return True
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Unsaved preferences")
        prompt.setText("Unsaved preferences will be lost")
        prompt.setInformativeText("The app will restart to install the update. Cancel to save your preferences first, or continue with the update.")
        update = prompt.addButton("Continue update", QMessageBox.AcceptRole)
        cancel = prompt.addButton(QMessageBox.Cancel)
        prompt.setDefaultButton(cancel)
        prompt.setEscapeButton(cancel)
        prompt.exec()
        return prompt.clickedButton() is update

    def install(self):
        if self.operation or not self.release or not self.release.get("available") or not self.release.get("can_install"):
            return
        if not self._confirm_discard():
            return
        release = dict(self.release)

        def download_and_prepare():
            def progress(downloaded, total):
                self.results.put(("progress", downloaded, total))
            artifact = self.manager.download(release, progress_callback=progress, cancel_event=self.cancel_event)
            if self.cancel_event.is_set() or self.shutting_down or self.owner.closed:
                raise _Cancelled()
            # After this point the requested installation is being handed to the
            # helper. A close/cancel cannot silently turn it into an unsafe partial update.
            self.committed.set()
            if self.cancel_event.is_set() or self.shutting_down or self.owner.closed:
                self.committed.clear()
                raise _Cancelled()
            self.results.put(("phase", "Preparing installation…"))
            plan = self.manager.prepare_install(release, artifact)
            self.results.put(("phase", "Getting ready to restart…"))
            process = self.manager.launch_install(plan)
            # This plain Python reference keeps the helper's stdin alive until
            # __main__ has finished shutting down the app and releases it.
            self.owner.update_process = process
            return process

        if self._start_work("install", download_and_prepare):
            self.heading.setText("Downloading version " + str(release["latest_version"]))
            self.description.setText("The download will be verified before installation. Your accounts and preferences will be kept.")
            self.feedback.setText("Starting download…")

    def cancel_download(self):
        if self.operation != "install" or self.committed.is_set():
            return
        self.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.feedback.setText("Cancelling download…")

    def _checked(self, release):
        self.release = release
        self.version.setText("Installed version " + str(release.get("current_version", __version__)))
        self.notes.setPlainText(str(release.get("notes") or "No release notes were provided."))
        self.check_button.setText("Check again")
        if release.get("available"):
            self.heading.setText("Version " + str(release["latest_version"]) + " is available")
            self.description.setText("Install the latest update when you're ready. The app will close and reopen with your saved settings.")
            if release.get("can_install"):
                size = release.get("asset_size", 0)
                suffix = f" · {size / (1024 * 1024):.1f} MB" if isinstance(size, (int, float)) and size > 0 else ""
                self.feedback.setText("Ready to download" + suffix)
            else:
                self.feedback.setText(str(release.get("reason") or "Download the release for your installation from GitHub."))
        else:
            self.heading.setText("You're up to date")
            self.description.setText("This installation has the latest stable version available for it.")
            self.feedback.setText("No update is needed.")

    def _idle_controls(self):
        self.check_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self.cancel_button.hide()
        available = bool(self.release and self.release.get("available"))
        can_install = bool(available and self.release.get("can_install"))
        self.install_button.setVisible(can_install)
        self.install_button.setEnabled(can_install)
        self.release_button.setVisible(available and not can_install)

    def _drain(self):
        if self.shutting_down:
            return
        for _ in range(100):
            try:
                result = self.results.get_nowait()
            except queue.Empty:
                break
            if result[0] == "progress":
                downloaded, total = result[1:]
                if total:
                    self.progress.setRange(0, 1000)
                    self.progress.setValue(max(0, min(1000, round(downloaded / total * 1000))))
                    self.feedback.setText(f"Downloaded {downloaded / 1048576:.1f} of {total / 1048576:.1f} MB")
            elif result[0] == "phase":
                self.heading.setText(result[1])
                self.feedback.setText("The app will restart after the update is installed.")
                self.cancel_button.setEnabled(False)
                self.close_button.setEnabled(False)
                self.progress.setRange(0, 0)
            elif result[0] == "finished":
                _kind, operation, value, error = result
                self.operation = None
                self.progress.hide()
                if error:
                    self.heading.setText("Download cancelled" if error == "cancelled" else "Couldn't complete the update")
                    self.description.setText("Your current app and saved settings have not been changed.")
                    self.feedback.setText("Nothing was installed. You can try again whenever you're ready." if error == "cancelled" else error)
                    self._error_color(error != "cancelled")
                elif operation == "check":
                    self._checked(value)
                elif operation == "install":
                    self.heading.setText("Installing update…")
                    self.feedback.setText("Plex Presence will reopen when installation finishes.")
                    self.owner.service.stop.set()
                    self.owner.service.wake.set()
                    return
                self._idle_controls()
                if self.close_requested:
                    super().reject()

    def reject(self):
        if self.operation == "install":
            if self.committed.is_set():
                return
            self.close_requested = True
            self.cancel_download()
            return
        super().reject()

    def closeEvent(self, event):
        if self.operation == "install":
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)

    def shutdown(self, wait=False):
        self.shutting_down = True
        self.timer.stop()
        if not self.committed.is_set():
            self.cancel_event.set()
        self.executor.shutdown(wait=wait, cancel_futures=True)
