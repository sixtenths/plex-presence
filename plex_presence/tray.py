"""Windows notification-area controls for the native configuration window."""
import sys
import threading
from pathlib import Path


def start_tray(service):
    if sys.platform != "win32":
        return None
    try:
        import pystray
        from PIL import Image
        image = Image.open(Path(__file__).parent / "assets/app.png").convert("RGBA")

        def configure(*_):
            service.show_window.set()

        def quit_app(*_):
            service.stop.set()
            service.wake.set()

        icon = pystray.Icon("PlexPresence", image, "Plex Presence — running in background", menu=pystray.Menu(
            pystray.MenuItem("Open settings / setup guide", configure, default=True),
            pystray.MenuItem("Quit Plex Presence", quit_app)))
        threading.Thread(target=icon.run, name="system-tray", daemon=True).start()
        return icon
    except Exception:
        # The Start menu shortcut still reopens the native window.
        return None
