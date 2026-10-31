"""Run the GUI on Windows, macOS or Linux."""
import argparse
import json
import signal
import sys
import threading
import webbrowser
from pathlib import Path
from . import __version__
from .service import Service
from .server import make_server
from .storage import data_directory
from .instance import InstanceLock
from .tray import start_tray
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description="Plex Presence — local setup GUI, no Discord desktop client required")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Run headlessly; use an SSH tunnel to access setup")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    instance = None
    try:
        instance = InstanceLock(args.data_dir)
        service = Service(args.data_dir, args.port)
        server = make_server(service)
    except (OSError, ValueError):
        if instance:
            instance.close()
        if not args.no_browser:
            try:
                origin = f"http://127.0.0.1:{args.port}"
                with urlopen(origin + "/api/status", timeout=2) as response:
                    running = json.load(response)
                if running.get("app_id") == "plex-presence":
                    webbrowser.open(origin)
                    return 0
            except (OSError, ValueError):
                pass
        message = f"Plex Presence could not start. Check the data directory and whether port {args.port} is already in use."
        if sys.stderr:
            print(message, file=sys.stderr)
        if not args.no_browser:
            try:
                import tkinter.messagebox
                tkinter.messagebox.showerror("Plex Presence", message)
            except Exception:
                pass
        return 1
    worker = threading.Thread(target=service.run, name="presence-worker", daemon=True)
    http = threading.Thread(target=server.serve_forever, name="setup-gui", daemon=True)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (service.stop.set(), service.wake.set()))
    worker.start()
    http.start()
    tray = start_tray(service)
    if sys.stdout:
        print(f"Plex Presence {__version__}: {service.origin}\nClose the app using Quit in its GUI or Ctrl+C.")
    if not args.no_browser:
        webbrowser.open(service.origin)
    try:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
