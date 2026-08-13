import unittest

from csv_parser import Track, coerce_duration_ms
from match import pick_best_candidate, score_candidate


class DurationCoerceTests(unittest.TestCase):
    def test_milliseconds(self) -> None:
        self.assertEqual(coerce_duration_ms(248373), 248373)
        self.assertEqual(coerce_duration_ms("248373"), 248373)

    def test_seconds(self) -> None:
        self.assertEqual(coerce_duration_ms(248), 248000)
        self.assertEqual(coerce_duration_ms("4:08"), 248000)


class MatchScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.track = Track(
            title="Get Lucky",
            artist="Daft Punk",
            album="RAM",
            duration_ms=248000,
        )

    def test_official_audio_scores_high(self) -> None:
        score = score_candidate(
            self.track,
            {
                "id": "5NV6Rdv1a3I",
                "title": "Daft Punk - Get Lucky (Official Audio)",
                "uploader": "Daft Punk - Topic",
                "duration": 248,
            },
        )
        self.assertGreaterEqual(score, 50)

    def test_wrong_song_is_rejected(self) -> None:
        score = score_candidate(
            self.track,
            {
                "id": "4NRXfOy7huA",
                "title": "The Weeknd - Blinding Lights (Official Video)",
                "uploader": "The Weeknd",
                "duration": 200,
            },
        )
        self.assertLess(score, 0)

    def test_hour_mix_is_rejected(self) -> None:
        score = score_candidate(
            self.track,
            {
                "id": "hourmix123",
                "title": "Get Lucky - 1 Hour Mix",
                "uploader": "Mix Channel",
                "duration": 3600,
            },
        )
        self.assertLess(score, 0)

    def test_cover_by_other_artist_is_rejected(self) -> None:
        score = score_candidate(
            self.track,
            {
                "id": "cover123456",
                "title": "Get Lucky (Cover)",
                "uploader": "Random Singer",
                "duration": 250,
            },
        )
        self.assertLess(score, 0)

    def test_picks_matching_result_not_first_wrong_one(self) -> None:
        chosen = pick_best_candidate(
            self.track,
            [
                {
                    "id": "wrong111111",
                    "title": "Summer Hits 2024 Mega Mix",
                    "uploader": "Various",
                    "duration": 4200,
                },
                {
                    "id": "5NV6Rdv1a3I",
                    "title": "Get Lucky",
                    "uploader": "Daft Punk - Topic",
                    "duration": 249,
                },
            ],
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["id"], "5NV6Rdv1a3I")

    def test_no_acceptable_candidate(self) -> None:
        chosen = pick_best_candidate(
            self.track,
            [
                {
                    "id": "other000000",
                    "title": "Completely Different Song",
                    "uploader": "Somebody",
                    "duration": 180,
                }
            ],
        )
        self.assertIsNone(chosen)
