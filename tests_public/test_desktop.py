import tempfile
import unittest
from unittest.mock import Mock, patch
from plex_presence.desktop import DesktopPresence, DEFAULT_APPLICATION_ID
from plex_presence.service import Service
from plex_presence.storage import save_private
from pathlib import Path


class DesktopTests(unittest.TestCase):
    def test_publish_deduplicates_and_clears_on_pause(self):
        p = DesktopPresence()
        p.rpc = Mock()
        activity = {"details":"Movie", "timestamps":{"start":1700000000000}, "assets":{"large_image":"https://example.org/poster.png"}}
        with patch.object(p, "_send") as send:
            self.assertEqual(p.sync(activity)["state"], "active")
            p.sync(activity)
            send.assert_called_once_with(activity)
            activity["details"] = "Next movie"
            p.sync(activity)
            self.assertEqual(send.call_count, 2)
            self.assertEqual(p.sync(None)["state"], "idle")
            send.assert_called_with(None)

    def test_failed_connection_retries_and_republishes(self):
        p = DesktopPresence()
        with patch.object(p, "_connect", side_effect=OSError):
            self.assertEqual(p.sync({"details":"Movie"})["state"], "not_connected")
        p.retry_at = 0
        with patch.object(p, "_connect"), patch.object(p, "_send") as send:
            self.assertEqual(p.sync({"details":"Movie"})["state"], "active")
            send.assert_called_once()

    def test_initial_idle_does_not_publish_activity(self):
        p = DesktopPresence()
        with patch.object(p, "_connect"), patch.object(p, "_send") as send:
            self.assertEqual(p.sync(None)["state"], "idle")
            send.assert_not_called()

    def test_default_and_migration(self):
        with tempfile.TemporaryDirectory() as d:
            service = Service(d)
            self.assertEqual(service.config["discord_mode"], "desktop")
            self.assertEqual(service.publisher.client_id, DEFAULT_APPLICATION_ID)
        with tempfile.TemporaryDirectory() as d:
            save_private(Path(d) / "config.json", {"discord_client_id":"123456789012345678"})
            self.assertEqual(Service(d).config["discord_mode"], "headless")

    def test_mode_switch_clears_and_invalidates_pending_auth(self):
        with tempfile.TemporaryDirectory() as d:
            service = Service(d)
            publisher = service.publisher
            service.oauth.pending = {"state":"old"}
            with patch.object(publisher, "sync", return_value={"state":"idle"}) as sync, patch.object(publisher, "close") as close:
                service.action("/api/discord/mode", {"mode":"headless"})
                sync.assert_called_once_with(None)
                close.assert_called_once()
            self.assertIsNone(service.oauth.pending)
            self.assertIsNone(service.publisher)
            service.action("/api/discord/mode", {"mode":"desktop", "client_id":"123456789012345678"})
            self.assertEqual(service.publisher.client_id, "123456789012345678")
            with self.assertRaises(ValueError):
                service.action("/api/discord/connect", {"client_id":"123456789012345678"})

