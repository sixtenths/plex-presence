import copy
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from plex_presence.artwork import ArtworkResolver, _PlexRedirectHandler


TMDB = "https://image.tmdb.org/t/p/w500/poster.jpg"
PLEX = "https://metadata-static.plex.tv/a/poster.jpg"
TVDB = "https://artworks.thetvdb.com/banners/poster.jpg"
SQUARE_TMDB = "https://wsrv.nl/?url=https%3A%2F%2Fimage.tmdb.org%2Ft%2Fp%2Fw500%2Fposter.jpg&w=256&h=256&fit=contain&cbg=00000000&output=png"
SQUARE_PLEX = "https://wsrv.nl/?url=https%3A%2F%2Fmetadata-static.plex.tv%2Fa%2Fposter.jpg&w=256&h=256&fit=contain&cbg=00000000&output=png"
SQUARE_TVDB = "https://wsrv.nl/?url=https%3A%2F%2Fartworks.thetvdb.com%2Fbanners%2Fposter.jpg&w=256&h=256&fit=contain&cbg=00000000&output=png"


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        self.resolver = ArtworkResolver({"plex_url": "http://192.0.2.10:32400", "plex_token": "DUMMY_SECRET"})
        self.resolver._opener = Mock()
        self.original = {"name": "Film", "type": 3, "details": "Film (2015)",
                         "timestamps": {"start": 1792000000000},
                         "assets": {"large_image": "plex-poster:1260", "large_text": "Poster"}}

    def response(self, entries):
        self.resolver._opener.open.return_value = io.BytesIO(json.dumps({
            "MediaContainer": {"Metadata": entries},
        }).encode())

    def test_selected_public_poster_preferred_and_original_preserved(self):
        self.response([{"ratingKey": TMDB}, {"thumb": PLEX, "selected": True}])
        snapshot = copy.deepcopy(self.original)
        enriched = self.resolver.enrich(self.original)
        self.assertEqual(enriched["assets"]["large_image"], SQUARE_PLEX)
        self.assertEqual(enriched["assets"]["large_text"], "Poster")
        self.assertEqual(enriched["timestamps"], snapshot["timestamps"])
        self.assertEqual(self.original, snapshot)

    def test_local_selected_poster_falls_back_to_first_public_poster(self):
        self.response([{"ratingKey": "metadata://posters/local", "selected": 1},
                       {"key": "/library/metadata/1260/thumb/1", "thumb": TMDB},
                       {"ratingKey": TVDB}])
        self.assertEqual(self.resolver.enrich(self.original)["assets"]["large_image"], SQUARE_TMDB)

    def test_output_fits_entire_poster_into_transparent_square(self):
        self.response([{"ratingKey": TMDB}])
        url = self.resolver.enrich(self.original)["assets"]["large_image"]
        self.assertEqual(urlsplit(url).netloc, "wsrv.nl")
        self.assertEqual(parse_qs(urlsplit(url).query), {
            "url": [TMDB], "w": ["256"], "h": ["256"],
            "fit": ["contain"], "cbg": ["00000000"], "output": ["png"],
        })
        self.assertNotIn("DUMMY_SECRET", url)
        self.assertNotIn("192.168.", url)

    def test_transformer_never_receives_private_or_untrusted_source(self):
        for source in ("http://192.0.2.10:32400/library/metadata/1260/thumb?X-Plex-Token=secret",
                       "https://image.tmdb.org/x.jpg?token=secret", SQUARE_TMDB,
                       "https://evil.test/poster.jpg", None):
            with self.subTest(source=source):
                self.resolver._resolve = Mock(return_value=source)
                self.assertNotIn("assets", self.resolver.enrich(self.original))

    def test_request_authentication_is_header_only(self):
        self.response([{"ratingKey": TMDB}])
        self.resolver.enrich(self.original)
        request = self.resolver._opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://192.0.2.10:32400/library/metadata/1260/posters")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("X-plex-token"), "DUMMY_SECRET")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertNotIn("DUMMY_SECRET", request.full_url)
        self.assertEqual(self.resolver._opener.open.call_args.kwargs["timeout"], 4)

    def test_unsafe_provider_urls_are_rejected(self):
        urls = ["http://image.tmdb.org/x.jpg", "https://evil.test/x.jpg",
                "https://image.tmdb.org.evil.test/x.jpg", "https://user@image.tmdb.org/x.jpg",
                "https://image.tmdb.org:443/x.jpg", "https://image.tmdb.org/x.jpg?X-Plex-Token=secret",
                "https://image.tmdb.org/x.jpg#fragment", "https://image.tmdb.org/x.jpg?",
                "https://image.tmdb.org/path/X-Plex-Token=secret.jpg",
                "https://image.tmdb.org/path/%61ccess_token/secret.jpg",
                "https://image.tmdb.org/x\n.jpg", "https://image.tmdb.org\\evil.test/x.jpg",
                "https://192.0.2.10/x.jpg", "https://image.tmdb.org/"]
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(self.resolver._public_url(url))
        for url in (TMDB, PLEX, TVDB):
            self.assertEqual(self.resolver._public_url(url), url)

    def test_invalid_markers_never_fetch_and_remove_unused_text(self):
        markers = ["plex-poster:", "plex-poster:../1260", "plex-poster:1260/posters",
                   "plex-poster:1260?token=x", "plex-poster:" + "1" * 21,
                   "plex-poster:１２６０", "plex-poster://1260", "plex-poster:1260\n"]
        for marker in markers:
            with self.subTest(marker=marker):
                self.original["assets"]["large_image"] = marker
                self.assertNotIn("assets", self.resolver.enrich(self.original))
        self.resolver._opener.open.assert_not_called()

    def test_small_image_resolves_without_changing_other_fields(self):
        self.response([{"ratingKey": TVDB, "selected": "1"}])
        self.original["assets"] = {"large_image": TMDB, "small_image": "plex-poster:456", "small_text": "Artist"}
        result = self.resolver.enrich(self.original)
        self.assertEqual(result["assets"], {"large_image": TMDB, "small_image": SQUARE_TVDB, "small_text": "Artist"})
        self.assertEqual(result["details"], self.original["details"])

    def test_failure_omits_only_failed_image_and_hover_text(self):
        self.response([{"ratingKey": "https://evil.test/x.jpg", "selected": True}])
        self.original["assets"]["small_image"] = "other-app-asset"
        self.original["assets"]["small_text"] = "Other"
        self.assertEqual(self.resolver.enrich(self.original)["assets"], {
            "small_image": "other-app-asset", "small_text": "Other"})

    def test_success_cache_expires_after_one_hour(self):
        self.response([{"ratingKey": TMDB}])
        with patch("plex_presence.artwork.time.monotonic", return_value=1000):
            self.resolver.enrich(self.original)
        with patch("plex_presence.artwork.time.monotonic", return_value=4599):
            self.assertEqual(self.resolver.enrich(self.original)["assets"]["large_image"], SQUARE_TMDB)
        self.assertEqual(self.resolver._opener.open.call_count, 1)
        self.response([{"ratingKey": PLEX}])
        with patch("plex_presence.artwork.time.monotonic", return_value=4600):
            self.assertEqual(self.resolver.enrich(self.original)["assets"]["large_image"], SQUARE_PLEX)
        self.assertEqual(self.resolver._opener.open.call_count, 2)

    def test_failed_request_is_cached_for_sixty_seconds(self):
        self.resolver._opener.open.side_effect = URLError("unavailable")
        with patch("plex_presence.artwork.time.monotonic", return_value=1000):
            self.assertNotIn("assets", self.resolver.enrich(self.original))
        with patch("plex_presence.artwork.time.monotonic", return_value=1059):
            self.resolver.enrich(self.original)
        self.assertEqual(self.resolver._opener.open.call_count, 1)
        self.resolver._opener.open.side_effect = None
        self.response([{"ratingKey": TMDB}])
        with patch("plex_presence.artwork.time.monotonic", return_value=1060):
            self.assertEqual(self.resolver.enrich(self.original)["assets"]["large_image"], SQUARE_TMDB)
        self.assertEqual(self.resolver._opener.open.call_count, 2)

    def test_cache_is_bounded(self):
        self.resolver._fetch = Mock(return_value=TMDB)
        for item_id in range(513):
            self.resolver._resolve(str(item_id))
        self.assertEqual(len(self.resolver._cache), 512)
        self.assertNotIn("0", self.resolver._cache)

    def test_oversized_response_is_rejected(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b" " * (self.resolver.MAX_RESPONSE + 1)
        self.resolver._opener.open.return_value = response
        self.assertNotIn("assets", self.resolver.enrich(self.original))
        response.read.assert_called_once_with(2 * 1024 * 1024 + 1)

    def test_missing_config_none_and_absent_assets(self):
        resolver = ArtworkResolver({})
        self.assertIsNone(resolver.enrich(None))
        self.assertEqual(resolver.enrich({"name": "Film"}), {"name": "Film"})
        self.assertNotIn("assets", resolver.enrich(self.original))


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.handler = _PlexRedirectHandler(("http", "192.0.2.10", 32400))
        self.request = Request("http://192.0.2.10:32400/library/metadata/1260/posters", headers={"X-Plex-Token": "DUMMY_SECRET"})

    def test_same_origin_redirect_preserves_header(self):
        request = self.handler.redirect_request(self.request, None, 302, "Found", {},
                                                "http://192.0.2.10:32400/redirected")
        self.assertEqual(request.get_header("X-plex-token"), "DUMMY_SECRET")

    def test_changed_origin_redirects_are_blocked(self):
        for url in ("http://evil.test/posters", "http://192.0.2.10:80/posters",
                    "https://192.0.2.10:32400/posters", "http://user@192.0.2.10:32400/posters"):
            with self.subTest(url=url), self.assertRaises(URLError):
                self.handler.redirect_request(self.request, None, 302, "Found", {}, url)


if __name__ == "__main__":
    unittest.main()
