"""Run the GUI on Windows, macOS or Linux."""
import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from . import __version__
from .service import Service
from .server import make_server
from .storage import data_directory
from .instance import InstanceLock
from .tray import start_tray
from urllib.request import Request, urlopen


def reopen_running(port, show=True):
    """Ask the existing native process to reveal its window, without a browser."""
    origin = f"http://127.0.0.1:{port}"
    try:
        with urlopen(origin + "/api/window/status", timeout=3) as response:
            running = json.load(response)
        if running.get("app_id") != "plex-presence" or not running.get("desktop_window"):
            return False
        if show:
            request = Request(origin + "/api/window/show", data=b"{}", headers={
                "Content-Type":"application/json", "Origin":origin, "X-CSRF-Token":running["csrf"]})
            with urlopen(request, timeout=3) as response:
                return json.load(response).get("ok") is True
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Plex Presence — native desktop app with optional headless operation")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--uninstall-data", action="store_true", help=argparse.SUPPRESS)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--no-browser", action="store_true", help="Run headlessly; use an SSH tunnel to access web setup")
    modes.add_argument("--background", action="store_true", help="Start the desktop app with its window hidden")
    args = parser.parse_args()
    if args.uninstall_data:
        from .uninstall import uninstall_data
        try:
            uninstall_data()
            return 0
        except (OSError, ValueError, KeyError):
            return 1
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    instance = None
    try:
        instance = InstanceLock(args.data_dir)
        service = Service(args.data_dir, args.port)
        server = make_server(service)
        from .uninstall import register_data
        register_data(service)
    except (OSError, ValueError):
        if instance:
            instance.close()
        if not args.no_browser and reopen_running(args.port, show=not args.background):
            return 0
        message = f"Plex Presence could not start. Check the data directory and whether port {args.port} is already in use."
        if sys.stderr:
            print(message, file=sys.stderr)
        if not args.no_browser:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox
                error_app = QApplication.instance() or QApplication([])
                QMessageBox.critical(None, "Plex Presence", message)
            except Exception:
                pass
        return 1
    app = None
    if not args.no_browser:
        try:
            from .native_gui import NativeApp
            app = NativeApp(service, start_hidden=args.background)
        except Exception:
            server.server_close()
            instance.close()
            if sys.stderr:
                print("The desktop window could not start. A graphical desktop and Qt support are required. Use --no-browser on a headless server.", file=sys.stderr)
            return 1
    worker = threading.Thread(target=service.run, name="presence-worker", daemon=True)
    http = threading.Thread(target=server.serve_forever, name="setup-gui", daemon=True)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (service.stop.set(), service.wake.set()))
    worker.start()
    http.start()
    tray = start_tray(service) if app else None
    if sys.stdout:
        print(f"Plex Presence {__version__}: {'desktop app' if app else service.origin}\nUse Quit to stop the app; closing the desktop window leaves it running.")
    try:
        if app:
            app.run()
        else:
            while not service.stop.wait(0.5):
                pass
    finally:
        service.stop.set()
        service.wake.set()
        worker.join(timeout=25)
        server.shutdown()
        server.server_close()
        if tray:
            tray.stop()
        instance.close()
        if app:
            app.shutdown_updates(wait=True)
            update_process = getattr(app, "update_process", None)
            if update_process is not None and update_process.stdin:
                update_process.stdin.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
