import unittest

from lidaclips.media_validation import MotionValidator, StaticVideoError


class MotionValidatorTests(unittest.TestCase):
    def test_rejects_static_low_delta_frames(self):
        frame = bytes([32] * 16)
        validator = MotionValidator(
            frame_sampler=lambda _path: [frame, frame, frame, frame],
            min_frames=4,
            min_average_delta=0.01,
            min_max_delta=0.02,
        )

        with self.assertRaises(StaticVideoError) as raised:
            validator.validate("/unused/static.mp4")

        self.assertEqual(raised.exception.metrics["average_delta"], 0.0)
        self.assertTrue(raised.exception.metrics["static"])

    def test_accepts_moving_frames(self):
        frames = [
            bytes([0] * 16),
            bytes([255] * 16),
            bytes([0] * 16),
            bytes([255] * 16),
        ]
        validator = MotionValidator(
            frame_sampler=lambda _path: frames,
            min_frames=4,
            min_average_delta=0.01,
            min_max_delta=0.02,
        )

        metrics = validator.validate("/unused/moving.mp4")

        self.assertFalse(metrics["static"])
        self.assertGreater(metrics["average_delta"], 0.9)


if __name__ == "__main__":
    unittest.main()
