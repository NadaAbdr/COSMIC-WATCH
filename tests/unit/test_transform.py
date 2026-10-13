"""Unit tests for code that had no tests before. Everything is in this one file.

Covers:
  * pipeline.transform_raw   (raw browse pages -> silver CSVs)
  * pipeline.extract_asteroids.atomic_json and file_digest

The input is real NASA data: the committed pages in data/raw/extract_50_page.
Two awkward real asteroids are always included: one with no close approaches
and one that also passes the Moon. No network, no database; files go to tmp_path.
The tests skip themselves if data/raw/extract_50_page is missing.
"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from pipeline import transform_raw
from pipeline.extract_asteroids import atomic_json, file_digest

BROWSE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "extract_50_page"
NORMAL_ROWS = 18  # plus the two awkward ones = 20 asteroids

DIM_COLUMNS = [
    "asteroid_id", "name", "absolute_magnitude", "diameter_min_m", "diameter_max_m",
    "is_hazardous", "is_sentry", "orbit_class", "eccentricity", "semi_major_axis_au",
]
FACT_COLUMNS = [
    "asteroid_id", "close_approach_date", "orbiting_body",
    "velocity_kmh", "miss_distance_km", "miss_distance_lunar",
]


# ---- real sample ---------------------------------------------------------

@pytest.fixture(scope="module")
def real_sample():
    """20 real asteroids: 18 normal ones, one with no approaches, one that passes the Moon."""
    pages = sorted(BROWSE_DIR.glob("page_*.json"))
    if not pages:
        pytest.skip(f"no real data in {BROWSE_DIR}")

    everything = []
    for page in pages:
        everything += json.loads(page.read_text(encoding="utf-8"))["near_earth_objects"]

    none_ = next(o for o in everything if not o["close_approach_data"])
    moon = next(o for o in everything if any(c["orbiting_body"] == "Moon" for c in o["close_approach_data"]))
    normal = [o for o in everything if o["close_approach_data"] and o["id"] not in (none_["id"], moon["id"])]
    return {"none": none_, "moon": moon, "all": [none_, moon] + normal[:NORMAL_ROWS]}


@pytest.fixture
def objects(real_sample):
    """A private copy, so a test may change it freely."""
    return copy.deepcopy(real_sample["all"])


@pytest.fixture
def run_transform(monkeypatch, tmp_path, objects, capsys):
    """Write the sample as one raw page, run transform_raw.main(), return (dim, fact)."""
    raw, silver = tmp_path / "raw", tmp_path / "silver"
    page = raw / "run_1" / "page_00000.json"
    page.parent.mkdir(parents=True)
    page.write_text(json.dumps({"near_earth_objects": objects}), encoding="utf-8")

    monkeypatch.setattr(transform_raw, "RAW", raw)
    monkeypatch.setattr(transform_raw, "SILVER", silver)

    def run():
        transform_raw.main()
        capsys.readouterr()  # drop the printed summary
        read = lambda name: pd.read_csv(silver / name, dtype={"asteroid_id": str})  # noqa: E731
        return read("dim_asteroids.csv"), read("fact_close_approaches.csv")

    run.page, run.raw, run.silver = page, raw, silver
    return run


# ---- to_float ------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("1.5", 1.5),
        (2, 2.0),
        (".3907680937246651", 0.3907680937246651),  # NASA sends numbers like this
        (" 7 ", 7.0),
        (None, None),
        ("", None),
        ("n/a", None),
        ([], None),
    ],
)
def test_to_float(value, expected):
    assert transform_raw.to_float(value) == expected


# ---- shape of the output -------------------------------------------------

def test_output_columns(run_transform):
    dim, fact = run_transform()
    assert list(dim.columns) == DIM_COLUMNS
    assert list(fact.columns) == FACT_COLUMNS


def test_dim_has_one_row_per_asteroid(run_transform, objects):
    dim, _ = run_transform()
    assert len(dim) == len(objects) == 20
    assert dim["asteroid_id"].is_unique


def test_fact_has_one_row_per_close_approach(run_transform, objects):
    _, fact = run_transform()
    assert len(fact) == sum(len(o["close_approach_data"]) for o in objects)
    assert not fact.duplicated(["asteroid_id", "close_approach_date", "velocity_kmh"]).any()


def test_every_fact_row_has_a_dim_row(run_transform):
    dim, fact = run_transform()
    assert fact["asteroid_id"].isin(dim["asteroid_id"]).all()


# ---- values --------------------------------------------------------------

def test_diameters_come_from_the_meters_block(run_transform, objects):
    dim, _ = run_transform()
    for obj in objects:
        row = dim[dim["asteroid_id"] == obj["id"]].iloc[0]
        meters = obj["estimated_diameter"]["meters"]
        assert row["diameter_min_m"] == pytest.approx(meters["estimated_diameter_min"])
        assert row["diameter_max_m"] == pytest.approx(meters["estimated_diameter_max"])


def test_orbit_strings_become_numbers(run_transform, objects):
    dim, _ = run_transform()
    obj = next(o for o in objects if o.get("orbital_data"))
    row = dim[dim["asteroid_id"] == obj["id"]].iloc[0]
    orbit = obj["orbital_data"]
    assert row["eccentricity"] == pytest.approx(float(orbit["eccentricity"]))
    assert row["semi_major_axis_au"] == pytest.approx(float(orbit["semi_major_axis"]))
    assert row["orbit_class"] == orbit["orbit_class"]["orbit_class_type"]


def test_hazard_and_sentry_flags_are_kept(run_transform, objects):
    dim, _ = run_transform()
    assert dim["is_hazardous"].sum() == sum(o["is_potentially_hazardous_asteroid"] for o in objects)
    assert dim["is_sentry"].sum() == sum(o["is_sentry_object"] for o in objects)


def test_velocity_and_distances_are_numbers(run_transform):
    _, fact = run_transform()
    for column in ("velocity_kmh", "miss_distance_km", "miss_distance_lunar"):
        assert fact[column].dtype.kind == "f"
        assert fact[column].notna().all()


# ---- the two awkward real asteroids --------------------------------------

def test_asteroid_without_approaches_is_in_dim_but_not_in_fact(run_transform, real_sample):
    dim, fact = run_transform()
    asteroid_id = real_sample["none"]["id"]
    assert asteroid_id in set(dim["asteroid_id"])
    assert asteroid_id not in set(fact["asteroid_id"])


def test_approaches_to_other_bodies_are_kept(run_transform, real_sample):
    _, fact = run_transform()
    moon = real_sample["moon"]
    expected = {c["orbiting_body"] for c in moon["close_approach_data"]}
    got = set(fact[fact["asteroid_id"] == moon["id"]]["orbiting_body"])
    assert "Moon" in got
    assert got == expected


# ---- messy input (made up here: the real data never lacks these) ---------

def test_missing_orbital_data_gives_empty_orbit_columns(run_transform, objects):
    payload = json.loads(run_transform.page.read_text(encoding="utf-8"))
    payload["near_earth_objects"][0].pop("orbital_data", None)
    run_transform.page.write_text(json.dumps(payload), encoding="utf-8")

    dim, _ = run_transform()
    row = dim[dim["asteroid_id"] == objects[0]["id"]].iloc[0]
    assert len(dim) == 20  # the asteroid is not dropped
    assert row[["orbit_class", "eccentricity", "semi_major_axis_au"]].isna().all()


def test_unparseable_number_becomes_empty_not_a_crash(run_transform):
    payload = json.loads(run_transform.page.read_text(encoding="utf-8"))
    target = payload["near_earth_objects"][2]
    target["close_approach_data"][0]["relative_velocity"]["kilometers_per_hour"] = "n/a"
    run_transform.page.write_text(json.dumps(payload), encoding="utf-8")

    _, fact = run_transform()
    rows = fact[fact["asteroid_id"] == target["id"]]
    assert rows["velocity_kmh"].isna().sum() == 1
    assert len(rows) == len(target["close_approach_data"])


# ---- idempotency ---------------------------------------------------------

def test_running_twice_gives_identical_output(run_transform):
    first = run_transform()
    second = run_transform()
    for before, after in zip(first, second):
        assert before.equals(after)


def test_same_page_in_two_runs_is_not_double_counted(run_transform):
    first_dim, first_fact = run_transform()

    second = run_transform.raw / "run_2"
    second.mkdir()
    (second / "page_00000.json").write_text(run_transform.page.read_text(encoding="utf-8"), encoding="utf-8")

    dim, fact = run_transform()
    assert len(dim) == len(first_dim)
    assert len(fact) == len(first_fact)


# ---- files and messages --------------------------------------------------

def test_prints_a_summary_and_writes_both_csvs(run_transform, capsys):
    transform_raw.main()
    out = capsys.readouterr().out
    assert "Files read: 1" in out
    assert "dim_asteroids rows: 20" in out
    assert (run_transform.silver / "dim_asteroids.csv").is_file()
    assert (run_transform.silver / "fact_close_approaches.csv").is_file()


def test_no_input_pages_stops_with_a_clear_message(tmp_path, monkeypatch):
    empty = tmp_path / "empty_raw"
    empty.mkdir()
    silver = tmp_path / "silver"
    monkeypatch.setattr(transform_raw, "RAW", empty)
    monkeypatch.setattr(transform_raw, "SILVER", silver)

    with pytest.raises(SystemExit) as stopped:
        transform_raw.main()

    assert "No page_*.json files found" in str(stopped.value)
    assert not silver.exists()  # nothing half-written


# ---- atomic_json and file_digest ----------------------------------------

def test_saved_json_reads_back_the_same_and_leaves_no_temp_file(tmp_path, objects):
    path = tmp_path / "page.json"
    atomic_json(path, {"near_earth_objects": objects})

    assert json.loads(path.read_text(encoding="utf-8")) == {"near_earth_objects": objects}
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_save_keeps_the_old_file(tmp_path):
    path = tmp_path / "feed.json"
    atomic_json(path, {"ok": 1})

    with pytest.raises(ValueError):
        atomic_json(path, {"bad": float("nan")})  # NaN is not valid JSON

    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": 1}


def test_digest_changes_when_the_file_changes(tmp_path):
    path = tmp_path / "a.json"
    atomic_json(path, {"v": 1})
    first = file_digest(path)
    assert file_digest(path) == first

    atomic_json(path, {"v": 2})
    assert file_digest(path) != first