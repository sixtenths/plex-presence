"""Exercise the actual frozen HTTP app without opening a browser or signing in."""
import json
import os
from pathlib import Path
import platform
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
if platform.system() == "Darwin":
    binary = root / "dist/PlexPresence.app/Contents/MacOS/PlexPresence"
else:
    binary = root / "dist/PlexPresence/PlexPresence.exe" if platform.system() == "Windows" else root / "dist/PlexPresence/PlexPresence"
binary = Path(os.environ.get("PLEX_BUNDLE_BINARY", binary))
runtime_certificate_policy = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
if runtime_certificate_policy.is_file():
    candidates = (
        binary.parent / "_internal" / runtime_certificate_policy.name,
        binary.parent.parent / "Resources" / runtime_certificate_policy.name,
        binary.parent.parent / "Frameworks" / runtime_certificate_policy.name,
    )
    expected = runtime_certificate_policy.read_bytes()
    assert any(path.is_file() and path.read_bytes() == expected for path in candidates), (
        "The frozen app is missing the build runtime's certificate policy file; TLS initialization would fail."
    )
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
        assert not status["plex_connected"] and status["settings"]["discord_mode"] == "desktop"
        for path, text in (("/", b"Choose your Plex"), ("/app.js", b"api/plex/connect"), ("/style.css", b".card"), ("/guide.html", b"setup-guide"), ("/guide.js", b"showModal"), ("/guide.css", b"guide-body")):
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
