"""Launch the frozen native window, reveal it, reopen it and quit cleanly."""
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
binary = root / ("dist/PlexPresence.app/Contents/MacOS/PlexPresence" if platform.system() == "Darwin" else
                 "dist/PlexPresence/PlexPresence.exe" if platform.system() == "Windows" else "dist/PlexPresence/PlexPresence")
binary = Path(os.environ.get("PLEX_BUNDLE_BINARY", binary))
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
origin = f"http://127.0.0.1:{port}"

def status():
    with urlopen(origin + "/api/window/status", timeout=5) as r:
        return json.load(r)

def post(path, csrf):
    request = Request(origin + path, data=b"{}", headers={"Content-Type":"application/json", "Origin":origin, "X-CSRF-Token":csrf})
    with urlopen(request, timeout=5) as r:
        assert json.load(r)["ok"]

with tempfile.TemporaryDirectory() as directory:
    args = [str(binary), "--port", str(port), "--data-dir", directory]
    process = subprocess.Popen(args + ["--background"])
    try:
        deadline = time.monotonic() + 45
        while True:
            try:
                value = status()
                if value["desktop_window"]:
                    break
            except OSError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Native window did not initialize")
            time.sleep(.3)
        assert value["window_visible"] is False
        post("/api/window/show", value["csrf"])
        deadline = time.monotonic() + 10
        while not status()["window_visible"]:
            if time.monotonic() > deadline:
                raise RuntimeError("Native window did not appear")
            time.sleep(.2)
        subprocess.run(args, check=True, timeout=15)
        with urlopen(origin + "/?plex=1", timeout=3) as r:
            page = r.read()
            assert b"Return to Plex Presence" in page and b"Choose your Plex" not in page
        post("/api/quit", value["csrf"])
        assert process.wait(timeout=25) == 0
        print("Frozen native window initializes hidden, opens, reopens through its shortcut, and quits cleanly.")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
