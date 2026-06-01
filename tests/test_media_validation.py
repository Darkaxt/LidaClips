import unittest
from unittest.mock import patch

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

    def test_default_sampler_reads_multiple_frames_with_one_ffmpeg_process(self):
        frame_size = 4
        stdout = (b"\x00" * frame_size) + (b"\x01" * frame_size) + (b"\x02" * frame_size)
        validator = MotionValidator(sample_count=3, frame_width=2, frame_height=2)

        with patch("lidaclips.media_validation.subprocess.run") as run:
            run.return_value.stdout = stdout

            frames = validator._sample_frames("/clips/example.mp4")

        self.assertEqual(frames, [b"\x00" * frame_size, b"\x01" * frame_size, b"\x02" * frame_size])
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertIn("-vf", command)
        self.assertIn("fps=1/6", command[command.index("-vf") + 1])
        self.assertEqual(run.call_args.kwargs["timeout"], 45)


if __name__ == "__main__":
    unittest.main()
