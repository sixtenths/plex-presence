"""Windows installer install/upgrade/launch/uninstall test in an isolated folder."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import winreg

root = Path(__file__).resolve().parents[1]
key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{904AB196-A338-49D3-98D4-E038923F42A0}_is1"
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
        raise RuntimeError("Refusing installer smoke test over an existing user installation")
except FileNotFoundError:
    pass

with tempfile.TemporaryDirectory(prefix="plex-installer-test-") as directory:
    target = Path(directory) / "app"
    installer = root / "release/PlexPresence-Setup-windows-x64.exe"
    command = [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/TASKS=", f"/DIR={target}"]
    try:
        for _ in range(2):
            subprocess.run(command, check=True, timeout=120)
            assert (target / "PlexPresence.exe").is_file()
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as installed:
                assert winreg.QueryValueEx(installed, "DisplayName")[0] .startswith("Plex Presence")
        subprocess.run([sys.executable, str(root / "tests_public/smoke_bundle.py")],
            env=dict(os.environ, PLEX_BUNDLE_BINARY=str(target / "PlexPresence.exe")), check=True, timeout=90)
        subprocess.run([sys.executable, str(root / "tests_public/smoke_native.py")],
            env=dict(os.environ, PLEX_BUNDLE_BINARY=str(target / "PlexPresence.exe")), check=True, timeout=90)
        sys.path.insert(0, str(root))
        from updater_integration import verify_installed_update
        verify_installed_update(root, target, installer, directory)
    finally:
        uninstaller = target / "unins000.exe"
        if uninstaller.exists():
            subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=90)
    assert not (target / "PlexPresence.exe").exists()
print("Windows installer installs, upgrades, launches and uninstalls successfully.")

