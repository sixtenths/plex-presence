"""Windows installer install/upgrade/launch/uninstall test in an isolated folder."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import winreg
import json
import socket
import time
from unittest.mock import patch
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{904AB196-A338-49D3-98D4-E038923F42A0}_is1"
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
        raise RuntimeError("Refusing installer smoke test over an existing user installation")
except FileNotFoundError:
    pass

with tempfile.TemporaryDirectory(prefix="plex-installer-test-") as directory:
    # Never allow a destructive-uninstall test to see real user app data.
    os.environ['LOCALAPPDATA'] = str(Path(directory) / 'isolated-local-appdata')
    default_data = Path(os.environ['LOCALAPPDATA']) / 'PlexPresence'
    default_data.mkdir(parents=True)
    (default_data / 'config.json').write_text('{"fixture":"keep during upgrade"}')
    (default_data / 'discord-tokens.json').write_text('{"fixture":"dummy credentials"}')
    (default_data / 'updates/download-fixture').mkdir(parents=True)
    (default_data / 'updates/download-fixture/package.part').write_bytes(b'dummy download')
    target = Path(directory) / "app"
    installer = root / "release/PlexPresence-Setup-windows-x64.exe"
    command = [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/TASKS=", f"/DIR={target}"]
    running = None
    try:
        for _ in range(2):
            subprocess.run(command, check=True, timeout=120)
            assert (target / "PlexPresence.exe").is_file()
            assert (default_data / 'discord-tokens.json').is_file(), 'Upgrade deleted saved accounts'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as installed:
                assert winreg.QueryValueEx(installed, "DisplayName")[0] .startswith("Plex Presence")
        subprocess.run([sys.executable, str(root / "tests_public/smoke_bundle.py")],
            env=dict(os.environ, PLEX_BUNDLE_BINARY=str(target / "PlexPresence.exe")), check=True, timeout=90)
        subprocess.run([sys.executable, str(root / "tests_public/smoke_native.py")],
            env=dict(os.environ, PLEX_BUNDLE_BINARY=str(target / "PlexPresence.exe")), check=True, timeout=90)
        sys.path.insert(0, str(root))
        from updater_integration import verify_installed_update
        verify_installed_update(root, target, installer, directory)
        custom = Path(directory) / 'running-custom-data'
        custom.mkdir()
        (custom / 'personal.txt').write_text('keep unrelated files')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        running = subprocess.Popen([str(target / 'PlexPresence.exe'), '--no-browser', '--data-dir', str(custom), '--port', str(port)])
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(f'http://127.0.0.1:{port}/api/status', timeout=2) as response:
                    assert json.load(response)['app_id'] == 'plex-presence'
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError('Uninstall fixture did not start')
                time.sleep(.2)
    finally:
        uninstaller = target / "unins000.exe"
        if uninstaller.exists():
            subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=90)
        if running is not None:
            assert running.wait(timeout=10) == 0, 'Uninstall did not close the running app'
    assert not (target / "PlexPresence.exe").exists()
    assert not default_data.exists(), 'Uninstall left default app data behind'
    assert not (Path(directory) / 'update-settings/config.json').exists(), 'Uninstall left custom account data behind'
    assert not (Path(directory) / 'update-settings/native-ui.json').exists(), 'Uninstall left window preferences behind'
    assert not (custom / 'config.json').exists()
    assert not (custom / 'runtime.json').exists()
    assert (custom / 'personal.txt').read_text() == 'keep unrelated files'
    from plex_presence.uninstall import registry_key
    with patch('plex_presence.uninstall.sys.executable', str(target / 'PlexPresence.exe')):
        data_key = registry_key()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, data_key):
            raise AssertionError('Uninstall left data-directory registry entries behind')
    except FileNotFoundError:
        pass
print("Installer upgrades preserve accounts; uninstall removes default and tracked custom app data.")

