"""Unit tests for collect_ids in pipeline.extract_asteroid_details."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.extract_asteroid_details import ExtractionError, collect_ids


def write_feed(folder: Path, name: str, ids):
    payload = {"near_earth_objects": {"2026-01-01": [{"id": i} for i in ids]}}
    (folder / name).write_text(json.dumps(payload), encoding="utf-8")


class CollectIdsTests(unittest.TestCase):
    def collect(self, folder):
        with contextlib.redirect_stdout(io.StringIO()):  # the function prints a summary
            return collect_ids(Path(folder))

    def test_unique_ids_sorted_numerically(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_feed(folder, "feed_2026-01-01_2026-01-01.json", ["10", "2"])
            write_feed(folder, "feed_2026-01-02_2026-01-02.json", ["2", "30"])
            self.assertEqual(self.collect(folder), ["2", "10", "30"])

    def test_no_feed_files_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ExtractionError):
                self.collect(tmp)

    def test_non_numeric_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_feed(folder, "feed_2026-01-01_2026-01-01.json", ["abc"])
            with self.assertRaises(ExtractionError):
                self.collect(folder)

    def test_file_without_near_earth_objects_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "feed_2026-01-01_2026-01-01.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ExtractionError):
                self.collect(folder)


if __name__ == "__main__":
    unittest.main()
