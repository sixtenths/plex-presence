"""Per-user storage, kept separate from application files and source checkouts."""
import json
import os
from pathlib import Path
import sys
import tempfile


def data_directory():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "PlexPresence"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/PlexPresence"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "plex-presence"


def save_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".save-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    if not Path(path).exists():
        return {} if default is None else default
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a JSON object.")
    return value
