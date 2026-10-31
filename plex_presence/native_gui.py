"""Native Qt desktop controls; all service/network work stays off the UI thread."""
from concurrent.futures import ThreadPoolExecutor
import html
import math
from pathlib import Path
import queue
import re
import sys
import time
from urllib.request import Request, urlopen
import webbrowser

from PySide6.QtCore import Qt, QTimer, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QCheckBox, QComboBox, QDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QPushButton,
    QProgressBar, QScrollArea, QSizePolicy, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import __version__
from .guide_content import GuideParser, load_guide
from .storage import read_json, save_private


def playback_progress(activity, now=None):
    """Return elapsed/duration only for a real bounded playback timeline."""
    stamps = activity.get("timestamps", {})
    try:
        start, end = float(stamps["start"]), float(stamps["end"])
        start = start / 1000 if start > 100_000_000_000 else start
        end = end / 1000 if end > 100_000_000_000 else end
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            return None
        return max(0, min((time.time() if now is None else now) - start, end - start)), end - start
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def navigation_icon(name):
    """Small original line icons drawn by the native toolkit."""
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#bfb4e8"), 1.6))
    if name == "Overview":
        for x, y in ((3, 3), (12, 3), (3, 12), (12, 12)):
            painter.drawRoundedRect(x, y, 6, 6, 1, 1)
    elif name == "Connections":
        painter.drawRoundedRect(2, 3, 11, 8, 3, 3)
        painter.drawRoundedRect(9, 11, 11, 8, 3, 3)
        painter.drawLine(8, 8, 14, 14)
    elif name == "Preferences":
        for y, x in ((5, 7), (11, 14), (17, 9)):
            painter.drawLine(3, y, 19, y)
            painter.setBrush(QColor("#18191f"))
            painter.drawRoundedRect(x - 2, y - 2, 4, 4, 1, 1)
    else:
        painter.drawRoundedRect(3, 3, 16, 16, 2, 2)
        painter.drawLine(8, 3, 8, 19)
        painter.drawLine(11, 8, 16, 8)
        painter.drawLine(11, 12, 16, 12)
    painter.end()
    return QIcon(pixmap)


class Toggle(QAbstractButton):
    """A keyboard-accessible, native-painted switch."""
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(48, 30)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#afa4f5" if self.isChecked() else "#41434f"))
        painter.drawRoundedRect(2, 3, 44, 24, 12, 12)
        painter.setBrush(QColor("#252133" if self.isChecked() else "#d8d9e1"))
        painter.drawEllipse(26 if self.isChecked() else 6, 7, 16, 16)
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor("#d5ceff"))
            painter.drawRoundedRect(0, 1, 47, 28, 14, 14)


class ComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#beb6d5" if self.isEnabled() else "#696574"), 1.8))
        x, y = self.width() - 18, self.height() // 2
        painter.drawLine(x - 4, y - 2, x, y + 2)
        painter.drawLine(x, y + 2, x + 4, y - 2)


class MainWindow(QMainWindow):
    hide_requested = Signal()

    def closeEvent(self, event):
        event.ignore()
        self.hide_requested.emit()


STYLE = """
QWidget { font-family: 'Segoe UI', 'Helvetica Neue', sans-serif; font-size: 14px; color: #e9eaf0; }
QMainWindow, QWidget#Main, QWidget#Page, QScrollArea { background: #111217; }
QWidget#Sidebar { background: #18191f; border-right: 1px solid #24252e; }
QFrame#Card { background: #1d1f27; border: none; border-radius: 16px; }
QFrame#Welcome { background: #262338; border: none; border-radius: 16px; }
QLabel { background: transparent; border: none; }
QLabel#Brand { font-size: 19px; font-weight: 650; color: #f5f3ff; }
QLabel#PageTitle { font-size: 29px; font-weight: 650; color: #f4f3fa; }
QLabel#CardTitle { font-size: 18px; font-weight: 600; }
QLabel#ActivityTitle { font-size: 23px; font-weight: 600; }
QLabel#Muted { color: #a4a6b6; font-size: 13px; }
QLabel#Eyebrow { color: #9d9bad; font-size: 11px; font-weight: 650; letter-spacing: 1.4px; }
QLabel#Notice { color: #aaa6c4; font-size: 12px; }
QLabel#Notice[error="true"] { color: #ffb4b8; }
QLabel#Poster { color: #78788b; background: #282a35; border-radius: 12px; font-size: 12px; }
QPushButton { background: #30323f; border: 1px solid transparent; border-radius: 10px; padding: 10px 16px; font-weight: 550; }
QPushButton:hover { background: #3b3d4c; }
QPushButton:pressed { background: #464452; }
QPushButton:focus { border: 1px solid #b9acf9; }
QPushButton:disabled { background: #272933; color: #70717e; }
QPushButton#Primary { background: #b4a9fa; color: #201b36; }
QPushButton#Primary:hover { background: #c6bdff; }
QPushButton#Primary:disabled { background: #4e476c; color: #b1aac9; }
QPushButton#Quiet { background: transparent; color: #c1b8ef; padding-left: 0px; padding-right: 8px; text-align: left; }
QPushButton#Quiet:hover { color: #e5deff; }
QPushButton#Nav { text-align: left; background: transparent; color: #a8a8b8; border-radius: 10px; padding: 13px 16px; }
QPushButton#Nav:hover { background: #242530; color: #e9e5ff; }
QPushButton#Nav[selected="true"] { background: #332f49; color: #d8cffc; }
QLineEdit, QComboBox { background: #15161d; border: 1px solid #363745; border-radius: 9px; padding: 10px 12px; selection-background-color: #665c97; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #aca0ee; }
QLineEdit:read-only { color: #aaaabd; }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView { background: #242632; selection-background-color: #45405e; border: 1px solid #484457; padding: 6px; }
QCheckBox { spacing: 10px; padding: 6px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #636173; border-radius: 5px; background: #191a23; }
QCheckBox::indicator:checked { background: #b4a9fa; border: 1px solid #b4a9fa; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #41404f; min-height: 30px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #625c78; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QTextBrowser { background: transparent; border: none; color: #d7d6e4; font-size: 14px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 10px 12px; margin-bottom: 4px; border-radius: 8px; color: #aaa7ba; }
QListWidget::item:selected { background: #373148; color: #e1d7ff; }
QDialog { background: #1d1f27; }
QProgressBar { background: #343340; border: none; border-radius: 2px; }
QProgressBar::chunk { background: #b4a9fa; border-radius: 2px; }
QToolTip { background: #343041; color: #f2ecff; border: 1px solid #625b79; padding: 6px; }
"""


