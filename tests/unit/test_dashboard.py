"""Tests for the calculation functions in apps/streamlit/app.py.

Streamlit and Altair are replaced by tiny stand-ins while the file is imported, so
these tests need only pandas and numpy.
"""
import importlib.util
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

APP_PATH = Path(__file__).resolve().parents[2] / "apps" / "streamlit" / "app.py"


def load_app():
    fake_streamlit = types.ModuleType("streamlit")
    fake_streamlit.cache_data = lambda *args, **kwargs: (lambda func: func)
    replaced = {"streamlit": fake_streamlit, "altair": MagicMock()}
    saved = {name: sys.modules.get(name) for name in replaced}
    sys.modules.update(replaced)
    try:
        spec = importlib.util.spec_from_file_location("cosmic_dashboard_app", APP_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


app = load_app()

COLUMNS = [
    "approach_id", "asteroid_id", "name", "close_approach_date", "orbiting_body",
    "velocity_kmh", "miss_distance_km", "miss_distance_lunar",
    "diameter_min_m", "diameter_max_m", "is_hazardous", "orbit_class",
]


def sample_frame():
    rows = [
        ("a1", "1", "Alpha", date(2026, 3, 5), "Earth", 40000.0, 3.8e6, 10.0, 100.0, 200.0, True, "APO"),
        ("a2", "1", "Alpha", date(2026, 3, 20), "Earth", 50000.0, 1.9e6, 5.0, 100.0, 200.0, True, "APO"),
        ("b1", "2", "Beta", date(2026, 4, 2), "Earth", 20000.0, 7.7e6, 20.0, 10.0, 30.0, False, "AMO"),
        ("c1", "3", "Gamma", date(2026, 4, 9), "Mars", 30000.0, 1.0e8, 260.0, 50.0, 70.0, False, None),
        ("d1", "4", "Delta", date(2027, 1, 1), "Earth", 60000.0, 3.8e5, 1.0, None, None, None, "ATE"),
    ]
    return app.prepare(pd.DataFrame(rows, columns=COLUMNS))


class PrepareTests(unittest.TestCase):
    def test_types_and_average_diameter(self):
        df = sample_frame()
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["close_approach_date"]))
        self.assertEqual(df.loc[df["name"] == "Alpha", "diameter_m"].iloc[0], 150.0)
        self.assertTrue(pd.isna(df.loc[df["name"] == "Delta", "diameter_m"].iloc[0]))

    def test_missing_hazard_flag_becomes_false(self):
        df = sample_frame()
        self.assertFalse(bool(df.loc[df["name"] == "Delta", "is_hazardous"].iloc[0]))


class FilterTests(unittest.TestCase):
    def test_date_and_body_filter(self):
        view = app.apply_filters(sample_frame(), date(2026, 3, 1), date(2026, 4, 30), ["Earth"], False)
        self.assertEqual(sorted(view["name"]), ["Alpha", "Alpha", "Beta"])

    def test_empty_body_list_means_all_bodies(self):
        view = app.apply_filters(sample_frame(), date(2026, 3, 1), date(2026, 4, 30), [], False)
        self.assertEqual(len(view), 4)

    def test_hazardous_only(self):
        view = app.apply_filters(sample_frame(), date(2026, 1, 1), date(2027, 12, 31), [], True)
        self.assertEqual(set(view["name"]), {"Alpha"})

    def test_date_range_is_inclusive(self):
        view = app.apply_filters(sample_frame(), date(2026, 3, 5), date(2026, 3, 5), [], False)
        self.assertEqual(len(view), 1)

    def test_no_match_gives_empty_frame(self):
        view = app.apply_filters(sample_frame(), date(2000, 1, 1), date(2000, 1, 2), [], False)
        self.assertTrue(view.empty)


class CalculationTests(unittest.TestCase):
    def test_kpis(self):
        k = app.kpis(sample_frame())
        self.assertEqual(k["asteroids"], 4)
        self.assertEqual(k["approaches"], 5)
        self.assertEqual(k["hazardous"], 1)
        self.assertEqual(k["closest_lunar"], 1.0)
        self.assertEqual(k["fastest_kmh"], 60000.0)

    def test_monthly_counts(self):
        monthly = app.monthly_counts(sample_frame())
        self.assertEqual(list(monthly.columns), ["month", "approaches"])
        self.assertEqual(list(monthly["approaches"]), [2, 2, 1])
        self.assertTrue(monthly["month"].is_monotonic_increasing)

    def test_speed_histogram_counts_every_value(self):
        hist = app.speed_histogram(sample_frame(), bins=4)
        self.assertEqual(len(hist), 4)
        self.assertEqual(int(hist["count"].sum()), 5)

    def test_speed_histogram_of_nothing_is_empty(self):
        empty = sample_frame().iloc[0:0]
        self.assertTrue(app.speed_histogram(empty).empty)

    def test_closest_table_is_sorted_and_renamed(self):
        table = app.closest_table(sample_frame(), n=2)
        self.assertEqual(list(table["Asteroid"]), ["Delta", "Alpha"])
        self.assertIn("Miss distance (lunar)", table.columns)
        self.assertEqual(table["Miss distance (lunar)"].iloc[0], 1.0)
        self.assertIsInstance(table["Date"].iloc[0], date)

    def test_scatter_points_skip_rows_without_a_diameter(self):
        pts = app.size_vs_distance(sample_frame())
        self.assertEqual(len(pts), 4)  # Delta has no diameter
        self.assertTrue(set(pts["Hazardous"]) <= {"Yes", "No"})

    def test_scatter_points_are_sampled(self):
        self.assertEqual(len(app.size_vs_distance(sample_frame(), n=2)), 2)

    def test_orbit_counts_count_asteroids_not_approaches(self):
        counts = app.orbit_counts(sample_frame())
        by_class = dict(zip(counts["orbit_class"], counts["asteroids"]))
        self.assertEqual(by_class["APO"], 1)  # Alpha appears twice but is one asteroid
        self.assertEqual(by_class["Unknown"], 1)
        self.assertEqual(int(counts["asteroids"].sum()), 4)


if __name__ == "__main__":
    unittest.main()
