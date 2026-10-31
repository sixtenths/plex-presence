import unittest
from unittest.mock import Mock
from plex_presence.anime import AnimeEpisodes


class AnimeTests(unittest.TestCase):
    def resolver(self, anime=True):
        resolver = AnimeEpisodes({})
        resolver._get = Mock(side_effect=[
            [{"Genre": [{"tag": "Anime" if anime else "Animation"}]}],
            [{"parentIndex": 0, "index": 99}, {"parentIndex": 1, "index": 1},
             {"parentIndex": 1, "index": 20}, {"parentIndex": 1, "index": 20},
             {"parentIndex": 2, "index": 21}, {"parentIndex": 3, "index": 1}],
        ])
        return resolver

    def test_absolute_across_seasons_excludes_specials_and_duplicates(self):
        resolver = self.resolver()
        self.assertEqual(resolver.label("123", 1, 13), "Episode 13")
        self.assertEqual(resolver.label("123", 2, 1), "Episode 21")
        self.assertEqual(resolver.label("123", 3, 1), "Episode 42")
        self.assertEqual(resolver._get.call_count, 2)
        self.assertIsNone(resolver.label("123", 0, 1))

    def test_other_animation_keeps_season_numbering(self):
        resolver = self.resolver(False)
        self.assertIsNone(resolver.label("123", 2, 1))
        self.assertEqual(resolver._get.call_count, 1)

    def test_already_absolute_indices_are_not_added_twice(self):
        resolver = self.resolver()
        resolver._get.side_effect = [[{"Genre": [{"tag": "Anime"}]}],
            [{"parentIndex": 1, "index": 20}, {"parentIndex": 2, "index": 21},
             {"parentIndex": 2, "index": 41}, {"parentIndex": 3, "index": 42}]]
        self.assertEqual(resolver.label("123", 2, 21), "Episode 21")
        self.assertEqual(resolver.label("123", 3, 42), "Episode 42")

    def test_missing_season_falls_back_and_explicit_absolute_wins(self):
        resolver = self.resolver()
        resolver._get.side_effect = [[{"Label": [{"tag": "anime"}]}],
            [{"parentIndex": 3, "index": 1, "absoluteIndex": 42}]]
        self.assertEqual(resolver.label("123", 3, 1), "Episode 42")
        self.assertIsNone(resolver.label("123", 3, 2))

    def test_unavailable_metadata_keeps_original_label(self):
        resolver = self.resolver()
        resolver._get.side_effect = OSError()
        self.assertIsNone(resolver.label("123", 2, 1))

    def test_bridge_title_and_original_activity_preserved(self):
        resolver = self.resolver()
        activity = {"state": "S2E1 · Episode title", "assets": {"large_image": "plex-poster:123"}}
        result = resolver.enrich(activity)
        self.assertEqual(result["state"], "Episode 21 · Episode title")
        self.assertEqual(activity["state"], "S2E1 · Episode title")
