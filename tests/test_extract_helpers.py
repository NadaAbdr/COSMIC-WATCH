"""Unit tests for the helper functions in pipeline.extract_asteroids."""
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

from pipeline.extract_asteroids import (
    ExtractionError,
    remove_links,
    retry_delay,
    split_date_range,
    validate_payload,
)


class SplitDateRangeTests(unittest.TestCase):
    def test_ten_days_become_two_windows(self):
        windows = list(split_date_range(date(2026, 7, 1), date(2026, 7, 10)))
        self.assertEqual(
            windows,
            [(date(2026, 7, 1), date(2026, 7, 7)), (date(2026, 7, 8), date(2026, 7, 10))],
        )

    def test_single_day(self):
        day = date(2026, 10, 11)
        self.assertEqual(list(split_date_range(day, day)), [(day, day)])

    def test_exactly_seven_days_is_one_window(self):
        windows = list(split_date_range(date(2026, 7, 1), date(2026, 7, 7)))
        self.assertEqual(len(windows), 1)

    def test_no_gaps_and_no_overlaps(self):
        windows = list(split_date_range(date(2026, 7, 1), date(2026, 9, 30), window_days=5))
        for (_, previous_end), (next_start, _) in zip(windows, windows[1:]):
            self.assertEqual(next_start, previous_end + timedelta(days=1))
        self.assertEqual(windows[0][0], date(2026, 7, 1))
        self.assertEqual(windows[-1][1], date(2026, 9, 30))

    def test_custom_window_size(self):
        windows = list(split_date_range(date(2026, 7, 1), date(2026, 7, 7), window_days=3))
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[-1], (date(2026, 7, 7), date(2026, 7, 7)))

    def test_start_after_end_is_rejected(self):
        with self.assertRaises(ValueError):
            list(split_date_range(date(2026, 7, 10), date(2026, 7, 1)))

    def test_window_size_must_be_between_1_and_7(self):
        for bad in (0, 8):
            with self.assertRaises(ValueError):
                list(split_date_range(date(2026, 7, 1), date(2026, 7, 5), window_days=bad))


class RemoveLinksTests(unittest.TestCase):
    def test_links_removed_at_every_level(self):
        value = {
            "links": {"self": "https://example.test"},
            "name": "A",
            "nested": [{"links": 1, "keep": 2}],
        }
        self.assertEqual(remove_links(value), {"name": "A", "nested": [{"keep": 2}]})

    def test_api_key_inside_a_url_is_redacted(self):
        value = {"url": "https://api.nasa.gov/x?api_key=SECRET123&b=1"}
        cleaned = remove_links(value)
        self.assertEqual(cleaned["url"], "https://api.nasa.gov/x?api_key=REDACTED&b=1")
        self.assertNotIn("SECRET123", str(cleaned))

    def test_numbers_and_none_are_left_alone(self):
        self.assertEqual(remove_links({"a": 1, "b": None, "c": 2.5}), {"a": 1, "b": None, "c": 2.5})


class RetryDelayTests(unittest.TestCase):
    def test_no_header_uses_fallback(self):
        self.assertEqual(retry_delay({}, 5), 5)
        self.assertEqual(retry_delay(None, 5), 5)

    def test_seconds_header(self):
        self.assertEqual(retry_delay({"Retry-After": "30"}, 5), 30.0)

    def test_tiny_values_are_raised_to_one_second(self):
        self.assertEqual(retry_delay({"Retry-After": "0"}, 5), 1)

    def test_negative_value_uses_fallback(self):
        self.assertEqual(retry_delay({"Retry-After": "-3"}, 5), 5)

    def test_garbage_uses_fallback(self):
        self.assertEqual(retry_delay({"Retry-After": "soon"}, 5), 5)

    def test_http_date_in_the_future(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=60)
        delay = retry_delay({"Retry-After": format_datetime(when, usegmt=True)}, 5)
        self.assertGreater(delay, 30)
        self.assertLessEqual(delay, 60)


class ValidatePayloadTests(unittest.TestCase):
    start = date(2026, 7, 1)
    end = date(2026, 7, 7)

    def test_valid_payload_returns_record_count(self):
        payload = {"near_earth_objects": {"2026-07-01": [{}, {}], "2026-07-02": [{}]}}
        self.assertEqual(validate_payload(payload, self.start, self.end), 3)

    def test_payload_must_be_an_object(self):
        with self.assertRaises(ExtractionError):
            validate_payload([], self.start, self.end)

    def test_missing_near_earth_objects(self):
        with self.assertRaises(ExtractionError):
            validate_payload({}, self.start, self.end)

    def test_invalid_date_key(self):
        with self.assertRaises(ExtractionError):
            validate_payload({"near_earth_objects": {"not-a-date": []}}, self.start, self.end)

    def test_date_outside_the_requested_range(self):
        with self.assertRaises(ExtractionError):
            validate_payload({"near_earth_objects": {"2026-08-01": []}}, self.start, self.end)

    def test_records_must_be_a_list(self):
        with self.assertRaises(ExtractionError):
            validate_payload({"near_earth_objects": {"2026-07-01": {}}}, self.start, self.end)

    def test_error_message_does_not_leak_a_key(self):
        try:
            validate_payload({"near_earth_objects": {"bad": []}}, self.start, self.end)
        except ExtractionError as exc:
            self.assertNotIn("api_key", str(exc).lower())


if __name__ == "__main__":
    unittest.main()
