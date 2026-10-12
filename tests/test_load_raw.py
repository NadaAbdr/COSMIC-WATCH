"""Unit tests for pipeline.load_raw. A fake connection stands in for PostgreSQL."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import load_raw


class FakeConn:
    """Records every SQL call; transaction() does nothing."""

    def __init__(self):
        self.calls = []

    def transaction(self):
        return contextlib.nullcontext()

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


def feed_payload(count=2, key="SECRETKEY123"):
    objects = [{"id": str(i)} for i in range(count)]
    return {
        "element_count": count,
        "near_earth_objects": {"2026-01-02": objects},
        "links": {"self": f"https://api.nasa.gov/neo/rest/v1/feed?api_key={key}"},
    }


class PatternTests(unittest.TestCase):
    def test_feed_file_name_pattern(self):
        match = load_raw.FEED_NAME.match("feed_2026-07-01_2026-07-07.json")
        self.assertEqual(match.groups(), ("2026-07-01", "2026-07-07"))

    def test_feed_file_name_rejects_other_names(self):
        for name in ("asteroid_123.json", "feed_2026-07-01.json", "feed_2026-07-01_2026-07-07.txt"):
            self.assertIsNone(load_raw.FEED_NAME.match(name))

    def test_api_key_pattern(self):
        cleaned = load_raw.API_KEY.sub("api_key=REDACTED", "x?api_key=abc-123_X&y=1")
        self.assertEqual(cleaned, "x?api_key=REDACTED&y=1")


class ReadCleanTests(unittest.TestCase):
    def test_key_is_removed_and_json_is_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feed_2026-01-02_2026-01-02.json"
            path.write_text(json.dumps(feed_payload()), encoding="utf-8")
            text, data = load_raw.read_clean(path)
        self.assertNotIn("SECRETKEY123", text)
        self.assertIn("api_key=REDACTED", text)
        self.assertEqual(data["element_count"], 2)

    def test_bad_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_raw.read_clean(path)


class LoadFeedFileTests(unittest.TestCase):
    def write(self, folder, name, payload):
        path = Path(folder) / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_good_file_is_upserted_and_logged_without_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "feed_2026-01-02_2026-01-02.json", feed_payload())
            conn = FakeConn()
            result = load_raw.load_feed_file(conn, path)
        self.assertEqual(result, "2026-01-02..2026-01-02 (2 objects)")
        self.assertEqual(len(conn.calls), 2)  # one upsert and one log row
        stored_text = conn.calls[0][1][2]
        self.assertNotIn("SECRETKEY123", stored_text)
        self.assertEqual(conn.calls[1][1][3], "success")

    def test_wrong_file_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "something.json", feed_payload())
            with self.assertRaises(ValueError):
                load_raw.load_feed_file(FakeConn(), path)

    def test_element_count_mismatch_is_rejected(self):
        payload = feed_payload(count=2)
        payload["element_count"] = 5
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "feed_2026-01-02_2026-01-02.json", payload)
            conn = FakeConn()
            with self.assertRaises(ValueError):
                load_raw.load_feed_file(conn, path)
        self.assertEqual(conn.calls, [])  # nothing is written for a bad file

    def test_missing_near_earth_objects_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "feed_2026-01-02_2026-01-02.json", {"element_count": 0})
            with self.assertRaises(ValueError):
                load_raw.load_feed_file(FakeConn(), path)


class LoadLookupFileTests(unittest.TestCase):
    def test_good_lookup_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asteroid_2001620.json"
            path.write_text(json.dumps({"id": "2001620", "name": "Albert"}), encoding="utf-8")
            conn = FakeConn()
            self.assertEqual(load_raw.load_lookup_file(conn, path), "asteroid 2001620")
        self.assertEqual(len(conn.calls), 2)

    def test_non_numeric_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asteroid_x.json"
            path.write_text(json.dumps({"id": "abc"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_raw.load_lookup_file(FakeConn(), path)


class LoadDirTests(unittest.TestCase):
    def test_one_bad_file_does_not_stop_the_others(self):
        good = feed_payload(count=1)
        bad = feed_payload(count=1)
        bad["element_count"] = 9
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "feed_2026-01-02_2026-01-02.json").write_text(json.dumps(good), encoding="utf-8")
            (folder / "feed_2026-01-03_2026-01-03.json").write_text(json.dumps(bad), encoding="utf-8")
            conn = FakeConn()
            with contextlib.redirect_stdout(io.StringIO()):
                ok, failed = load_raw.load_dir(conn, "feed", folder)
        self.assertEqual((ok, failed), (1, 1))
        statuses = [params[3] for _, params in conn.calls if params and len(params) == 6]
        self.assertIn("failed", statuses)
        self.assertIn("success", statuses)


if __name__ == "__main__":
    unittest.main()
