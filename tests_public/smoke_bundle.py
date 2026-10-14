"""Exercise the actual frozen HTTP app without opening a browser or signing in."""
import json
from pathlib import Path
import platform
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
if platform.system() == "Darwin":
    binary = root / "dist/PlexPresence.app/Contents/MacOS/PlexPresence"
else:
    binary = root / "dist/PlexPresence" / ("PlexPresence.exe" if platform.system() == "Windows" else "PlexPresence")
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
origin = f"http://127.0.0.1:{port}"
with tempfile.TemporaryDirectory() as directory:
    process = subprocess.Popen([str(binary), "--no-browser", "--port", str(port), "--data-dir", directory])
    try:
        deadline = time.monotonic() + 45
        while True:
            try:
                with urlopen(origin + "/api/status", timeout=2) as r:
                    status = json.load(r)
                break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Packaged app did not start")
                time.sleep(.5)
        assert not status["discord_connected"] and not status["plex_connected"]
        for path, text in (("/", b"Choose your Plex"), ("/app.js", b"api/plex/connect"), ("/style.css", b".card")):
            with urlopen(origin + path, timeout=3) as r:
                assert text in r.read(), path
        req = Request(origin + "/api/quit", data=b"{}", headers={"Content-Type": "application/json", "Origin": origin, "X-CSRF-Token": status["csrf"]})
        with urlopen(req, timeout=3) as r:
            assert json.load(r)["ok"]
        assert process.wait(timeout=15) == 0
        print("Native bundle launches, serves its GUI, starts disconnected, and exits cleanly.")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
