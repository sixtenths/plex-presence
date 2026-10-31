from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from plex_presence import uninstall


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.default = self.base / 'PlexPresence'

    def test_default_data_and_nested_downloads_are_removed(self):
        (self.default / 'updates/download-example').mkdir(parents=True)
        (self.default / 'config.json').write_text('{}')
        (self.default / 'discord-tokens.json').write_text('{}')
        (self.default / 'updates/download-example/package.part').write_bytes(b'partial')
        neighbor = self.base / 'unrelated.txt'
        neighbor.write_text('keep')
        uninstall.clean_directory(self.default, self.default)
        self.assertFalse(self.default.exists())
        self.assertEqual(neighbor.read_text(), 'keep')

    def test_custom_data_cleans_owned_files_without_unrelated_files(self):
        custom = self.base / 'custom'
        download = custom / 'updates/download-example'
        download.mkdir(parents=True)
        for name in uninstall.FILES:
            (custom / name).write_text('{}')
        (custom / 'personal.txt').write_text('keep')
        (download / 'PlexPresence-Setup-windows-x64.exe.part').write_bytes(b'download')
        (custom / 'updates/result.json').write_text('{}')
        uninstall.clean_directory(custom, self.default)
        self.assertEqual(list(custom.iterdir()), [custom / 'personal.txt'])

    def test_custom_unrelated_updates_folder_is_preserved(self):
        custom = self.base / 'custom'
        folder = custom / 'updates/download-other-project'
        folder.mkdir(parents=True)
        (folder / 'personal.txt').write_text('keep')
        uninstall.clean_directory(custom, self.default)
        self.assertEqual((folder / 'personal.txt').read_text(), 'keep')

    @unittest.skipIf(os.name == 'nt', 'Symlinks may require Windows administrator rights')
    def test_default_symlink_does_not_delete_link_target(self):
        external = self.base / 'external'
        external.mkdir()
        (external / 'keep.txt').write_text('keep')
        self.default.symlink_to(external, target_is_directory=True)
        uninstall.clean_directory(self.default, self.default)
        self.assertFalse(self.default.is_symlink())
        self.assertTrue((external / 'keep.txt').exists())

    def test_running_instance_failure_prevents_all_deletion(self):
        with patch.object(uninstall, 'data_directory', return_value=self.default), patch.object(uninstall, 'registered_directories', return_value=[]), patch.object(uninstall, 'stop_running', side_effect=ValueError('still running')), patch.object(uninstall, 'clean_directory') as clean, patch.object(uninstall, 'remove_registry') as registry:
            with self.assertRaises(ValueError):
                uninstall.uninstall_data()
            clean.assert_not_called()
            registry.assert_not_called()

    def test_missing_data_does_not_recreate_folder(self):
        uninstall.stop_running(self.default)
        uninstall.clean_directory(self.default, self.default)
        self.assertFalse(self.default.exists())

    def test_source_launch_does_not_register_data(self):
        with patch.object(uninstall.sys, 'frozen', False, create=True), patch.object(uninstall, 'save_private') as save:
            uninstall.register_data(Mock())
        save.assert_not_called()
