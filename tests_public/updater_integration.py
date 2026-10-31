"""Real installer handoff against the isolated installation owned by smoke_installer."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen


def verify_installed_update(root, target, installer, directory):
    data = Path(directory).resolve() / "update-settings"
    staged = data / "updates" / "integration"
    staged.mkdir(parents=True)
    # This is intentionally the same built version: exercise real installation,
    # shutdown and restart without publishing a pretend release or using accounts.
    from plex_presence import __version__
    config = {"discord_mode":"desktop", "use_plex_artwork":False, "fixture":"preserve"}
    (data / "config.json").write_text(json.dumps(config))
    archive = staged / installer.name
    shutil.copy2(installer, archive)
    helper = staged / "PlexPresenceUpdater.exe"
    shutil.copy2(target / "_internal/plex_presence/assets/PlexPresenceUpdater.exe", helper)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    plan = {"schema":1, "platform":"windows", "current_root":str(target.resolve()),
            "executable":"PlexPresence.exe", "archive":str(archive),
            "sha256":hashlib.sha256(archive.read_bytes()).hexdigest(), "data_dir":str(data),
            "port":port, "parent_pid":1, "plan_dir":str(staged), "target_version":__version__}
    path = staged / "plan.json"
    path.write_text(json.dumps(plan))
    child = '''
import json, os, sys
from pathlib import Path
from plex_presence.updates import UpdateManager
path = Path(sys.argv[1])
plan = json.loads(path.read_text())
plan['parent_pid'] = os.getpid()
path.write_text(json.dumps(plan))
manager = UpdateManager(plan['data_dir'], plan['port'])
process = manager.launch_install(path)
assert process.poll() is None
# Only exit releases stdin; the helper must wait for this process to finish.
os._exit(0)
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    subprocess.run([sys.executable, "-c", child, str(path)], cwd=root,
                   env=environment, check=True, timeout=50)
    origin = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 180
    status = None
    while time.monotonic() < deadline:
        try:
            with urlopen(origin + "/api/status", timeout=2) as response:
                status = json.load(response)
            break
        except OSError:
            time.sleep(.3)
    try:
        assert status is not None, "Updated native app did not restart"
        result = json.loads((data / "updates/result.json").read_text())
        assert result["state"] == "installed", result
        assert result["version"] == __version__
        assert status["settings"]["use_plex_artwork"] is False
        assert json.loads((data / "config.json").read_text())["fixture"] == "preserve"
    finally:
        if status is not None:
            # Renaming an executable is permitted while Windows still has it
            # mapped. Wait on the actual process handle before uninstalling.
            found = subprocess.check_output(["powershell", "-NoProfile", "-Command",
                "@(Get-Process PlexPresence -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $env:PLEX_TEST_BINARY } | ForEach-Object { $_.Id }) | ConvertTo-Json -Compress"],
                env=dict(os.environ, PLEX_TEST_BINARY=str(target / "PlexPresence.exe")), text=True)
            pids = json.loads(found) if found.strip() else []
            if isinstance(pids, int):
                pids = [pids]
            assert pids, "Cannot identify the isolated app process"
            from plex_presence.update_helper import ParentProcess
            processes = [ParentProcess(pid) for pid in pids]
            request = Request(origin + "/api/quit", data=b"{}", headers={
                "Content-Type":"application/json", "Origin":origin, "X-CSRF-Token":status["csrf"]})
            with urlopen(request, timeout=5) as response:
                assert json.load(response)["ok"]
            try:
                assert all(process.wait(35) for process in processes), "Updated app did not exit cleanly"
            finally:
                for process in processes:
                    process.close()
    print("Real updater installed, restarted the native app, preserved settings, and exited cleanly.")
