import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from plex_presence import update_helper as helper


class UpdateHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()

    def plan(self, platform="linux"):
        root = self.base / ("PlexPresence.app" if platform == "macos" else "PlexPresence")
        root.mkdir(exist_ok=True)
        entry = root / helper.EXECUTABLES[platform]
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(b"old application")
        if platform == "macos":
            (root / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":"io.github.sixtenths.plexpresence"}))
        else:
            (root / "_internal").mkdir(exist_ok=True)
        if platform == "windows":
            (root / "unins000.exe").write_bytes(b"installer marker")
        data, staging = self.base / "settings", self.base / "staged"
        data.mkdir(exist_ok=True)
        staging.mkdir(exist_ok=True)
        (data / "config.json").write_text('{"private":"keep me"}')
        name = sorted(helper.ASSETS[platform])[0]
        archive = staging / name
        archive.write_bytes(b"staged asset")
        return {"schema":1, "platform":platform, "current_root":str(root), "executable":helper.EXECUTABLES[platform],
                "archive":str(archive), "sha256":hashlib.sha256(archive.read_bytes()).hexdigest(), "data_dir":str(data),
                "port":8790, "parent_pid":os.getpid() + 100000, "plan_dir":str(staging), "target_version":"0.3.1"}

    def tar(self, plan, extra=()):
        members = [("PlexPresence/PlexPresence", b"new application", 0o755, "file", ""),
                   ("PlexPresence/_internal/library", b"new library", 0o644, "file", ""), *extra]
        with tarfile.open(plan["archive"], "w:gz") as archive:
            for name, content, mode, kind, link in members:
                item = tarfile.TarInfo(name)
                item.mode = mode
                item.size = len(content) if kind == "file" else 0
                item.type = {"file":tarfile.REGTYPE, "symlink":tarfile.SYMTYPE, "hardlink":tarfile.LNKTYPE}[kind]
                item.linkname = link
                archive.addfile(item, io.BytesIO(content) if kind == "file" else None)
        plan["sha256"] = hashlib.sha256(Path(plan["archive"]).read_bytes()).hexdigest()

    def test_validation_rejects_root_settings_overlap_and_wrong_asset(self):
        plan = self.plan()
        for change in ({"current_root":str(Path.home().resolve())}, {"current_root":str(Path(plan["current_root"]).anchor)},
                       {"data_dir":plan["current_root"]}, {"port":True}, {"target_version":"../bad"}, {"executable":"elsewhere"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                helper.validate_plan(dict(plan, **change))
        other = self.base / "other.tar.gz"
        other.write_bytes(b"asset")
        with self.assertRaises(ValueError):
            helper.validate_plan(dict(plan, archive=str(other)))

    def test_windows_requires_installed_app_marker(self):
        plan = self.plan("windows")
        (Path(plan["current_root"]) / "unins000.exe").unlink()
        with self.assertRaisesRegex(ValueError, "installer"):
            helper.validate_plan(plan)

    def test_linux_refuses_folder_with_unrelated_user_files(self):
        plan = self.plan()
        (Path(plan["current_root"]) / "personal.txt").write_text("not application data")
        with self.assertRaisesRegex(ValueError, "own application folder"):
            helper.validate_plan(plan)

    def test_bad_checksum_cannot_invoke_installer_or_change_app(self):
        plan = self.plan("windows")
        plan["sha256"] = "0" * 64
        with patch.object(helper.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "checksum"):
            helper.apply_update(plan)
        run.assert_not_called()
        self.assertEqual((Path(plan["current_root"]) / "PlexPresence.exe").read_bytes(), b"old application")

    def test_installer_uses_exact_existing_folder_and_keeps_settings(self):
        plan = self.plan("windows")
        with patch.object(helper.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, patch.object(helper, "_child_environment", return_value={}):
            self.assertEqual(helper.apply_update(plan), {"backup":None})
        command = run.call_args.args[0]
        self.assertEqual(command[0], plan["archive"])
        self.assertIn("/DIR=" + plan["current_root"], command)
        self.assertIn("/NORESTART", command)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual((Path(plan["data_dir"]) / "config.json").read_text(), '{"private":"keep me"}')

    def test_installer_failure_is_reported(self):
        plan = self.plan("windows")
        with patch.object(helper.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), patch.object(helper, "_child_environment", return_value={}), self.assertRaisesRegex(ValueError, "could not complete"):
            helper.apply_update(plan)

    def test_archive_swap_keeps_previous_bundle_and_settings(self):
        plan = self.plan()
        self.tar(plan)
        result = helper.apply_update(plan)
        self.assertEqual((Path(plan["current_root"]) / "PlexPresence").read_bytes(), b"new application")
        self.assertEqual((Path(result["backup"]) / "PlexPresence").read_bytes(), b"old application")
        self.assertEqual((Path(plan["data_dir"]) / "config.json").read_text(), '{"private":"keep me"}')

    def test_failed_swap_rolls_back_old_bundle(self):
        plan = self.plan()
        self.tar(plan)
        rename = Path.rename
        def fail_prepared(path, target):
            if path.parent.name.startswith(".plex-presence-update-"):
                raise OSError("simulated replacement failure")
            return rename(path, target)
        with patch.object(Path, "rename", fail_prepared), self.assertRaises(OSError):
            helper.apply_update(plan)
        self.assertEqual((Path(plan["current_root"]) / "PlexPresence").read_bytes(), b"old application")

    def test_traversal_absolute_paths_and_escaping_links_are_rejected(self):
        plan = self.plan()
        for entry in (("../escape", b"bad", 0o644, "file", ""), ("/absolute", b"bad", 0o644, "file", ""),
                      ("PlexPresence/link", b"", 0o777, "symlink", "../../escape"),
                      ("PlexPresence/link", b"", 0o777, "hardlink", "../escape")):
            with self.subTest(entry=entry):
                self.tar(plan, [entry])
                with self.assertRaises(ValueError):
                    helper.apply_update(plan)
                self.assertEqual((Path(plan["current_root"]) / "PlexPresence").read_bytes(), b"old application")
        self.assertFalse((self.base / "escape").exists())

    def test_no_archive_members_can_be_beneath_a_symlink(self):
        plan = self.plan()
        self.tar(plan, [("PlexPresence/link", b"", 0o777, "symlink", "_internal"),
                        ("PlexPresence/link/payload", b"bad", 0o644, "file", "")])
        with self.assertRaisesRegex(ValueError, "beneath a symbolic link"):
            helper.apply_update(plan)

    def test_expansion_limit_is_enforced_before_swap(self):
        plan = self.plan()
        self.tar(plan)
        with patch.object(helper, "MAX_EXPANDED", 2), self.assertRaisesRegex(ValueError, "too large"):
            helper.apply_update(plan)
        self.assertEqual((Path(plan["current_root"]) / "PlexPresence").read_bytes(), b"old application")

    def test_macos_zip_requires_application_identity_and_preserves_modes(self):
        plan = self.plan("macos")
        plan["archive"] = str(Path(plan["plan_dir"]) / "PlexPresence-macos-universal.zip")
        values = {"PlexPresence.app/Contents/MacOS/PlexPresence":(b"new application", 0o4755),
                  "PlexPresence.app/Contents/Info.plist":(plistlib.dumps({"CFBundleIdentifier":"io.github.sixtenths.plexpresence"}), 0o644)}
        with zipfile.ZipFile(plan["archive"], "w") as archive:
            for name, (content, mode) in values.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, content)
        plan["sha256"] = hashlib.sha256(Path(plan["archive"]).read_bytes()).hexdigest()
        helper.apply_update(plan)
        entry = Path(plan["current_root"]) / plan["executable"]
        self.assertEqual(entry.read_bytes(), b"new application")
        self.assertFalse(entry.stat().st_mode & stat.S_ISUID)

    @unittest.skipIf(os.name == "nt", "Creating symlinks can require Windows administrator rights")
    def test_valid_internal_archive_symlink_survives(self):
        plan = self.plan()
        self.tar(plan, [("PlexPresence/_internal/alias", b"", 0o777, "symlink", "library")])
        helper.apply_update(plan)
        linked = Path(plan["current_root"]) / "_internal/alias"
        self.assertTrue(linked.is_symlink())
        self.assertEqual(linked.read_bytes(), b"new library")

    def test_handshake_result_and_restart_happen_after_parent_exit(self):
        platform = {"win32":"windows", "darwin":"macos"}.get(sys.platform, "linux")
        plan = self.plan(platform)
        path = Path(plan["plan_dir"]) / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        with patch.object(helper, "ParentProcess") as parent, patch.object(helper, "apply_update", return_value={"backup":None}) as apply, patch.object(helper, "_restart") as restart, patch.object(helper.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b""))):
            parent.return_value.wait.return_value = True
            self.assertEqual(helper.main([str(path)]), 0)
        self.assertTrue(json.loads((path.parent / "ready.json").read_text())["ready"])
        self.assertEqual(json.loads((Path(plan["data_dir"]) / "updates/result.json").read_text())["state"], "installed")
        apply.assert_called_once()
        restart.assert_called_once()
        parent.return_value.close.assert_called_once()
