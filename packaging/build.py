"""Build a native portable app on its target OS; never cross-compile."""
import hashlib
import os
import tomllib
from pathlib import Path
import platform
import shutil
import ssl
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
os_name = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[platform.system()]
arch = {"AMD64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine(), platform.machine())
command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "PlexPresence",
           "--add-data", f"{root / 'plex_presence/web'}:plex_presence/web", "--add-data", f"{root / 'LICENSE'}:.",
           "--add-data", f"{root / 'NOTICE.md'}:.", "--distpath", "dist", "--workpath", "build/pyinstaller", "--specpath", "build"]
command.extend(["--add-data", f"{root / 'plex_presence/assets'}:plex_presence/assets"])
command.extend(["--add-data", f"{root / 'THIRD_PARTY_LICENSES'}:THIRD_PARTY_LICENSES"])
# Some Python distributions load extra certificate policy data beside ssl.py.
# Keep that runtime file beside the frozen module so TLS context creation works.
runtime_certificate_policy = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
if runtime_certificate_policy.is_file():
    command.extend(["--add-data", f"{runtime_certificate_policy}:."])
if os_name in ("windows", "macos"):
    command.append("--windowed")
    command.extend(["--icon", str(root / "plex_presence/assets" / ("app.ico" if os_name == "windows" else "app.icns"))])
if os_name == "macos":
    command.extend(["--osx-bundle-identifier", "io.github.sixtenths.plexpresence"])
command.append(str(root / "run.py"))
subprocess.run(command, cwd=root, check=True)
output = root / "release"
output.mkdir(exist_ok=True)
base = output / f"PlexPresence-{os_name}-{arch}"
if os_name == "macos":
    # ditto preserves the .app bundle layout and symlinks.
    archive = Path(str(base) + ".zip")
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(root / "dist/PlexPresence.app"), str(archive)], check=True)
elif os_name == "windows":
    compiler = shutil.which("iscc") or str(Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6/ISCC.exe")
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    subprocess.run([compiler, f"/DAppVersion={version}", str(root / "packaging/windows.iss")], cwd=root, check=True)
    archive = output / "PlexPresence-Setup-windows-x64.exe"
else:
    archive = Path(shutil.make_archive(str(base), "gztar", root / "dist", "PlexPresence"))
Path(str(archive) + ".sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n", encoding="ascii")
print(archive)
