import unittest

from lidaclips.scoring import Candidate, ClipScorer


class ClipScorerTests(unittest.TestCase):
    def setUp(self):
        self.scorer = ClipScorer(minimum_score=75)

    def test_accepts_official_music_video_from_verified_artist_channel(self):
        candidate = Candidate(
            video_id="abc123",
            title="The Example Band - Bright Lights (Official Music Video)",
            webpage_url="https://www.youtube.com/watch?v=abc123",
            channel="The Example Band",
            uploader="The Example Band",
            duration=242,
            view_count=2_500_000,
            channel_follower_count=900_000,
            channel_is_verified=True,
        )

        decision = self.scorer.score(
            artist="The Example Band",
            title="Bright Lights",
            expected_duration=240,
            candidate=candidate,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.quality_tier, "official")
        self.assertGreaterEqual(decision.score, 75)
        self.assertIn("official", decision.reasons)
        self.assertIn("verified_channel", decision.reasons)

    def test_verified_artist_channel_non_official_match_is_fallback(self):
        candidate = Candidate(
            video_id="fallback123",
            title="The Example Band - Bright Lights",
            webpage_url="https://www.youtube.com/watch?v=fallback123",
            channel="The Example Band",
            uploader="The Example Band",
            duration=242,
            view_count=2_500_000,
            channel_follower_count=900_000,
            channel_is_verified=True,
        )

        decision = self.scorer.score(
            artist="The Example Band",
            title="Bright Lights",
            expected_duration=240,
            candidate=candidate,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.quality_tier, "fallback")
        self.assertNotIn("official", decision.reasons)

    def test_rejects_topic_audio_even_when_title_matches(self):
        candidate = Candidate(
            video_id="topic123",
            title="Bright Lights",
            webpage_url="https://www.youtube.com/watch?v=topic123",
            channel="The Example Band - Topic",
            uploader="The Example Band - Topic",
            duration=240,
            view_count=8_000_000,
            channel_follower_count=2_000_000,
            channel_is_verified=True,
        )

        decision = self.scorer.score(
            artist="The Example Band",
            title="Bright Lights",
            expected_duration=240,
            candidate=candidate,
        )

        self.assertFalse(decision.accepted)
        self.assertEqual(decision.quality_tier, "rejected")
        self.assertEqual(decision.rejection_reason, "blocked_keyword")

    def test_rejects_lyric_live_cover_and_visualizer_variants(self):
        titles = [
            "The Example Band - Bright Lights (Lyric Video)",
            "The Example Band - Bright Lights Live at Wembley",
            "Bright Lights - cover by Someone Else",
            "The Example Band - Bright Lights Visualizer",
        ]

        for index, title in enumerate(titles):
            with self.subTest(title=title):
                decision = self.scorer.score(
                    artist="The Example Band",
                    title="Bright Lights",
                    expected_duration=240,
                    candidate=Candidate(
                        video_id=f"bad{index}",
                        title=title,
                        webpage_url=f"https://www.youtube.com/watch?v=bad{index}",
                        channel="The Example Band",
                        uploader="The Example Band",
                        duration=240,
                        view_count=10_000_000,
                        channel_follower_count=2_000_000,
                        channel_is_verified=True,
                    ),
                )

                self.assertFalse(decision.accepted)
                self.assertEqual(decision.quality_tier, "rejected")
                self.assertEqual(decision.rejection_reason, "blocked_keyword")

    def test_rejects_wrong_artist_even_if_video_is_popular(self):
        candidate = Candidate(
            video_id="wrong123",
            title="Another Artist - Bright Lights (Official Video)",
            webpage_url="https://www.youtube.com/watch?v=wrong123",
            channel="Another Artist",
            uploader="Another Artist",
            duration=240,
            view_count=100_000_000,
            channel_follower_count=5_000_000,
            channel_is_verified=True,
        )

        decision = self.scorer.score(
            artist="The Example Band",
            title="Bright Lights",
            expected_duration=240,
            candidate=candidate,
        )

        self.assertFalse(decision.accepted)
        self.assertEqual(decision.quality_tier, "rejected")
        self.assertEqual(decision.rejection_reason, "low_score")

    def test_multi_song_music_video_cannot_be_official(self):
        candidate = Candidate(
            video_id="medley123",
            title="The Example Band - Bright Lights / City Glow (Music Video)",
            webpage_url="https://www.youtube.com/watch?v=medley123",
            channel="The Example Band",
            uploader="The Example Band",
            duration=242,
            view_count=2_500_000,
            channel_follower_count=900_000,
            channel_is_verified=True,
        )

        decision = self.scorer.score(
            artist="The Example Band",
            title="Bright Lights",
            expected_duration=240,
            candidate=candidate,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.quality_tier, "fallback")
        self.assertNotIn("official", decision.reasons)


if __name__ == "__main__":
    unittest.main()
