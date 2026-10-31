import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from plex_presence.updates import (UpdateCancelled, UpdateError, UpdateManager, checked_url,
                                   installation_target, platform_asset, version_tuple)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = UpdateManager(self.temporary.name, current_version="0.2.0")
        self.manager.system = "windows"
        self.manager.asset_name = "PlexPresence-Setup-windows-x64.exe"
        self.payload = b"verified release content"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.release = {"tag":"v0.3.0", "latest_version":"0.3.0", "asset_name":self.manager.asset_name,
                        "asset_size":len(self.payload), "asset_digest":"sha256:" + self.digest}

    def checksummed_responses(self, payload=None, checksum=None):
        return [io.BytesIO(checksum or (self.digest + "  " + self.manager.asset_name + "\n").encode()),
                io.BytesIO(self.payload if payload is None else payload)]

    def metadata(self):
        return {"tag_name":"v0.3.0", "draft":False, "prerelease":False, "body":"Release notes",
                "assets":[{"name":self.manager.asset_name, "size":len(self.payload), "digest":"sha256:" + self.digest},
                          {"name":self.manager.asset_name + ".sha256", "size":100}]}

    def test_numeric_versions_and_supported_assets(self):
        self.assertGreater(version_tuple("v0.10.0"), version_tuple("0.9.8"))
        for value in ("v1.2.3-beta", "../1.2.3", "1.2", "1.2.3/other"):
            with self.subTest(value=value), self.assertRaises(UpdateError):
                version_tuple(value)
        self.assertEqual(platform_asset("Windows", "AMD64")[1], self.manager.asset_name)
        self.assertEqual(platform_asset("Darwin", "arm64")[1], "PlexPresence-macos-arm64.zip")
        self.assertEqual(platform_asset("Linux", "aarch64")[1], "PlexPresence-linux-arm64.tar.gz")
        self.assertEqual(platform_asset("Linux", "armv7l"), (None, None))

    def test_only_official_https_download_hosts_are_allowed(self):
        for value in ("http://github.com/file", "https://github.com.evil.test/file", "https://user:pass@github.com/file", "https://github.com:444/file"):
            with self.subTest(value=value), self.assertRaises(UpdateError):
                checked_url(value)
        self.assertEqual(checked_url("https://release-assets.githubusercontent.com/file"), "https://release-assets.githubusercontent.com/file")

    def test_check_compares_versions_and_does_not_trust_metadata_urls(self):
        metadata = self.metadata()
        metadata.update(html_url="https://wrong.example", body="<script>plain notes</script>")
        metadata["assets"][0]["browser_download_url"] = "https://wrong.example/file"
        with patch.object(self.manager, "_read", return_value=json.dumps(metadata).encode()), patch("plex_presence.updates.installation_target", return_value=(Path("app"), "PlexPresence.exe", "")):
            release = self.manager.check()
        self.assertTrue(release["available"])
        self.assertTrue(release["can_install"])
        self.assertEqual(release["notes"], "<script>plain notes</script>")
        self.assertEqual(release["release_url"], "https://github.com/sixtenths/plex-presence/releases/tag/v0.3.0")

    def test_prerelease_and_missing_checksum_cannot_install(self):
        metadata = self.metadata()
        metadata["prerelease"] = True
        with patch.object(self.manager, "_read", return_value=json.dumps(metadata).encode()), self.assertRaises(UpdateError):
            self.manager.check()
        metadata["prerelease"] = False
        metadata["assets"].pop()
        with patch.object(self.manager, "_read", return_value=json.dumps(metadata).encode()):
            self.assertFalse(self.manager.check()["can_install"])

    def test_source_copy_is_check_only(self):
        with patch("plex_presence.updates.sys.frozen", False, create=True):
            root, entry, reason = installation_target(self.temporary.name, "windows")
        self.assertIsNone(root)
        self.assertIn("Source copies", reason)

    def test_download_verifies_exact_bytes_and_canonical_url(self):
        progress = Mock()
        with patch.object(self.manager, "_open", side_effect=self.checksummed_responses()) as request:
            path = self.manager.download(dict(self.release, url="https://wrong.example/file"), progress)
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertEqual(self.manager.verified[path.resolve()]["sha256"], self.digest)
        self.assertTrue(all(call.args[0].startswith("https://github.com/sixtenths/plex-presence/releases/download/v0.3.0/") for call in request.call_args_list))
        progress.assert_called_once_with(len(self.payload), len(self.payload))

    def test_download_rejects_corruption_and_cleans_partial_file(self):
        for payload in (b"wrong", self.payload + b"extra"):
            with self.subTest(payload=payload), patch.object(self.manager, "_open", side_effect=self.checksummed_responses(payload)), self.assertRaises(UpdateError):
                self.manager.download(self.release)
        self.assertEqual(list((Path(self.temporary.name) / "updates").iterdir()), [])
        self.assertFalse(self.manager.verified)

    def test_checksum_filename_and_github_digest_must_match(self):
        checksum = (self.digest + "  another.exe\n").encode()
        with patch.object(self.manager, "_open", side_effect=self.checksummed_responses(checksum=checksum)) as request, self.assertRaises(UpdateError):
            self.manager.download(self.release)
        self.assertEqual(request.call_count, 1)
        with patch.object(self.manager, "_open", side_effect=self.checksummed_responses()) as request, self.assertRaises(UpdateError):
            self.manager.download(dict(self.release, asset_digest="sha256:" + "0" * 64))
        self.assertEqual(request.call_count, 1)

    def test_cancel_before_or_during_download_never_marks_verified(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(self.manager, "_open") as request, self.assertRaises(UpdateCancelled):
            self.manager.download(self.release, cancel_event=cancel)
        request.assert_not_called()
        cancel.clear()
        with patch.object(self.manager, "_open", side_effect=self.checksummed_responses()), self.assertRaises(UpdateCancelled):
            self.manager.download(self.release, lambda *_: cancel.set(), cancel)
        self.assertFalse(self.manager.verified)
        self.assertEqual(list((Path(self.temporary.name) / "updates").iterdir()), [])

    def test_download_rejects_downgrades_and_other_platform(self):
        for release in (dict(self.release, tag="v0.1.0"), dict(self.release, asset_name="PlexPresence-linux-x64.tar.gz")):
            with patch.object(self.manager, "_open") as request, self.assertRaises(UpdateError):
                self.manager.download(release)
            request.assert_not_called()

    def test_prepare_rejects_unverified_or_changed_file(self):
        path = Path(self.temporary.name) / self.manager.asset_name
        path.write_bytes(self.payload)
        with self.assertRaises(UpdateError):
            self.manager.prepare_install(self.release, path)
        self.manager.verified[path.resolve()] = {"sha256":self.digest, "tag":"v0.3.0"}
        path.write_bytes(b"changed")
        with self.assertRaises(UpdateError):
            self.manager.prepare_install(self.release, path)

    def test_rate_limit_has_actionable_error(self):
        with patch.object(self.manager, "_read", side_effect=HTTPError("https://api.github.com", 403, "", {}, None)), self.assertRaisesRegex(UpdateError, "limiting"):
            self.manager.check()

    def test_prepare_and_launch_verified_update_waits_for_helper_readiness(self):
        base = Path(self.temporary.name).resolve()
        app = base / "app"
        (app / "_internal").mkdir(parents=True)
        (app / "PlexPresence.exe").write_bytes(b"fixture")
        (app / "unins000.exe").write_bytes(b"fixture")
        assets = base / "source/assets"
        assets.mkdir(parents=True)
        (assets / "PlexPresenceUpdater.exe").write_bytes(b"helper fixture")
        with patch.object(self.manager, "_open", side_effect=self.checksummed_responses()):
            artifact = self.manager.download(self.release)
        with patch("plex_presence.updates.installation_target", return_value=(app, "PlexPresence.exe", "")), patch("plex_presence.updates.__file__", str(base / "source/updates.py")):
            path = self.manager.prepare_install(self.release, artifact)
        plan = json.loads(path.read_text())
        self.assertEqual(plan["target_version"], "0.3.0")
        self.assertEqual(plan["data_dir"], str(base))
        self.assertEqual((path.parent / "PlexPresenceUpdater.exe").read_bytes(), b"helper fixture")
        process = Mock()
        with patch("plex_presence.updates.subprocess.Popen", return_value=process) as launch, patch("plex_presence.updates.read_json", side_effect=[plan, {"ready":True, "version":"0.3.0"}]):
            self.assertIs(self.manager.launch_install(path), process)
        self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertTrue(self.manager.install_started)
        process.stdin.close.assert_not_called()
        with self.assertRaises(UpdateError):
            self.manager.launch_install(path)

    def test_inconsistent_release_version_cannot_download(self):
        with patch.object(self.manager, "_open") as request, self.assertRaises(UpdateError):
            self.manager.download(dict(self.release, latest_version="99.0.0"))
        request.assert_not_called()

    def test_public_update_request_contains_no_account_authorization(self):
        self.manager.opener = Mock()
        self.manager._open("https://api.github.com/repos/sixtenths/plex-presence/releases/latest")
        request = self.manager.opener.open.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("X-plex-token"))
        self.assertIsNone(request.get_header("Cookie"))
