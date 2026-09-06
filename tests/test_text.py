import unittest

from lidaclips.text import normalize_text


class NormalizeTextTests(unittest.TestCase):
    def test_preserves_cyrillic_letters(self):
        self.assertEqual(normalize_text("Элизиум"), "элизиум")
        self.assertEqual(normalize_text("Король и Шут"), "король и шут")
        self.assertEqual(normalize_text("Как кошки по крышам!"), "как кошки по крышам")

    def test_still_folds_latin_case_and_punctuation(self):
        self.assertEqual(normalize_text("The Example Band!"), "the example band")
        self.assertEqual(normalize_text("Bright Lights (Official)"), "bright lights official")

    def test_strips_combining_marks_but_keeps_base_letters(self):
        # Latin with diacritic should fold; Cyrillic base letters remain.
        self.assertEqual(normalize_text("café"), "cafe")
        self.assertIn("е", normalize_text("ёжик"))


if __name__ == "__main__":
    unittest.main()
