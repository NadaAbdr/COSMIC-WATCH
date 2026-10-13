"""Tests for scripts/scrub_api_key.py, which removes NASA keys from saved JSON files."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scrub_api_key.py"


def run_script(folder):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(folder)], capture_output=True, text=True, check=True
    )
    return result.stdout


class ScrubScriptTests(unittest.TestCase):
    def test_key_is_replaced_and_second_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            target = folder / "asteroid_1.json"
            target.write_text('{"url": "https://x.test/?api_key=SECRET123&a=1"}', encoding="utf-8")

            self.assertIn("Scrubbed 1 file(s)", run_script(folder))
            content = target.read_text(encoding="utf-8")
            self.assertNotIn("SECRET123", content)
            self.assertIn("api_key=REDACTED&a=1", content)

            self.assertIn("Scrubbed 0 file(s)", run_script(folder))

    def test_other_file_types_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            note = folder / "notes.txt"
            note.write_text("api_key=SECRET123", encoding="utf-8")
            run_script(folder)
            self.assertEqual(note.read_text(encoding="utf-8"), "api_key=SECRET123")


if __name__ == "__main__":
    unittest.main()
