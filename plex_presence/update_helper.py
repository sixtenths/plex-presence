"""Standalone updater: validate a staged release, wait for exit, install and restart."""
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import posixpath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 10_000
EXECUTABLES = {"windows":"PlexPresence.exe", "macos":"Contents/MacOS/PlexPresence", "linux":"PlexPresence"}
ASSETS = {
    "windows":{"PlexPresence-Setup-windows-x64.exe"},
    "macos":{"PlexPresence-macos-x64.zip", "PlexPresence-macos-arm64.zip", "PlexPresence-macos-universal.zip"},
    "linux":{"PlexPresence-linux-x64.tar.gz", "PlexPresence-linux-arm64.tar.gz"},
}


def _absolute(value, label, exists=True):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(f"Invalid {label}.")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"The {label} must be an absolute path.")
    resolved = path.resolve(strict=exists)
    if path != resolved or path.is_symlink():
        raise ValueError(f"The {label} must not use a symbolic link.")
    return resolved


def _within(path, root):
    return path == root or root in path.parents


def _bundle(root, platform, installed=False):
    entry = root / EXECUTABLES[platform]
    if not root.is_dir() or root.is_symlink() or not entry.is_file() or entry.is_symlink():
        raise ValueError("The application folder does not contain the expected executable.")
    if platform in ("windows", "linux"):
        internal = root / "_internal"
        if not internal.is_dir() or internal.is_symlink():
            raise ValueError("The application bundle is incomplete.")
    if platform == "windows" and installed and not (root / "unins000.exe").is_file():
        raise ValueError("Install Plex Presence with its Windows installer before using automatic updates.")
    if platform == "linux" and installed:
        if any(p.name not in {"PlexPresence", "_internal"} for p in root.iterdir()):
            raise ValueError("Keep Plex Presence in its own application folder before updating it.")
    if platform == "macos":
        if root.suffix != ".app":
            raise ValueError("The application must be inside its .app bundle.")
        try:
            with (root / "Contents/Info.plist").open("rb") as stream:
                identity = plistlib.load(stream).get("CFBundleIdentifier")
        except (OSError, ValueError, plistlib.InvalidFileException) as error:
            raise ValueError("The application bundle is incomplete.") from error
        if identity != "io.github.sixtenths.plexpresence":
            raise ValueError("The application bundle has an unexpected identity.")
    return entry


