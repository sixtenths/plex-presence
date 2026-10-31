import io
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
