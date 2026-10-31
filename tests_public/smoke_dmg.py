"""Install the published DMG's app into an isolated checkout and exercise it."""
import os
from pathlib import Path
import runpy
import ssl
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
dmg = root / 'release/PlexPresence-macos-universal.dmg'
subprocess.run(['hdiutil', 'verify', str(dmg)], check=True)
with tempfile.TemporaryDirectory(prefix='plex-dmg-mount-') as folder:
    mount = Path(folder) / 'volume'
    subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(dmg)], check=True)
    try:
        assert (mount / 'Applications').is_symlink()
        assert os.readlink(mount / 'Applications') == '/Applications'
        app = mount / 'PlexPresence.app'
        runpy.run_path(str(root / 'packaging/macos.py'))['verify_universal_bundle'](app)
        roots = list(app.rglob('certifi/cacert.pem'))
        assert roots, 'Universal app is missing its bundled TLS root certificates'
        assert ssl.create_default_context(cafile=str(roots[0])).cert_store_stats()['x509_ca'] > 0
        target = root / 'dist/PlexPresence.app'
        assert not target.exists(), 'DMG smoke test requires a clean checkout'
        target.parent.mkdir(exist_ok=True)
        subprocess.run(['ditto', str(app), str(target)], check=True)
    finally:
        subprocess.run(['hdiutil', 'detach', str(mount)], check=True)
for script in ('smoke_bundle.py', 'smoke_updater.py', 'smoke_native.py'):
    subprocess.run([sys.executable, str(root / 'tests_public' / script)], check=True, timeout=180)
print('The universal DMG installs, launches natively, and supports updates on this Mac architecture.')