def validate_plan(plan):
    """Return a normalized plan, without changing any application files."""
    if not isinstance(plan, dict) or plan.get("schema") != 1:
        raise ValueError("The update plan is not supported.")
    platform = plan.get("platform")
    if platform not in EXECUTABLES or plan.get("executable") != EXECUTABLES[platform]:
        raise ValueError("The update does not match this application layout.")
    root = _absolute(plan.get("current_root"), "application folder")
    archive = _absolute(plan.get("archive"), "downloaded update")
    data = _absolute(plan.get("data_dir"), "settings folder")
    staging = _absolute(plan.get("plan_dir"), "update staging folder")
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("The update cannot replace that folder.")
    if not data.is_dir() or not staging.is_dir() or not archive.is_file():
        raise ValueError("The update files or settings folder are unavailable.")
    if archive.parent != staging or archive.name not in ASSETS[platform]:
        raise ValueError("The downloaded file is not the expected release asset.")
    if _within(data, root) or _within(staging, root) or _within(root, staging):
        raise ValueError("Settings and update files must be outside the application folder.")
    updates = data / "updates"
    if updates.is_symlink() or not _within(updates.resolve(), data):
        raise ValueError("The update results folder must remain inside the settings folder.")
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise ValueError("The downloaded update has an invalid size.")
    if not isinstance(plan.get("sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", plan["sha256"]):
        raise ValueError("The update checksum is missing or invalid.")
    if not isinstance(plan.get("target_version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", plan["target_version"]):
        raise ValueError("The update version is invalid.")
    if type(plan.get("port")) is not int or not 1024 <= plan["port"] <= 65535:
        raise ValueError("The application port is invalid.")
    if type(plan.get("parent_pid")) is not int or plan["parent_pid"] <= 0:
        raise ValueError("The application process is invalid.")
    _bundle(root, platform, installed=True)
    return dict(plan, current_root=str(root), archive=str(archive), data_dir=str(data), plan_dir=str(staging), sha256=plan["sha256"].lower())


def _verify(plan):
    digest = hashlib.sha256()
    with Path(plan["archive"]).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != plan["sha256"]:
        raise ValueError("The downloaded update failed its checksum check. Download it again.")


def _archive_name(name, root_name):
    if not isinstance(name, str) or not name or "\0" in name or "\\" in name:
        raise ValueError("The update archive contains an unsafe path.")
    parts = PurePosixPath(name).parts
    if PurePosixPath(name).is_absolute() or any(p == ".." or ":" in p for p in parts):
        raise ValueError("The update archive contains an unsafe path.")
    if parts and parts[0] == "__MACOSX":
        return None
    if not parts or parts[0] != root_name:
        raise ValueError("The update archive has an unexpected application folder.")
    return PurePosixPath(*parts)


def _copy_member(stream, destination, size, mode):
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with destination.open("xb") as target:
        while True:
            block = stream.read(min(1024 * 1024, size - total + 1))
            if not block:
                break
            total += len(block)
            if total > size:
                raise ValueError("The update archive contains an invalid file size.")
            target.write(block)
    if total != size:
        raise ValueError("The update archive is incomplete.")
    destination.chmod(((mode or 0o644) & 0o777) & ~0o022)


def _extract(archive, destination, platform):
    """Extract regular files first, then bounded in-bundle links; never follow archive links."""
    root_name = "PlexPresence.app" if platform == "macos" else "PlexPresence"
    root = destination / root_name
    seen, links, directories, file_modes = set(), [], [], {}
    total = 0

    def register(name, size, kind, mode, link=None, stream=None):
        nonlocal total
        relative = _archive_name(name, root_name)
        if relative is None:
            return
        if relative in seen or len(seen) >= MAX_MEMBERS:
            raise ValueError("The update archive contains duplicate or too many files.")
        seen.add(relative)
        if size < 0 or total + size > MAX_EXPANDED:
            raise ValueError("The update archive is too large.")
        total += size
        target = destination.joinpath(*relative.parts)
        if kind == "directory":
            target.mkdir(parents=True, exist_ok=True)
            directories.append((target, mode))
        elif kind == "file":
            if stream is None:
                raise ValueError("The update archive is incomplete.")
            _copy_member(stream, target, size, mode)
            file_modes[relative] = mode
        elif kind in ("symlink", "hardlink"):
            if not isinstance(link, str) or not link or len(link) > 4096 or "\0" in link or "\\" in link:
                raise ValueError("The update archive contains an invalid link.")
            if PurePosixPath(link).is_absolute() or any(":" in p for p in PurePosixPath(link).parts):
                raise ValueError("The update archive contains an unsafe link.")
            linked_name = posixpath.normpath(str(relative.parent / link) if kind == "symlink" else link)
            linked = _archive_name(linked_name, root_name)
            if linked is None:
                raise ValueError("The update archive contains an unsafe link.")
            links.append((target, kind, link, destination.joinpath(*linked.parts)))
        else:
            raise ValueError("The update archive contains an unsupported file type.")

    if platform == "macos":
        with zipfile.ZipFile(archive) as package:
            if len(package.infolist()) > MAX_MEMBERS:
                raise ValueError("The update archive contains too many files.")
            for item in package.infolist():
                mode = item.external_attr >> 16
                kind = "directory" if item.is_dir() else "symlink" if stat.S_ISLNK(mode) else "file"
                if not item.is_dir() and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFLNK):
                    raise ValueError("The update archive contains an unsupported file type.")
                if kind == "symlink":
                    if item.file_size > 4096:
                        raise ValueError("The update archive contains an invalid link.")
                    with package.open(item) as stream:
                        link = stream.read(4097).decode("utf-8")
                    register(item.filename, item.file_size, kind, stat.S_IMODE(mode), link=link)
                elif kind == "file":
                    with package.open(item) as stream:
                        register(item.filename, item.file_size, kind, stat.S_IMODE(mode), stream=stream)
                else:
                    register(item.filename, 0, kind, stat.S_IMODE(mode))
    else:
        with tarfile.open(archive, "r:gz") as package:
            for count, item in enumerate(package):
                if count >= MAX_MEMBERS:
                    raise ValueError("The update archive contains too many files.")
                kind = "directory" if item.isdir() else "file" if item.isfile() else "symlink" if item.issym() else "hardlink" if item.islnk() else "unsupported"
                if kind == "file":
                    with package.extractfile(item) as stream:
                        register(item.name, item.size, kind, item.mode, stream=stream)
                else:
                    register(item.name, 0, kind, item.mode, link=item.linkname)
    # Do not allow regular-file extraction to traverse a link created by the archive.
    for linked_path, kind, _link, _linked in links:
        if kind == "symlink" and any(_within(destination.joinpath(*name.parts), linked_path) and destination.joinpath(*name.parts) != linked_path for name in seen):
            raise ValueError("The update archive puts files beneath a symbolic link.")
    for target, kind, link, linked in sorted(links, key=lambda value: value[1] == "symlink"):
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "hardlink":
            if not linked.is_file() or linked.is_symlink() or not _within(linked.resolve(), root):
                raise ValueError("The update archive contains an invalid hard link.")
            os.link(linked, target)
        else:
            os.symlink(link, target)
    for target, kind, _link, _linked in links:
        if kind == "symlink":
            try:
                resolved = target.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ValueError("The update archive contains a broken or circular link.") from error
            if not _within(resolved, root):
                raise ValueError("The update archive contains an unsafe link.")
    for folder, mode in reversed(directories):
        folder.chmod((((mode or 0o755) & 0o777) & ~0o022) | 0o700)
    _bundle(root, platform)
    if not file_modes.get(PurePosixPath(root_name) / EXECUTABLES[platform], 0) & 0o111:
        raise ValueError("The update archive does not contain an executable application.")
    return root


def _child_environment():
    environment = dict(os.environ)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for key in ("LD_LIBRARY_PATH", "LIBPATH"):
        original = environment.get(key + "_ORIG")
        if original is None:
            environment.pop(key, None)
        else:
            environment[key] = original
    if os.name == "nt":
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    return environment


def apply_update(plan):
    """Install the verified update after the caller has ensured the old process exited."""
    plan = validate_plan(plan)
    _verify(plan)
    root, archive = Path(plan["current_root"]), Path(plan["archive"])
    if plan["platform"] == "windows":
        completed = subprocess.run([str(archive), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NORESTARTAPPLICATIONS", "/DIR=" + str(root)],
                                   check=False, timeout=300, env=_child_environment())
        if completed.returncode:
            raise ValueError("The installer could not complete the update. The previous application can be opened again.")
        _bundle(root, "windows", installed=True)
        return {"backup":None}
    staging = Path(tempfile.mkdtemp(prefix=".plex-presence-update-", dir=root.parent)).resolve()
    if staging.parent != root.parent:
        raise ValueError("The update staging folder is invalid.")
    prepared = _extract(archive, staging, plan["platform"])
    backup = root.with_name(root.name + ".previous-" + uuid.uuid4().hex)
    if backup.exists() or backup.parent != root.parent:
        raise ValueError("The application backup folder is unavailable.")
    root.rename(backup)
    try:
        prepared.rename(root)
    except BaseException:
        backup.rename(root)
        raise
    # Keep the complete previous bundle for recovery. Only the now-empty staging
    # container is removed; never recursively remove a computed application path.
    try:
        staging.rmdir()
    except OSError:
        pass
    return {"backup":str(backup)}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".update-", delete=False) as stream:
        json.dump(value, stream)
        name = Path(stream.name)
    try:
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


class ParentProcess:
    def __init__(self, pid):
        self.pid, self.handle = pid, None
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            self.kernel.OpenProcess.restype = ctypes.c_void_p
            self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)
            if not self.handle and ctypes.get_last_error() != 87:
                raise ValueError("The updater could not wait for the running application.")

    def wait(self, timeout):
        if os.name == "nt":
            return not self.handle or self.kernel.WaitForSingleObject(self.handle, max(0, int(timeout * 1000))) == 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(self.pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            time.sleep(.1)
        return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _restart(plan):
    arguments = [str(Path(plan["current_root"]) / plan["executable"]), "--data-dir", plan["data_dir"], "--port", str(plan["port"])]
    options = {"cwd":plan["current_root"], "stdin":subprocess.DEVNULL, "stdout":subprocess.DEVNULL, "stderr":subprocess.DEVNULL, "env":_child_environment()}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    subprocess.Popen(arguments, **options)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        return 2
    plan, parent, stopped, outcome = None, None, False, {}
    try:
        path = _absolute(argv[0], "update plan")
        if path.stat().st_size > 32 * 1024:
            raise ValueError("The update plan is too large.")
        plan = validate_plan(json.loads(path.read_text(encoding="utf-8")))
        host = {"win32":"windows", "darwin":"macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else None)
        if plan["platform"] != host:
            raise ValueError("The update does not match this operating system.")
        if plan["parent_pid"] == os.getpid():
            raise ValueError("The updater cannot wait for itself to exit.")
        if path.parent != Path(plan["plan_dir"]):
            raise ValueError("The update plan is outside its staging folder.")
        _verify(plan)
        parent = ParentProcess(plan["parent_pid"])
        ended = threading.Event()
        def wait_pipe():
            try:
                while sys.stdin.buffer.read(1024):
                    pass
                ended.set()
            except (OSError, AttributeError):
                pass
        threading.Thread(target=wait_pipe, daemon=True).start()
        _write_json(Path(plan["plan_dir"]) / "ready.json", {"schema":1, "ready":True, "pid":os.getpid(), "version":plan["target_version"]})
        deadline = time.monotonic() + 90
        if not ended.wait(90) or not parent.wait(max(0, deadline - time.monotonic())):
            raise ValueError("The app did not finish closing. No update was installed; try again.")
        stopped = True
        outcome = apply_update(plan)
        result = {"schema":1, "state":"installed", "version":plan["target_version"], "message":"Plex Presence was updated successfully.", "timestamp":int(time.time()), **outcome}
        _write_json(Path(plan["data_dir"]) / "updates/result.json", result)
        _restart(plan)
        return 0
    except Exception as error:
        if plan:
            message = str(error) if isinstance(error, ValueError) else "The update could not be installed. Your account settings were kept. Download the latest installer or try again."
            try:
                _write_json(Path(plan["data_dir"]) / "updates/result.json", {"schema":1, "state":"failed", "version":plan["target_version"], "message":message, "timestamp":int(time.time()), **outcome})
            except OSError:
                pass
            if stopped:
                try:
                    _restart(plan)
                except OSError:
                    pass
        return 1
    finally:
        if parent:
            parent.close()


if __name__ == "__main__":
    raise SystemExit(main())
