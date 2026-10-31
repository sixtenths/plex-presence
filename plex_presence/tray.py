"""Windows notification-area controls; GUI remains in the default browser."""
import sys
import threading
import webbrowser


def start_tray(service):
    if sys.platform != "win32":
        return None
    try:
        import pystray
        from PIL import Image, ImageDraw
        image = Image.new("RGBA", (64, 64), (23, 25, 33, 255))
        draw = ImageDraw.Draw(image)
        draw.polygon([(21, 12), (35, 12), (48, 32), (35, 52), (21, 52), (34, 32)], fill="#e5a00d")

        def configure(*_):
            webbrowser.open(service.origin)

        def quit_app(*_):
            service.stop.set()
            service.wake.set()

        icon = pystray.Icon("PlexPresence", image, "Plex Presence — running in background", menu=pystray.Menu(
            pystray.MenuItem("Open settings / setup guide", configure, default=True),
            pystray.MenuItem("Quit Plex Presence", quit_app)))
        threading.Thread(target=icon.run, name="system-tray", daemon=True).start()
        return icon
    except Exception:
        # The local GUI and Start menu shortcut still provide configuration/quit.
        return None
