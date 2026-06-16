import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InitScriptTests(unittest.TestCase):
    def test_init_does_not_recursively_chown_media_mount(self):
        script = (PROJECT_ROOT / "lidaclips-init.sh").read_text(encoding="utf-8")
        recursive_chown_targets = self._recursive_chown_targets(script)

        self.assertNotIn("chown -R ${PUID}:${PGID} /lidaclips\n", script)
        self.assertNotIn("/lidaclips/clips", recursive_chown_targets)
        self.assertIn("/lidaclips/config", recursive_chown_targets)
        self.assertIn("/lidaclips/cache", recursive_chown_targets)
        self.assertIn("/lidaclips/staging", recursive_chown_targets)

    def _recursive_chown_targets(self, script: str) -> str:
        lines = [line.strip() for line in script.splitlines()]
        return "\n".join(line for line in lines if line.startswith("chown -R "))


if __name__ == "__main__":
    unittest.main()
