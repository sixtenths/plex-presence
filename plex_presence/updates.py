"""User-requested updates from the project's stable GitHub releases."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .storage import read_json, save_private

REPOSITORY = "sixtenths/plex-presence"
LATEST_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
DOWNLOAD_ROOT = "https://github.com/" + REPOSITORY + "/releases/download/"
MAX_DOWNLOAD = 512 * 1024 * 1024
ALLOWED_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class UpdateError(ValueError):
    pass


class UpdateCancelled(UpdateError):
    pass


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value))
    if not match:
        raise UpdateError("The release has an unsupported version number.")
    return tuple(int(part) for part in match.groups())


def checked_url(url):
    value = urlsplit(url)
    if (value.scheme != "https" or value.hostname not in ALLOWED_HOSTS
            or value.username or value.password or value.port not in (None, 443)):
        raise UpdateError("The update download points outside the official release hosts.")
    return url


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def platform_asset(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {"amd64":"x64", "x86_64":"x64", "arm64":"arm64", "aarch64":"arm64"}.get(machine)
    if system == "Windows" and arch == "x64":
        return "windows", "PlexPresence-Setup-windows-x64.exe"
    if system in ("Darwin", "Linux") and arch:
        name = "macos" if system == "Darwin" else "linux"
        if name == "macos":
            return name, "PlexPresence-macos-universal.dmg"
        suffix = ".zip" if name == "macos" else ".tar.gz"
        return name, "PlexPresence-" + name + "-" + arch + suffix
    return None, None


def installation_target(data_dir, system):
    if not getattr(sys, "frozen", False):
        return None, None, "Install the downloaded app to update it here. Source copies can view the latest release."
    executable = Path(sys.executable).resolve()
    if system == "macos":
        root, relative = executable.parents[2], "Contents/MacOS/PlexPresence"
        valid = root.suffix == ".app" and (root / relative).resolve() == executable
    else:
        root, relative = executable.parent, "PlexPresence.exe" if system == "windows" else "PlexPresence"
        valid = executable.name == relative and (root / "_internal").is_dir()
    if not valid:
        return None, None, "This app layout cannot be updated automatically. Download the installer or complete app archive."
    if system == "windows" and not (root / "unins000.exe").is_file():
        return None, None, "Run the Windows installer first to enable updates inside the app."
    if Path(data_dir).resolve().is_relative_to(root):
        return None, None, "Your settings are inside the app folder. Move them to a separate data folder before replacing the app."
    if not os.access(root, os.W_OK) or not os.access(root.parent, os.W_OK):
        return None, None, "This app folder is read-only. Download the update and install it in a folder you can change."
    return root, relative, ""


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UpdateManager:
    def __init__(self, data_dir, port=8765, current_version=__version__):
        self.data_dir = Path(data_dir).resolve()
        self.port = int(port)
        self.current_version = current_version
        self.system, self.asset_name = platform_asset()
        self.opener = build_opener(ReleaseRedirect())
        self.verified = {}
        self.install_started = False

    def _open(self, url):
        request = Request(checked_url(url), headers={"User-Agent":"PlexPresence/" + self.current_version,
            "Accept":"application/vnd.github+json" if url == LATEST_URL else "application/octet-stream",
            "Accept-Encoding":"identity"})
        return self.opener.open(request, timeout=20)

    def _read(self, url, maximum):
        with self._open(url) as response:
            value = response.read(maximum + 1)
        if len(value) > maximum:
            raise UpdateError("The update service returned an oversized response.")
        return value

    def check(self):
        try:
            release = json.loads(self._read(LATEST_URL, 2 * 1024 * 1024))
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                raise UpdateError("A stable release is not available right now.")
            tag = release.get("tag_name", "")
            latest = version_tuple(tag)
            current = version_tuple(self.current_version)
            assets = release.get("assets", [])
            if not isinstance(assets, list):
                raise UpdateError("The release download list is invalid.")
            matches = [a for a in assets if isinstance(a, dict) and a.get("name") == self.asset_name]
            sums = [a for a in assets if isinstance(a, dict) and a.get("name") == (self.asset_name or "") + ".sha256"]
            asset = matches[0] if len(matches) == 1 else {}
            size = asset.get("size", 0)
            compatible = bool(self.asset_name and len(matches) == 1 and len(sums) == 1
                              and type(size) is int and 0 < size <= MAX_DOWNLOAD)
            _root, _relative, reason = installation_target(self.data_dir, self.system)
            if not compatible:
                reason = "This release has no compatible download and checksum for your device yet."
            return {"current_version":self.current_version, "latest_version":".".join(map(str, latest)),
                    "tag":tag, "available":latest > current, "notes":str(release.get("body") or "")[:16000],
                    "release_url":"https://github.com/" + REPOSITORY + "/releases/tag/" + tag,
                    "asset_name":self.asset_name, "asset_size":size if compatible else 0,
                    "can_install":compatible and not reason, "reason":reason,
                    "asset_digest":str(asset.get("digest") or "")}
        except HTTPError as error:
            if error.code in (403, 429):
                raise UpdateError("GitHub is limiting update checks. Please try again later.") from None
            raise UpdateError("The latest release could not be checked. Please try again later.") from None
        except (OSError, json.JSONDecodeError, TypeError) as error:
            raise UpdateError("Could not reach GitHub. Check your connection and try again.") from error

    def _release(self, release):
        tag = release.get("tag", "")
        if version_tuple(tag) <= version_tuple(self.current_version):
            raise UpdateError("This release is not newer than your installed version.")
        if version_tuple(release.get("latest_version", "")) != version_tuple(tag):
            raise UpdateError("The update version does not match its release tag.")
        size = release.get("asset_size")
        if (not self.asset_name or release.get("asset_name") != self.asset_name
                or type(size) is not int or not 0 < size <= MAX_DOWNLOAD):
            raise UpdateError("This download does not match your device.")
        return DOWNLOAD_ROOT + tag + "/" + self.asset_name

    def download(self, release, progress_callback=None, cancel_event=None):
        def cancelled():
            if cancel_event is not None and cancel_event.is_set():
                raise UpdateCancelled("Download cancelled.")
        cancelled()
        url = self._release(release)
        folder = None
        try:
            checksum = self._read(url + ".sha256", 4096).decode("ascii").strip()
            match = re.fullmatch(r"([A-Fa-f0-9]{64})\s+\*?([^\r\n]+)", checksum)
            if not match or match[2] != self.asset_name:
                raise UpdateError("The release checksum does not match the selected download.")
            expected = match[1].lower()
            published = release.get("asset_digest", "")
            if published and published != "sha256:" + expected:
                raise UpdateError("The release checksum and GitHub's file digest disagree.")
            cancelled()
            base = self.data_dir / "updates"
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            folder = Path(tempfile.mkdtemp(prefix="download-", dir=base))
            target = folder / self.asset_name
            partial = folder / (self.asset_name + ".part")
            total = 0
            digest = hashlib.sha256()
            with self._open(url) as response, partial.open("xb") as output:
                while True:
                    cancelled()
                    block = response.read(128 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > release["asset_size"] or total > MAX_DOWNLOAD:
                        raise UpdateError("The update file exceeds its published size.")
                    output.write(block)
                    digest.update(block)
                    if progress_callback:
                        progress_callback(total, release["asset_size"])
            cancelled()
            if total != release["asset_size"] or not hmac.compare_digest(digest.hexdigest(), expected):
                raise UpdateError("The update download failed verification. Please try again.")
            partial.replace(target)
            self.verified[target.resolve()] = {"sha256":expected, "tag":release["tag"]}
            return target
        except BaseException as error:
            if folder is not None:
                (folder / (self.asset_name + ".part")).unlink(missing_ok=True)
                if not any(folder.iterdir()):
                    folder.rmdir()
            if isinstance(error, (UpdateError, KeyboardInterrupt, SystemExit)):
                raise
            raise UpdateError("The update could not be downloaded. Check your connection and free disk space.") from error

    def prepare_install(self, release, artifact):
        self._release(release)
        artifact = Path(artifact).resolve()
        verified = self.verified.get(artifact)
        if not verified or verified["tag"] != release["tag"] or file_digest(artifact) != verified["sha256"]:
            raise UpdateError("Download and verify the update before installing it.")
        root, executable, reason = installation_target(self.data_dir, self.system)
        if reason:
            raise UpdateError(reason)
        helper_name = "PlexPresenceUpdater.exe" if self.system == "windows" else "PlexPresenceUpdater"
        bundled_helper = Path(__file__).parent / "assets" / helper_name
        if not bundled_helper.is_file():
            raise UpdateError("The update helper is missing. Please reinstall the complete app.")
        helper = artifact.parent / helper_name
        shutil.copyfile(bundled_helper, helper)
        helper.chmod(0o700)
        plan = {"schema":1, "platform":self.system, "current_root":str(root), "executable":executable,
                "archive":str(artifact), "sha256":verified["sha256"], "data_dir":str(self.data_dir),
                "port":self.port, "parent_pid":os.getpid(), "plan_dir":str(artifact.parent),
                "target_version":release["latest_version"]}
        from .update_helper import validate_plan
        validate_plan(plan)
        path = artifact.parent / "plan.json"
        save_private(path, plan)
        return path

    def launch_install(self, plan_path):
        if self.install_started:
            raise UpdateError("An update is already being installed.")
        plan_path = Path(plan_path).resolve()
        plan = read_json(plan_path)
        from .update_helper import validate_plan
        validate_plan(plan)
        helper = plan_path.parent / ("PlexPresenceUpdater.exe" if self.system == "windows" else "PlexPresenceUpdater")
        ready = plan_path.parent / "ready.json"
        ready.unlink(missing_ok=True)
        options = {"stdin":subprocess.PIPE, "stdout":subprocess.DEVNULL, "stderr":subprocess.DEVNULL,
                   "cwd":str(plan_path.parent), "env":dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen([str(helper), str(plan_path)], **options)
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                value = read_json(ready)
                if value.get("ready") is True and value.get("version") == plan["target_version"]:
                    self.install_started = True
                    return process
                if process.poll() is not None:
                    raise UpdateError("The updater could not prepare the installation. Your app has not been changed.")
                time.sleep(0.1)
            raise UpdateError("The updater took too long to start. Your app has not been changed.")
        except BaseException:
            process.terminate()
            process.wait(timeout=10)
            if process.stdin:
                process.stdin.close()
            raise

    def last_result(self):
        try:
            result = read_json(self.data_dir / "updates" / "result.json")
            if result.get("state") in ("installed", "failed"):
                return result
        except (OSError, ValueError):
            pass
        return {}
