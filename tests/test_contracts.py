"""Tests for pipeline.contracts: raw JSON and table checks catch bad data and accept good data."""
import datetime
import unittest
from pathlib import Path

import pandas as pd

from pipeline import contracts

ROOT = Path(__file__).resolve().parents[1]
RAW = contracts.load_contract(ROOT / "contracts" / "neows_browse.yml")
TABLES = contracts.load_contract(ROOT / "contracts" / "silver_tables.yml")


def good_object():
    return {
        "id": "2000433",
        "neo_reference_id": "2000433",
        "name": "433 Eros",
        "absolute_magnitude_h": 10.4,
        "is_potentially_hazardous_asteroid": False,
        "is_sentry_object": False,
        "estimated_diameter": {"meters": {"estimated_diameter_min": 10.0, "estimated_diameter_max": 20.0}},
        "orbital_data": {
            "orbit_class": {"orbit_class_type": "AMO"},
            "eccentricity": "0.22",
            "semi_major_axis": "1.46",
            "inclination": "10.8",
        },
        "close_approach_data": [
            {
                "close_approach_date": "2026-01-01",
                "epoch_date_close_approach": 1767225600000,
                "orbiting_body": "Earth",
                "relative_velocity": {"kilometers_per_hour": "20000.5"},
                "miss_distance": {"kilometers": "1000000.1", "lunar": "2.6"},
            }
        ],
    }


def good_frames():
    dim = pd.DataFrame(
        {
            "asteroid_id": ["1", "2"],
            "name": ["A", "B"],
            "absolute_magnitude": [20.1, 22.0],
            "diameter_min_m": [10.0, 20.0],
            "diameter_max_m": [30.0, 40.0],
            "is_hazardous": [True, False],
            "is_sentry": [False, False],
            "orbit_class": ["APO", "AMO"],
            "eccentricity": [0.3, 0.5],
            "semi_major_axis_au": [1.2, 1.8],
        }
    )
    fact = pd.DataFrame(
        {
            "approach_id": ["a", "b"],
            "asteroid_id": ["1", "2"],
            "close_approach_date": [datetime.date(2026, 1, 1), datetime.date(2026, 1, 2)],
            "orbiting_body": ["Earth", "Earth"],
            "velocity_kmh": [1000.0, 2000.0],
            "miss_distance_km": [5.0, 6.0],
            "miss_distance_lunar": [0.1, 0.2],
        }
    )
    return {"dim_asteroids": dim, "fact_close_approaches": fact}


class RawContractTests(unittest.TestCase):
    def check(self, obj):
        report = contracts.Report()
        contracts.validate_object(RAW, obj, report)
        return report

    def test_good_object_passes(self):
        self.assertTrue(self.check(good_object()).ok)

    def test_non_numeric_velocity_is_reported(self):
        obj = good_object()
        obj["close_approach_data"][0]["relative_velocity"]["kilometers_per_hour"] = "fast"
        self.assertFalse(self.check(obj).ok)

    def test_negative_miss_distance_is_reported(self):
        obj = good_object()
        obj["close_approach_data"][0]["miss_distance"]["kilometers"] = "-5"
        self.assertFalse(self.check(obj).ok)

    def test_id_must_be_digit_string(self):
        obj = good_object()
        obj["id"] = "abc"
        self.assertFalse(self.check(obj).ok)


class TableContractTests(unittest.TestCase):
    def test_good_tables_pass(self):
        self.assertTrue(contracts.validate_tables(TABLES, good_frames()).ok)

    def test_duplicate_primary_key_is_reported(self):
        frames = good_frames()
        frames["dim_asteroids"].loc[1, "asteroid_id"] = "1"
        self.assertFalse(contracts.validate_tables(TABLES, frames).ok)

    def test_unknown_orbit_class_is_reported(self):
        frames = good_frames()
        frames["dim_asteroids"].loc[0, "orbit_class"] = "XXX"
        self.assertFalse(contracts.validate_tables(TABLES, frames).ok)

    def test_orphan_approach_is_reported(self):
        frames = good_frames()
        frames["fact_close_approaches"].loc[0, "asteroid_id"] = "999"
        self.assertFalse(contracts.validate_tables(TABLES, frames).ok)

    def test_zero_velocity_is_reported(self):
        frames = good_frames()
        frames["fact_close_approaches"].loc[0, "velocity_kmh"] = 0.0
        self.assertFalse(contracts.validate_tables(TABLES, frames).ok)

    def test_missing_table_is_reported(self):
        frames = good_frames()
        del frames["fact_close_approaches"]
        self.assertFalse(contracts.validate_tables(TABLES, frames).ok)


class CommandLineTests(unittest.TestCase):
    def test_real_raw_data_passes(self):
        raw = ROOT / "data" / "raw"
        if not raw.exists():
            self.skipTest("no data/raw folder")
        self.assertEqual(contracts.main(["raw", str(raw)]), 0)


if __name__ == "__main__":
    unittest.main()
