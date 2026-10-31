"""Exercise the copied frozen helper and exit handshake using a harmless invalid asset."""
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
host = {"Windows":"windows", "Darwin":"macos", "Linux":"linux"}[platform.system()]
helper_name = "PlexPresenceUpdater.exe" if host == "windows" else "PlexPresenceUpdater"
candidates = [root / "dist/PlexPresence/_internal/plex_presence/assets" / helper_name,
              root / "dist/PlexPresence.app/Contents/Resources/plex_presence/assets" / helper_name,
              root / "dist/PlexPresence.app/Contents/Frameworks/plex_presence/assets" / helper_name]
bundled = next((path for path in candidates if path.is_file()), None)
assert bundled is not None, "The application does not include its update helper"

child = r'''
import json, os, subprocess, sys, time
from pathlib import Path
path = Path(sys.argv[1])
plan = json.loads(path.read_text())
plan['parent_pid'] = os.getpid()
path.write_text(json.dumps(plan))
environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
options = {'stdin':subprocess.PIPE, 'stdout':subprocess.DEVNULL, 'stderr':subprocess.DEVNULL, 'env':environment}
if os.name == 'nt':
    options['creationflags'] = subprocess.CREATE_NO_WINDOW
process = subprocess.Popen([sys.argv[2], str(path)], **options)
deadline = time.monotonic() + 45
while time.monotonic() < deadline:
    ready = path.parent / 'ready.json'
    if ready.exists():
        value = json.loads(ready.read_text())
        assert value['ready'] and value['version'] == plan['target_version']
        # OS exit closes stdin; no parent-managed helper libraries remain open.
        os._exit(0)
    if process.poll() is not None:
        raise RuntimeError('Frozen updater exited before its ready handshake')
    time.sleep(.1)
process.terminate()
raise RuntimeError('Frozen updater did not report ready')
'''

with tempfile.TemporaryDirectory(prefix="plex-updater-smoke-") as folder:
    base = Path(folder).resolve()
    current = base / ("PlexPresence.app" if host == "macos" else "PlexPresence")
    entry = {"windows":"PlexPresence.exe", "macos":"Contents/MacOS/PlexPresence", "linux":"PlexPresence"}[host]
    executable = current / entry
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"This is a harmless test fixture, not an executable.")
    if host == "macos":
        (current / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":"io.github.sixtenths.plexpresence"}))
    else:
        (current / "_internal").mkdir()
    if host == "windows":
        (current / "unins000.exe").write_bytes(b"test installation marker")
    data, staged = base / "settings", base / "staged"
    data.mkdir()
    staged.mkdir()
    (data / "config.json").write_text('{"fixture":"preserve"}')
    copied = staged / helper_name
    shutil.copy2(bundled, copied)
    copied.chmod(0o700)
    asset = staged / {"windows":"PlexPresence-Setup-windows-x64.exe", "macos":"PlexPresence-macos-x64.zip", "linux":"PlexPresence-linux-x64.tar.gz"}[host]
    asset.write_bytes(b"Deliberately invalid fixture: never install or execute an actual update.")
    plan = {"schema":1, "platform":host, "current_root":str(current), "executable":entry,
            "archive":str(asset), "sha256":hashlib.sha256(asset.read_bytes()).hexdigest(), "data_dir":str(data),
            "port":8790, "parent_pid":1, "plan_dir":str(staged), "target_version":"0.3.1"}
    path = staged / "plan.json"
    path.write_text(json.dumps(plan))
    subprocess.run([sys.executable, "-c", child, str(path), str(copied)], check=True, timeout=55)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        result = data / "updates/result.json"
        if result.exists():
            value = json.loads(result.read_text())
            assert value["state"] == "failed", value
            break
        time.sleep(.1)
    else:
        raise RuntimeError("Updater did not handle the invalid fixture after its parent exited")
    assert executable.read_bytes().startswith(b"This is a harmless test fixture")
    assert (data / "config.json").read_text() == '{"fixture":"preserve"}'
    # Allow the independent helper to finish its failed-restart cleanup before
    # TemporaryDirectory removes this test's private staging directory.
    for _ in range(50):
        try:
            copied.unlink()
            break
        except PermissionError:
            time.sleep(.1)
    else:
        raise RuntimeError("Updater did not exit after reporting a failed installation")
print("Frozen updater runs outside the app bundle, waits for parent exit, and preserves app/settings on a failed update.")

if host != "windows":
    # Exercise the actual release archive (including Qt's symlinks and modes),
    # replacing only a fresh copy inside this test's temporary directory.
    sys.path.insert(0, str(root))
    from plex_presence.update_helper import apply_update
    from plex_presence import __version__
    architecture = {"arm64":"arm64", "aarch64":"arm64", "x86_64":"x64"}[platform.machine()]
    suffix = ".zip" if host == "macos" else ".tar.gz"
    release_asset = root / "release" / ("PlexPresence-" + host + "-" + architecture + suffix)
    with tempfile.TemporaryDirectory(prefix="plex-archive-update-") as folder:
        base = Path(folder).resolve()
        name = "PlexPresence.app" if host == "macos" else "PlexPresence"
        current = base / name
        shutil.copytree(root / "dist" / name, current, symlinks=True)
        data, staged = base / "settings", base / "staged"
        data.mkdir()
        staged.mkdir()
        asset = staged / release_asset.name
        shutil.copy2(release_asset, asset)
        entry = "Contents/MacOS/PlexPresence" if host == "macos" else "PlexPresence"
        plan = {"schema":1, "platform":host, "current_root":str(current), "executable":entry,
                "archive":str(asset), "sha256":hashlib.sha256(asset.read_bytes()).hexdigest(),
                "data_dir":str(data), "port":8790, "parent_pid":os.getpid(),
                "plan_dir":str(staged), "target_version":__version__}
        outcome = apply_update(plan)
        assert Path(outcome["backup"]).is_dir()
        subprocess.run([sys.executable, str(root / "tests_public/smoke_bundle.py")], check=True, timeout=90,
                       env=dict(os.environ, PLEX_BUNDLE_BINARY=str(current / entry)))
    print("Actual release archive replaces a copied installation and the updated bundle launches successfully.")
