"""Remove installed-app data during an explicit Windows uninstall, never an upgrade."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.request import Request, urlopen

from .instance import InstanceLock
from .storage import data_directory, read_json, save_private

FILES = ('config.json', 'discord-tokens.json', 'native-ui.json', 'runtime.json', 'instance.lock')


def registry_key():
    installation = str(Path(sys.executable).resolve().parent).casefold()
    return 'Software\\PlexPresence\\DataDirectories\\' + hashlib.sha256(installation.encode()).hexdigest()


def register_data(service):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return
    if not (Path(sys.executable).parent / 'unins000.exe').is_file():
        return
    import winreg
    directory = service.directory.resolve()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_key()) as key:
        name = hashlib.sha256(str(directory).casefold().encode()).hexdigest()
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(directory))
    save_private(directory / 'runtime.json', {'port':service.port, 'csrf':service.csrf, 'pid':os.getpid()})


def registered_directories():
    import winreg
    paths = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_key()) as key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                _name, value, kind = winreg.EnumValue(key, index)
                if kind == winreg.REG_SZ and Path(value).is_absolute():
                    paths.append(Path(value))
    except FileNotFoundError:
        pass
    return paths


def remove_registry():
    import winreg
    for key in (registry_key(), r'Software\PlexPresence\DataDirectories', r'Software\PlexPresence'):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as opened:
                children, values, _time = winreg.QueryInfoKey(opened)
            if key == registry_key() or (not children and not values):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


def linked(path):
    return path.is_symlink() or junction(path)


def junction(path):
    return os.name == 'nt' and path.exists() and bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def remove_tree(path):
    # Never follow a junction/symlink into another application's or user's data.
    if path.is_symlink():
        path.unlink()
    elif junction(path):
        path.rmdir()
    elif path.exists():
        shutil.rmtree(path)


def stop_running(directory):
    if not directory.exists() or linked(directory):
        return
    try:
        lock = InstanceLock(directory)
    except OSError:
        runtime = read_json(directory / 'runtime.json')
        port = runtime.get('port')
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('Close Plex Presence before uninstalling.')
        origin = f'http://127.0.0.1:{port}'
        with urlopen(origin + '/api/window/status', timeout=5) as response:
            status = json.load(response)
        if status.get('app_id') != 'plex-presence' or status.get('csrf') != runtime.get('csrf'):
            raise ValueError('Close Plex Presence before uninstalling.')
        from .update_helper import ParentProcess
        process = ParentProcess(runtime['pid'])
        try:
            request = Request(origin + '/api/quit', data=b'{}', headers={
                'Content-Type':'application/json', 'Origin':origin, 'X-CSRF-Token':runtime['csrf']})
            with urlopen(request, timeout=5) as response:
                if not json.load(response).get('ok'):
                    raise ValueError('Close Plex Presence before uninstalling.')
            if not process.wait(40):
                raise ValueError('Plex Presence is still closing. Try uninstalling again.')
        finally:
            process.close()
        lock = InstanceLock(directory)
    lock.close()


def clean_directory(directory, default):
    directory = Path(directory).absolute()
    default = Path(default).absolute()
    if directory == default:
        # This exact, dedicated per-user folder is owned by the app.
        if directory.name != 'PlexPresence' or directory.parent == directory:
            raise ValueError('Unexpected app-data folder.')
        remove_tree(directory)
        return
    if not directory.exists():
        return
    if linked(directory):
        raise ValueError('Custom app-data folder was replaced with a link.')
    if directory.resolve() != directory:
        raise ValueError('Custom app-data folder no longer has its registered location.')
    # A custom --data-dir may contain unrelated user files. Remove our known
    # files and generated update downloads, then the directory only if empty.
    for name in FILES:
        path = directory / name
        if path.is_file() or path.is_symlink():
            path.unlink()
    updates = directory / 'updates'
    if updates.is_dir() and not linked(updates):
        allowed = {'plan.json', 'ready.json', 'PlexPresenceUpdater.exe', 'PlexPresenceUpdater'}
        from .update_helper import ASSETS
        assets = set().union(*ASSETS.values())
        allowed |= assets | {name + '.part' for name in assets}
        for child in updates.iterdir():
            if child.name == 'result.json' and child.is_file():
                child.unlink()
            elif child.name.startswith('download-') and child.is_dir() and not linked(child):
                if all(p.name in allowed and (p.is_file() or p.is_symlink()) for p in child.iterdir()):
                    remove_tree(child)
        if not any(updates.iterdir()):
            updates.rmdir()
    if not any(directory.iterdir()):
        directory.rmdir()


def uninstall_data():
    default = data_directory().absolute()
    paths = list(dict.fromkeys([default, *registered_directories()]))
    # All instances must be stopped before deleting any account data.
    for path in paths:
        stop_running(path)
    for path in paths:
        clean_directory(path, default)
    remove_registry()
