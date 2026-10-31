import io
import json
import unittest
from unittest.mock import Mock, patch
from plex_presence.selected_artwork import SelectedArtworkResolver


class SelectedArtworkTests(unittest.TestCase):
    def setUp(self):
        self.resolver = SelectedArtworkResolver({"plex_url": "http://127.0.0.1:32400", "plex_token": "PRIVATE_TOKEN"})
        self.image = b"\xff\xd8\xff" + b"poster"

    def test_fetches_actual_thumb_and_reuses_same_image_upload(self):
        self.resolver._opener = Mock()
        self.resolver._opener.open.side_effect = [io.BytesIO(self.image), io.BytesIO(self.image)]
        self.resolver._upload = Mock(return_value="https://litter.catbox.moe/test.jpg")
        self.assertEqual(self.resolver._fetch("123"), self.resolver._fetch("123"))
        self.resolver._upload.assert_called_once_with(self.image)
        request = self.resolver._opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/library/metadata/123/thumb'))
        self.assertEqual(request.get_header('X-plex-token'), 'PRIVATE_TOKEN')

    def test_upload_only_contains_image_and_expiry_not_credentials(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"https://litter.catbox.moe/test.jpg")
        with patch('plex_presence.selected_artwork.build_opener', return_value=opener):
            self.assertEqual(self.resolver._upload(self.image), 'https://litter.catbox.moe/test.jpg')
        request = opener.open.call_args.args[0]
        self.assertIn(b'1h', request.data)
        self.assertIn(self.image, request.data)
        self.assertNotIn(b'PRIVATE_TOKEN', request.data)
        self.assertIsNone(request.get_header('X-plex-token'))

    def test_invalid_content_and_upload_destination_rejected(self):
        with self.assertRaises(ValueError):
            self.resolver.image_type(b'<html>login</html>')
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'https://other.example/poster.jpg')
        with patch('plex_presence.selected_artwork.build_opener', return_value=opener):
            with self.assertRaises(ValueError):
                self.resolver._upload(self.image)

    def test_failed_selected_poster_does_not_substitute_another_cover(self):
        self.resolver._fetch = Mock(side_effect=OSError())
        result = self.resolver.enrich({'assets': {'large_image': 'plex-poster:123'}})
        self.assertNotIn('assets', result)

    def test_host_outage_uses_same_image_with_one_hour_fallback(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({"success": True, "files":[{"id":"abc123def456"}]}).encode())
        with patch.object(self.resolver, '_upload_litterbox', side_effect=OSError('host down')), patch('plex_presence.selected_artwork.build_opener', return_value=opener):
            url = self.resolver._upload(self.image)
        self.assertEqual(url, 'https://tempfile.org/abc123def456/preview')
        request = opener.open.call_args.args[0]
        self.assertIn(b'name="expiryHours"\r\n\r\n1\r\n', request.data)
        self.assertIn(self.image, request.data)
        self.assertNotIn(b'PRIVATE_TOKEN', request.data)
        self.assertNotIn(b'127.0.0.1', request.data)
        self.assertIn('tempfile.org', self.resolver._square_url(url))

    def test_fallback_rejects_malformed_responses(self):
        for body in ([], {}, {"success":True, "files":[{"id":"../../elsewhere"}]}, {"success":False,"files":[{"id":"abc123def456"}]}):
            opener = Mock()
            opener.open.return_value = io.BytesIO(json.dumps(body).encode())
            with self.subTest(body=body), patch('plex_presence.selected_artwork.build_opener', return_value=opener), self.assertRaises(ValueError):
                self.resolver._upload_tempfile(self.image)