class NativeApp:
    def __init__(self, service, start_hidden=False):
        self.service = service
        self.application = QApplication.instance() or QApplication(sys.argv[:1])
        self.application.setApplicationName("Plex Presence")
        self.application.setQuitOnLastWindowClosed(False)
        self.application.setStyle("Fusion")
        self.application.setStyleSheet(STYLE)
        self.window = self.root = MainWindow()
        self.window.setWindowTitle("Plex Presence")
        self.window.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/app.png")))
        screen = self.application.primaryScreen().availableGeometry()
        width, height = min(1200, screen.width() - 40), min(850, screen.height() - 40)
        self.window.resize(width, height)
        self.window.setMinimumSize(min(900, width), min(580, height))
        self.window.move(screen.x() + (screen.width() - width) // 2, screen.y() + (screen.height() - height) // 2)
        self.window.hide_requested.connect(self.hide)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="desktop-ui")
        self.results = queue.Queue()
        self.closed = self.busy = self.status_pending = self.pin_pending = False
        self.last_status_request = self.last_pin_poll = 0
        self.initialized = self.settings_fill = False
        self.preferences_dirty = False
        self.preference_revision = 0
        self.refresh_profile_username = False
        self.latest_status = {}
        self.servers = self.profiles = []
        self.last_image_url = self.poster_image = None
        self.image_pending = False
        self.image_retry_at = 0
        self.guide_page = 0
        self.update_dialog = None
        self.update_process = None
        self.ui_path = service.directory / "native-ui.json"
        try:
            self.ui_state = read_json(self.ui_path)
        except (OSError, ValueError):
            self.ui_state = {}
        self.guide = load_guide(service.origin)
        self.action_buttons = []
        self._build()
        self.service.desktop_window = True
        self.service.window_visible = not start_hidden
        self.show_page("Overview")
        if not start_hidden:
            self.window.show()
        self._dark_titlebar()
        self.timer = QTimer(self.window)
        self.timer.timeout.connect(self._pump)
        self.timer.start(100)

    def _dark_titlebar(self):
        if sys.platform == "win32":
            try:
                import ctypes
                flag = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(self.window.winId())), 20, ctypes.byref(flag), ctypes.sizeof(flag))
            except (OSError, AttributeError):
                pass

    @staticmethod
    def _layout(parent, spacing=14, margins=0, horizontal=False):
        layout = QHBoxLayout(parent) if horizontal else QVBoxLayout(parent)
        layout.setContentsMargins(margins, margins, margins, margins)
        layout.setSpacing(spacing)
        return layout

    @staticmethod
    def _label(text, style=""):
        widget = QLabel(text)
        widget.setTextFormat(Qt.PlainText)
        widget.setWordWrap(True)
        if style:
            widget.setObjectName(style)
        return widget

    def _button(self, text, command, style="", action=False):
        widget = QPushButton(text.replace("&", "&&"))
        widget.setCursor(Qt.PointingHandCursor)
        widget.setObjectName(style)
        widget.clicked.connect(lambda _checked=False: command())
        if action:
            self.action_buttons.append(widget)
        return widget

    def _row(self, *widgets, stretch=False):
        row = QWidget()
        layout = self._layout(row, spacing=10, horizontal=True)
        for widget in widgets:
            layout.addWidget(widget)
        if stretch:
            layout.addStretch()
        return row

    def _card(self, parent_layout, title=None, subtitle=None, style="Card"):
        frame = QFrame()
        frame.setObjectName(style)
        layout = self._layout(frame, spacing=16, margins=24)
        if title:
            layout.addWidget(self._label(title, "CardTitle"))
        if subtitle:
            layout.addWidget(self._label(subtitle, "Muted"))
        parent_layout.addWidget(frame)
        return layout

    def _field(self, layout, title, password=False, placeholder=""):
        group = QWidget()
        row = self._layout(group, spacing=7)
        row.addWidget(self._label(title, "Muted"))
        entry = QLineEdit()
        entry.setMinimumHeight(42)
        entry.setAccessibleName(title)
        entry.setPlaceholderText(placeholder)
        if password:
            entry.setEchoMode(QLineEdit.Password)
        row.addWidget(entry)
        layout.addWidget(group)
        return entry

    def _scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Page")
        layout = self._layout(body, spacing=18)
        layout.setContentsMargins(0, 0, 12, 8)
        layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(body)
        return scroll, layout

    def _build(self):
        shell = QWidget()
        shell.setObjectName("Main")
        self.window.setCentralWidget(shell)
        outer = self._layout(shell, spacing=0, horizontal=True)
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(218)
        left = self._layout(sidebar, spacing=8, margins=16)
        left.setContentsMargins(16, 30, 16, 22)
        app_image = QLabel()
        app_image.setPixmap(self.window.windowIcon().pixmap(28, 28))
        app_image.setFixedSize(28, 28)
        brand = self._row(app_image, self._label("Plex Presence", "Brand"))
        brand.layout().setSpacing(9)
        brand.layout().setContentsMargins(6, 0, 0, 0)
        left.addWidget(brand)
        left.addWidget(self._label("   YOUR PLAYBACK, SHARED", "Eyebrow"))
        left.addSpacing(28)
        self.nav = {}
        for title in ("Overview", "Connections", "Preferences", "Setup guide"):
            button = self._button(title, lambda name=title: self.show_page(name), "Nav")
            button.setIcon(navigation_icon(title))
            button.setIconSize(QSize(20, 20))
            self.nav[title] = button
            left.addWidget(button)
        left.addStretch()
        left.addWidget(self._label("Runs in the background", "Muted"))
        left.addSpacing(4)
        left.addWidget(self._button("Hide window", self.hide))
        left.addWidget(self._button("Quit application", self._quit, "Quiet"))
        left.addSpacing(12)
        left.addWidget(self._button("Check for updates", self.show_updates, "Quiet"))
        left.addWidget(self._label("Version " + __version__, "Muted"))
        outer.addWidget(sidebar)
        main = QWidget()
        content = self._layout(main, spacing=12, margins=30)
        self.page_title = self._label("Overview", "PageTitle")
        self.page_description = self._label("", "Muted")
        content.addWidget(self.page_title)
        content.addWidget(self.page_description)
        content.addSpacing(10)
        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        self.pages = {}
        for title in ("Overview", "Connections", "Preferences"):
            scroll, layout = self._scroll_page()
            if title == "Preferences":
                page = QWidget()
                page_layout = self._layout(page, spacing=12)
                page_layout.addWidget(scroll, 1)
                page_layout.addWidget(self._row(self._button("Save preferences", self._save_preferences, "Primary", True), stretch=True))
            else:
                page = scroll
            self.pages[title] = page
            self.stack.addWidget(page)
            if title == "Overview":
                self._build_overview(layout)
            elif title == "Connections":
                self._build_plex(layout)
                self._build_discord(layout)
            else:
                self._build_preferences(layout)
        help_page = QWidget()
        self.pages["Setup guide"] = help_page
        self.stack.addWidget(help_page)
        self._build_guide(help_page)
        self.notice = self._label("Closing this window keeps your activity running.", "Notice")
        self.notice.setMinimumHeight(32)
        content.addWidget(self.notice)
        outer.addWidget(main, 1)

    def show_page(self, title):
        self.stack.setCurrentWidget(self.pages[title])
        self.page_title.setText(title)
        descriptions = {
            "Overview": "Your playback and connections, at a glance.",
            "Connections": "Your Plex library. Your Discord account.",
            "Preferences": "Choose exactly what appears on your profile.",
            "Setup guide": "Everything you need, from your first sign-in to everyday use.",
        }
        self.page_description.setText(descriptions[title])
        for name, button in self.nav.items():
            button.setProperty("selected", name == title)
            button.style().unpolish(button)
            button.style().polish(button)

    def _build_overview(self, layout):
        welcome = self._card(layout, "Make yourself at home", "A few simple steps, then Plex Presence takes care of the rest.", "Welcome")
        self.welcome_frame = welcome.parentWidget()
        welcome.addWidget(self._label("1   Sign in to Plex     2   Choose your server     3   Connect Discord", "Muted"))
        welcome.addWidget(self._row(self._button("Start setup", lambda: self.show_page("Connections"), "Primary"), self._button("Walk me through it", self.show_guide, "Quiet"), stretch=True))
        if self.ui_state.get("guide_seen"):
            self.welcome_frame.hide()
        activity = self._card(layout)
        activity.addWidget(self._label("NOW PLAYING", "Eyebrow"))
        self.poster = self._label("Artwork", "Poster")
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setFixedSize(112, 112)
        details = QWidget()
        info = self._layout(details, spacing=10)
        info.setAlignment(Qt.AlignVCenter)
        self.media_title = self._label("Ready when you are", "ActivityTitle")
        self.media_detail = self._label("Start a movie, episode, or track on your Plex player. Your activity will appear here.", "Muted")
        info.addWidget(self.media_title)
        info.addWidget(self.media_detail)
        self.progress_group = QWidget()
        progress_layout = self._layout(self.progress_group, spacing=9)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        progress_layout.addWidget(self.progress)
        self.progress_elapsed = self._label("", "Muted")
        self.progress_duration = self._label("", "Muted")
        times = self._row(self.progress_elapsed, self.progress_duration)
        times.layout().setStretch(0, 1)
        progress_layout.addWidget(times)
        info.addWidget(self.progress_group)
        self.progress_group.hide()
        row = self._row(self.poster, details)
        row.layout().setSpacing(24)
        row.layout().setStretch(1, 1)
        activity.addWidget(row)
        activity.addWidget(self._label("Pausing or stopping playback clears your Discord activity.", "Muted"))
        pair = QWidget()
        pair.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        pair_layout = self._layout(pair, spacing=18, horizontal=True)
        plex = self._card(pair_layout, "Plex")
        self.overview_plex = self._label("Connect your account to get started.", "Muted")
        plex.addWidget(self.overview_plex)
        plex.addStretch()
        plex.addWidget(self._button("Manage Plex", lambda: self.show_connection("plex"), "Quiet"))
        discord = self._card(pair_layout, "Discord")
        self.overview_discord = self._label("Checking connection…", "Muted")
        discord.addWidget(self.overview_discord)
        discord.addStretch()
        discord.addWidget(self._button("Manage Discord", lambda: self.show_connection("discord"), "Quiet"))
        layout.addWidget(pair)
        self.connection = self._label("", "Muted")
        layout.addWidget(self.connection)

    def _build_plex(self, layout):
        card = self._card(layout, "Plex", "Sign in with the account you use to watch. Shared servers and Home profiles work too.")
        self.plex_card = card.parentWidget()
        self.account_label = self._label("No account connected", "Muted")
        card.addWidget(self.account_label)
        self.plex_signin_button = self._button("Connect Plex", self._connect_plex, "Primary", True)
        self.plex_check_button = self._button("Check sign-in", self._poll_pin, action=True)
        self.plex_disconnect_button = self._button("Disconnect", lambda: self._action("/api/plex/disconnect"), "Quiet", True)
        self.plex_check_button.hide()
        self.plex_disconnect_button.hide()
        card.addWidget(self._row(self.plex_signin_button, self.plex_check_button, self.plex_disconnect_button, stretch=True))
        card.addWidget(self._label("Your server", "Muted"))
        self.server_combo = ComboBox()
        self.server_combo.setAccessibleName("Plex server")
        self.server_combo.setPlaceholderText("Sign in, then refresh your servers")
        card.addWidget(self.server_combo)
        self.select_server_button = self._button("Use this server", self._select_server, action=True)
        self.refresh_servers_button = self._button("Refresh servers", self._load_servers, "Quiet", True)
        self.select_server_button.setEnabled(False)
        self.refresh_servers_button.setEnabled(False)
        self.server_combo.currentIndexChanged.connect(lambda _index: self._update_action_states())
        card.addWidget(self._row(self.select_server_button, self.refresh_servers_button, stretch=True))
        self.chosen_server = self._label("No server selected", "Muted")
        card.addWidget(self.chosen_server)
        card.addWidget(self._row(self._button("Plex Home profiles", self._home_dialog, "Quiet"), self._button("Direct connection…", self._manual_dialog, "Quiet"), stretch=True))

    def _build_discord(self, layout):
        card = self._card(layout, "Discord", "Choose how you want to share your activity.")
        self.discord_card = card.parentWidget()
        self.mode = ComboBox()
        self.mode.addItems(("Discord desktop — simple setup", "Always-on — no Discord client"))
        self.mode.setAccessibleName("Discord connection mode")
        self.mode.currentIndexChanged.connect(self._mode_fields)
        card.addWidget(self.mode)
        card.addWidget(self._row(self._button("Apply connection mode", self._apply_mode, action=True), stretch=True))
        self.desktop_id = ""
        self.desktop_help = QWidget()
        desktop = self._layout(self.desktop_help, spacing=12)
        desktop.addWidget(self._label("Open Discord. You're ready.", "CardTitle"))
        desktop.addWidget(self._label("Keep the Discord desktop app running on this computer, signed into your own account. Plex Presence connects automatically. No Developer Portal setup needed.", "Muted"))
        desktop.addWidget(self._row(self._button("Get Discord desktop", lambda: self._external("https://discord.com/download"), "Quiet"), self._button("Custom application ID…", self._desktop_id_dialog, "Quiet"), stretch=True))
        card.addWidget(self.desktop_help)
        self.headless = QWidget()
        headless = self._layout(self.headless, spacing=14)
        self.discord_linked = self._label("Always-on mode uses your own Discord application.", "Muted")
        headless.addWidget(self.discord_linked)
        headless.addWidget(self._label("Create an application in Discord's Developer Portal, enable Social SDK access and Public Client, then add this OAuth redirect URL. The setup guide explains every step.", "Muted"))
        redirect = self._field(headless, "OAuth redirect URL")
        redirect.setText(self.service.origin + "/callback")
        redirect.setReadOnly(True)
        headless.addWidget(self._row(self._button("Copy redirect URL", self._copy_redirect, "Quiet"), self._button("Open Developer Portal", lambda: self._external("https://discord.com/developers/applications"), "Quiet"), stretch=True))
        self.client_id = self._field(headless, "Discord application ID", placeholder="Paste the application ID")
        self.consent = QCheckBox("I understand the Discord permissions below")
        headless.addWidget(self.consent)
        headless.addWidget(self._label("Discord bundles profile access, friends and blocked-user management, friends' status, game invites, and activity updates into this permission. Plex Presence uses activity updates.", "Muted"))
        headless.addWidget(self._row(self._button("Connect Discord", self._connect_discord, "Primary", True), self._button("Disconnect", lambda: self._action("/api/discord/disconnect"), "Quiet", True), stretch=True))
        card.addWidget(self.headless)
        self._mode_fields()

    def show_connection(self, target):
        self.show_page("Connections")
        card = self.discord_card if target == "discord" else self.plex_card
        QTimer.singleShot(0, lambda: self.pages["Connections"].verticalScrollBar().setValue(card.y()))

    def _build_preferences(self, layout):
        viewer = self._card(layout, "Who's watching?")
        viewer.setSpacing(12)
        self.username = QLineEdit()
        self.username.setAccessibleName("Plex username or Home profile")
        self.username.setPlaceholderText("Plex username or Home profile")
        self.username.textEdited.connect(self._preference_changed)
        viewer.addWidget(self._row(self.username, self._button("Find active users", self._load_users, "Quiet", True)))
        self.user_suggestions = self._label("For shared servers, playback is matched to your connected account.", "Muted")
        viewer.addWidget(self.user_suggestions)
        sharing = self._card(layout, "Your activity", "Control what's shared with Discord.")
        self.flags = {}
        for key, title, explanation in (
            ("enabled", "Share playback", "Show what you're watching or listening to."),
            ("artwork_enabled", "Show artwork", "Include cover art in your activity."),
            ("use_plex_artwork", "Use your Plex artwork", "Use the posters and covers from your selected server."),
            ("show_progress", "Show playback progress", "Display the playback timer while media is playing."),
        ):
            labels = QWidget()
            text = self._layout(labels, spacing=5)
            text.addWidget(self._label(title))
            text.addWidget(self._label(explanation, "Muted"))
            toggle = Toggle(title)
            toggle.toggled.connect(self._preference_changed)
            self.flags[key] = toggle
            row = self._row(labels, toggle)
            row.layout().setStretch(0, 1)
            row.layout().setContentsMargins(0, 6, 0, 6)
            sharing.addWidget(row)
            if key == "use_plex_artwork":
                self.artwork_disclosure = self._label("Selected posters are uploaded to Litterbox, or TempFile.org if unavailable, for 1 hour so Discord can display them. Images are public; Plex credentials stay local.", "Muted")
                sharing.addWidget(self.artwork_disclosure)
                self.artwork_disclosure.hide()
                toggle.toggled.connect(self.artwork_disclosure.setVisible)

    def _build_guide(self, page):
        layout = self._layout(page, spacing=16)
        frame = QFrame()
        frame.setObjectName("Card")
        columns = self._layout(frame, spacing=24, margins=20, horizontal=True)
        self.guide_chapters = QListWidget()
        self.guide_chapters.setFixedWidth(210)
        self.guide_chapters.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.guide_chapters.setWordWrap(True)
        short_titles = ("Welcome", "Install & open", "Choose a mode", "Sign in to Plex", "Home & shared servers", "Discord desktop", "Always-on setup", "Preferences & testing", "Everyday use", "Troubleshooting", "Pi & remote machines", "Final checklist")
        for index, (title, _body) in enumerate(self.guide):
            self.guide_chapters.addItem(f"{index + 1:02d}  {short_titles[index] if index < len(short_titles) else title}")
            self.guide_chapters.item(index).setToolTip(title)
        self.guide_chapters.currentRowChanged.connect(self._guide_show)
        columns.addWidget(self.guide_chapters)
        content = QWidget()
        right = self._layout(content, spacing=16)
        self.guide_title = self._label("", "CardTitle")
        right.addWidget(self.guide_title)
        self.guide_text = QTextBrowser()
        self.guide_text.setOpenLinks(False)
        self.guide_text.setOpenExternalLinks(False)
        self.guide_text.anchorClicked.connect(lambda url: self._external(url.toString()))
        right.addWidget(self.guide_text, 1)
        columns.addWidget(content, 1)
        layout.addWidget(frame, 1)
        self.guide_counter = self._label("", "Muted")
        self.guide_finish = self._button("Open connections", lambda: self._finish_guide() if self.guide_page == len(self.guide) - 1 else self.show_page("Connections"), "Primary")
        footer = self._row(self.guide_counter, self._button("Back", lambda: self._guide_show(self.guide_page - 1)), self._button("Next", lambda: self._guide_show(self.guide_page + 1)), self.guide_finish)
        footer.layout().setStretch(0, 1)
        layout.addWidget(footer)
        self._guide_show(0)

    def _guide_show(self, index):
        self.guide_page = max(0, min(index, len(self.guide) - 1))
        title, body = self.guide[self.guide_page]
        self.guide_title.setText(title)
        safe = html.escape(body)
        safe = re.sub(r"https://[^\s<)]+", lambda match: '<a style="color:#c1afff" href="' + match[0] + '">' + match[0] + '</a>', safe)
        paragraphs = ["<p style='margin-bottom:14px;line-height:145%'>" + p.replace("\n", "<br>") + "</p>" for p in safe.split("\n\n")]
        self.guide_text.setHtml("".join(paragraphs))
        self.guide_counter.setText(f"Chapter {self.guide_page + 1} of {len(self.guide)}")
        self.guide_finish.setText("Finish setup" if self.guide_page == len(self.guide) - 1 else "Open connections")
        self.guide_chapters.blockSignals(True)
        self.guide_chapters.setCurrentRow(self.guide_page)
        self.guide_chapters.blockSignals(False)

    def show_guide(self):
        self.show_page("Setup guide")

    def _finish_guide(self):
        self.ui_state["guide_seen"] = True
        save_private(self.ui_path, self.ui_state)
        self.welcome_frame.hide()
        self.show_page("Connections")
        self._message("Guide completed. Connect your accounts to get started.")

    def _message(self, message, error=False):
        self.notice.setText(message)
        self.notice.setProperty("error", error)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        for dialog in self.window.findChildren(QDialog):
            if dialog.isVisible():
                label = dialog.findChild(QLabel, "DialogNotice")
                if label:
                    label.setText(message)
                    label.setStyleSheet("color: #ffb4b8" if error else "color: #b9b0d5")

    def _submit(self, function, callback, action=False, kind="other"):
        if self.closed or (action and self.busy):
            return False
        if action:
            self.busy = True
            self._message("Working…")
            for button in self.action_buttons:
                button.setEnabled(False)
        future = self.executor.submit(function)
        future.add_done_callback(lambda done: self.results.put((done, callback, action, kind)))
        return True

    def _action(self, path, data=None, callback=None, success="Done."):
        payload = dict(data or {})
        def done(result):
            if path in ("/api/plex/profile", "/api/plex/manual", "/api/plex/disconnect"):
                self.refresh_profile_username = True
            if path not in ("/api/plex/users", "/api/plex/servers", "/api/plex/home"):
                self.settings_fill = True
            self.last_status_request = 0
            self._message(success)
            if callback:
                callback(result)
        return self._submit(lambda: self.service.action(path, payload), done, action=True)

    def _external(self, url):
        if not isinstance(url, str) or not url.startswith("https://"):
            return self._message("Only secure external links can open here.", True)
        self._submit(lambda: webbrowser.open(url), lambda ok: None if ok else self._message("Your browser could not open. Open the provider's website to continue.", True))

    def _connect_plex(self):
        self._action("/api/plex/connect", callback=lambda result: self._external(result["url"]), success="Finish signing in on Plex's website. This window updates automatically.")

    def _poll_pin(self):
        if self.pin_pending or self.busy:
            return
        self.pin_pending = True
        self.last_pin_poll = time.monotonic()
        def done(result):
            if result.get("connected"):
                self.refresh_profile_username = True
                self.settings_fill = True
                self.last_status_request = 0
                self._load_servers()
                self._message("Plex connected. Choose your server.")
        if not self._submit(lambda: self.service.action("/api/plex/poll", {}), done, kind="pin"):
            self.pin_pending = False

    def _load_servers(self):
        def loaded(result):
            self.servers = result["servers"]
            self.server_combo.clear()
            for server in self.servers:
                self.server_combo.addItem(server["name"] + (" (shared)" if not server["owned"] else ""), server["id"])
            selected_id = result.get("selected_server_id")
            selected_index = self.server_combo.findData(selected_id) if selected_id is not None else -1
            self.server_combo.setCurrentIndex(selected_index)
            self._update_action_states()
            if selected_index >= 0:
                self._message("Your selected Plex server is ready.")
            elif len(self.servers) == 1:
                self.server_combo.setCurrentIndex(0)
                self._update_action_states()
                self._select_server()
            elif not self.servers:
                self._message("No accessible servers found for this account or profile.", True)
        self._action("/api/plex/servers", callback=loaded, success="Choose a Plex server from the list.")

    def _select_server(self):
        identifier = self.server_combo.currentData()
        if identifier is None:
            return self._message("Refresh servers and choose one from the list.", True)
        self._action("/api/plex/select", {"server_id": identifier}, success="Plex server connected.")

    def _dialog(self, title, description, height=420):
        dialog = QDialog(self.window)
        dialog.setWindowTitle(title)
        dialog.setWindowIcon(self.window.windowIcon())
        dialog.resize(560, height)
        content = self._layout(dialog, spacing=16, margins=28)
        content.addWidget(self._label(title, "CardTitle"))
        content.addWidget(self._label(description, "Muted"))
        body = QWidget()
        body_layout = self._layout(body, spacing=16)
        content.addWidget(body)
        label = self._label("", "DialogNotice")
        content.addWidget(label)
        return dialog, body_layout

    def _home_dialog(self):
        dialog, content = self._dialog("Plex Home profiles", "Choose the same profile you use on your TV or player. Sign in to a member of that Plex Home first.")
        combo = ComboBox()
        combo.setAccessibleName("Home profile")
        combo.setPlaceholderText("Load your Home profiles")
        def loaded(result):
            combo.clear()
            for profile in result["profiles"]:
                combo.addItem(profile["name"] + (" (PIN required)" if profile["protected"] else ""), profile["id"])
            combo.setCurrentIndex(-1)
        content.addWidget(self._button("Load Home profiles", lambda: self._action("/api/plex/home", callback=loaded)))
        content.addWidget(combo)
        pin = self._field(content, "Profile PIN (if protected)", password=True)
        pin.setMaxLength(4)
        def select():
            if combo.currentData() is None:
                return self._message("Load Home profiles and choose one.", True)
            def selected(_result):
                dialog.accept()
                self._load_servers()
            if self._action("/api/plex/profile", {"profile_id":combo.currentData(), "pin":pin.text()}, selected, "Profile selected. Choose its server."):
                pin.clear()
        content.addWidget(self._button("Use this profile", select, "Primary"))
        content.addWidget(self._label("After switching, select a server available to this profile.", "Muted"))
        dialog.show()

    def _manual_dialog(self):
        dialog, content = self._dialog("Advanced connection", "Enter your own server and token. Normal Plex sign-in is recommended for shared servers.", 470)
        address = self._field(content, "Server address", placeholder="http://192.168.1.100:32400")
        token = self._field(content, "Plex token", password=True)
        username = self._field(content, "Plex username")
        username.setText(self.username.text())
        def connect():
            if self._action("/api/plex/manual", {"url":address.text(), "token":token.text(), "username":username.text()}, lambda _result: dialog.accept(), "Plex server connected."):
                token.clear()
        content.addWidget(self._button("Test & connect", connect, "Primary"))
        dialog.show()

    def _desktop_id_dialog(self):
        dialog, content = self._dialog("Custom application ID", "Optional: use your own Discord application name. Leave blank for the default app. This doesn't change your Discord account.", 300)
        entry = self._field(content, "Discord application ID (optional)")
        entry.setText(self.desktop_id)
        def save():
            self.desktop_id = entry.text()
            dialog.accept()
            self._apply_mode()
        content.addWidget(self._button("Save application ID", save, "Primary"))
        dialog.show()

    def _mode_fields(self, _index=None):
        if hasattr(self, "headless"):
            headless = self.mode.currentIndex() == 1
            self.headless.setVisible(headless)
            self.desktop_help.setVisible(not headless)

    def _apply_mode(self):
        self._action("/api/discord/mode", {"mode":"headless" if self.mode.currentIndex() == 1 else "desktop", "client_id":self.desktop_id}, success="Connection mode saved.")

    def _connect_discord(self):
        if not self.consent.isChecked():
            return self._message("Review the Discord permissions and check the box first.", True)
        self._action("/api/discord/connect", {"client_id":self.client_id.text()}, lambda result: self._external(result["url"]), "Approve the account connection on Discord, then return here.")

    def _copy_redirect(self):
        self.application.clipboard().setText(self.service.origin + "/callback")
        self._message("Redirect URL copied.")

    def _save_preferences(self):
        data = {key:widget.isChecked() for key, widget in self.flags.items()}
        data["plex_username"] = self.username.text()
        revision = self.preference_revision
        def saved(_result):
            if self.preference_revision == revision:
                self.preferences_dirty = False
            else:
                self._message("Saved. Your newer changes are still unsaved.")
        self._action("/api/settings", data, saved, success="Preferences saved.")

    def _preference_changed(self, _value=None):
        self.preferences_dirty = True
        self.preference_revision += 1
        self._message("You have unsaved preferences.")

    def _update_action_states(self):
        connected = bool(self.latest_status.get("plex_connected"))
        self.plex_check_button.setVisible(bool(self.latest_status.get("plex_signin_pending")))
        self.plex_disconnect_button.setVisible(connected)
        self.select_server_button.setEnabled(not self.busy and self.server_combo.currentData() is not None)
        self.refresh_servers_button.setEnabled(not self.busy and connected)

    def _load_users(self):
        self._action("/api/plex/users", callback=lambda result: self.user_suggestions.setText("Active users: " + (", ".join(result["users"]) or "none — start playback first")))

    def _status(self, status):
        self.status_pending = False
        self.latest_status = status
        settings = status["settings"]
        if not self.initialized or self.settings_fill:
            if not self.preferences_dirty:
                self.username.setText(settings.get("plex_username", ""))
                for key, toggle in self.flags.items():
                    toggle.blockSignals(True)
                    toggle.setChecked(settings.get(key) is True)
                    toggle.blockSignals(False)
                self.artwork_disclosure.setVisible(self.flags["use_plex_artwork"].isChecked())
            self.client_id.setText(settings.get("discord_client_id", ""))
            self.desktop_id = settings.get("desktop_client_id", "")
            self.mode.setCurrentIndex(1 if settings.get("discord_mode") == "headless" else 0)
            self.initialized, self.settings_fill = True, False
            self._mode_fields()
        self.username.setReadOnly(settings.get("plex_source") == "notifications")
        if self.refresh_profile_username or settings.get("plex_source") == "notifications":
            self.username.setText(settings.get("plex_username", ""))
            self.refresh_profile_username = False
        self._update_action_states()
        if settings.get("server_name") and status["discord_connected"]:
            self.welcome_frame.hide()
        self.account_label.setText("Connected as " + (settings.get("plex_profile_name") or settings.get("plex_username") or "your Plex account") if status["plex_connected"] else "No account connected")
        self.chosen_server.setText("Selected: " + settings["server_name"] if settings.get("server_name") else "No server selected")
        self.discord_linked.setText("Your Discord account is linked." if settings.get("discord_mode") == "headless" and status["discord_connected"] else "Always-on mode uses your own Discord application.")
        self.overview_plex.setText((settings.get("server_name") or "Account connected — choose your server.") if status["plex_connected"] else "Connect your account to get started.")
        self.overview_discord.setText(status["discord"].get("message", "Not connected"))
        states = {"setup_required":"Choose a server", "playing":"Playing", "paused":"Paused — activity hidden", "idle":"Waiting for playback", "buffering":"Buffering — activity hidden", "unavailable":"Server unavailable", "disabled":"Sharing disabled"}
        self.connection.setText(states.get(status["plex"], status["plex"]))
        activity = status.get("activity") or {}
        self.media_title.setText(activity.get("details", "Ready when you are"))
        self.media_detail.setText(activity.get("state", "Start a movie, episode, or track on your Plex player. Your activity will appear here."))
        timeline = playback_progress(activity)
        self.progress_group.setVisible(timeline is not None)
        if timeline:
            elapsed, duration = timeline
            self.progress.setValue(round(elapsed / duration * 1000))
            def time_label(value):
                seconds = int(value)
                return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"
            self.progress_elapsed.setText(time_label(elapsed))
            self.progress_duration.setText(time_label(duration))
        self.poster.setFixedSize(144 if activity else 112, 144 if activity else 112)
        url = activity.get("assets", {}).get("large_image")
        if url != self.last_image_url:
            self.last_image_url = url
            self.poster.clear()
            self.poster.setText("Artwork")
            self.poster_image = None
            self.image_retry_at = 0
        if self.poster_image is None and not self.image_pending and time.monotonic() >= self.image_retry_at:
            if isinstance(url, str) and url.startswith("https://wsrv.nl/?"):
                self.image_pending = True
                self.image_retry_at = time.monotonic() + 30
                def get_image():
                    with urlopen(Request(url, headers={"User-Agent":"PlexPresence"}), timeout=8) as response:
                        data = response.read(2 * 1024 * 1024 + 1)
                    if len(data) > 2 * 1024 * 1024 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                        return None
                    return url, data
                if not self._submit(get_image, self._poster, kind="poster"):
                    self.image_pending = False
        if status.get("auth_error"):
            self._message(status["auth_error"], True)
        if status.get("plex_signin_pending") and time.monotonic() - self.last_pin_poll > 3:
            self._poll_pin()

    def _poster(self, result):
        self.image_pending = False
        if result and result[0] == self.last_image_url:
            image = QPixmap()
            if image.loadFromData(result[1], "PNG"):
                self.poster_image = image
                self.poster.setPixmap(image.scaled(QSize(self.poster.width() - 4, self.poster.height() - 4), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.image_retry_at = 0

    def _pump(self):
        if self.closed:
            return
        if self.service.stop.is_set():
            self.closed = True
            self.shutdown_updates()
            self.timer.stop()
            self.service.desktop_window = self.service.window_visible = False
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.window.hide()
            self.application.quit()
            return
        if self.service.show_window.is_set():
            self.service.show_window.clear()
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            self.service.window_visible = True
        for _ in range(20):
            try:
                completed, callback, action, kind = self.results.get_nowait()
            except queue.Empty:
                break
            if action:
                self.busy = False
                for button in self.action_buttons:
                    button.setEnabled(True)
                self._update_action_states()
            if kind == "status":
                self.status_pending = False
            elif kind == "pin":
                self.pin_pending = False
            elif kind == "poster":
                self.image_pending = False
            try:
                callback(completed.result())
            except ValueError as error:
                if action:
                    self._message(str(error), True)
            except Exception:
                if action:
                    self._message("The connection failed. Check your settings and try again.", True)
        now = time.monotonic()
        if not self.status_pending and now - self.last_status_request >= 2:
            self.status_pending = True
            self.last_status_request = now
            self._submit(self.service.status, self._status, kind="status")

    def _quit(self):
        self.service.stop.set()
        self.service.wake.set()

    def show_updates(self):
        from .update_gui import UpdateDialog
        if self.update_dialog is None:
            self.update_dialog = UpdateDialog(self)
        self.update_dialog.show()
        self.update_dialog.raise_()
        self.update_dialog.activateWindow()
        if not self.update_dialog.operation:
            self.update_dialog.check()

    def shutdown_updates(self, wait=False):
        if self.update_dialog is not None:
            self.update_dialog.shutdown(wait=wait)

    def hide(self):
        self.window.hide()
        self.service.window_visible = False

    def run(self):
        self.application.exec()
