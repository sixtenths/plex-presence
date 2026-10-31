"""Build a universal drag-to-Applications DMG, also used by the updater."""
import hashlib
from pathlib import Path
import subprocess
import tempfile

MACHO = {bytes.fromhex(value) for value in ('feedface','feedfacf','cefaedfe','cffaedfe','cafebabe','bebafeca','cafebabf','bfbafeca')}


def verify_universal_bundle(app):
    count = 0
    for path in Path(app).rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            magic = stream.read(4)
        if magic in MACHO:
            subprocess.run(['lipo', str(path), '-verify_arch', 'arm64', 'x86_64'], check=True)
            count += 1
    if count < 3:
        raise RuntimeError('The universal app is missing its native runtime binaries')
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print(f'Verified both architectures in {count} native binaries.')


def package_macos(root):
    app = root / 'dist/PlexPresence.app'
    output = root / 'release'
    verify_universal_bundle(app)
    dmg = output / 'PlexPresence-macos-universal.dmg'
    with tempfile.TemporaryDirectory(prefix='plex-dmg-') as folder:
        stage = Path(folder)
        subprocess.run(['ditto', str(app), str(stage / app.name)], check=True)
        (stage / 'Applications').symlink_to('/Applications', target_is_directory=True)
        (stage / 'Install Plex Presence.txt').write_text(
            'Drag PlexPresence.app to Applications, then eject this disk image.\n'
            'Open Plex Presence from Applications to start setup.\n'
            'This app works natively on Apple Silicon and Intel Macs.\n', encoding='utf-8')
        subprocess.run(['hdiutil', 'create', '-ov', '-volname', 'Plex Presence', '-fs', 'HFS+',
                        '-srcfolder', str(stage), '-format', 'UDZO', str(dmg)], check=True)
    Path(str(dmg) + '.sha256').write_text(hashlib.sha256(dmg.read_bytes()).hexdigest() + '  ' + dmg.name + '\n', encoding='ascii')
    return dmg
