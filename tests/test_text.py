import unittest

from lidaclips.text import path_basename, safe_filename


class PathBasenameTests(unittest.TestCase):
    def test_posix_path(self):
        self.assertEqual(path_basename("/music/Artist/Album/01 - Track.flac"), "01 - Track.flac")

    def test_windows_path_inside_linux(self):
        self.assertEqual(
            path_basename(r"F:\Music\BABYMETAL\METAL FORTH (2025) [Album]\09 METALI!!.m4a"),
            "09 METALI!!.m4a",
        )

    def test_safe_filename_uses_basename_not_full_windows_path(self):
        stem = path_basename(r"F:\Music\Artist\Album\07 Track.flac")
        self.assertEqual(safe_filename(stem.rsplit(".", 1)[0]), "07 Track")


if __name__ == "__main__":
    unittest.main()
