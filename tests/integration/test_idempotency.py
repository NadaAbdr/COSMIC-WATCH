"""Integration test: loading or building the same data twice must not create duplicates.

What it protects (the duplicate problem), layer by layer:
  1. load_raw     upserts, so the same file loaded twice is still one row in raw.*
  2. raw tables   two feed files whose dates overlap DO store the same approach twice.
                  That is expected, and the dbt model below is what removes it.
  3. dbt models   fact_close_approaches keeps one row per (asteroid, approach time),
                  even when feed and lookup both contain it, and a second `dbt run`
                  gives exactly the same counts.

It needs a running PostgreSQL (`docker compose up -d postgres`) and POSTGRES_PASSWORD
(in .env or the environment). It never touches your data: it creates a throwaway database
called cosmic_watch_test_<random> and drops it at the end. Tests skip themselves when
PostgreSQL is not reachable. The dbt test also needs `dbt` installed (pip install -e ".[dbt]").

Run:  python -m pytest tests/integration/test_idempotency.py -v
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from pipeline import load_raw  # noqa: E402  (after importorskip on purpose)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "sql" / "001_raw_schema.sql"
DBT_DIR = ROOT / "dbt"
FEED_DIR = ROOT / "data" / "raw" / "feed" / "extract_10_days" / "2026-09-01_2026-09-10_w7"
LOOKUP_DIR = ROOT / "data" / "raw" / "asteroid_details"

pytestmark = pytest.mark.postgres


# ---- throwaway database -------------------------------------------------

@pytest.fixture(scope="module")
def db_params():
    """Connection settings for a brand new empty database that is dropped afterwards."""
    load_raw.load_dotenv(ROOT / ".env")
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        pytest.skip("POSTGRES_PASSWORD is not set (put it in .env)")
    base = {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "cosmic"),
        "password": password,
    }
    admin_db = os.environ.get("POSTGRES_DB", "cosmic_watch")
    try:
        admin = psycopg.connect(dbname=admin_db, autocommit=True, connect_timeout=3, **base)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL is not reachable ({exc}). Start it with: docker compose up -d postgres")

    name = f"cosmic_watch_test_{uuid.uuid4().hex[:8]}"
    admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield {**base, "dbname": name}
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@pytest.fixture
def conn(db_params):
    """A connection to the throwaway database with empty raw tables (fresh for every test)."""
    connection = psycopg.connect(autocommit=True, **db_params)
    connection.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
    connection.execute("DROP SCHEMA IF EXISTS raw CASCADE")
    connection.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ---- real sample data ----------------------------------------------------

def real_feed_days(name):
    """{'2026-09-01': [asteroid, ...], ...} from one real saved feed response."""
    return json.loads((FEED_DIR / name).read_text(encoding="utf-8"))["near_earth_objects"]


def write_feed(path, days):
    """Write a feed file in the shape NASA sends (element_count must match the objects)."""
    payload = {"element_count": sum(len(v) for v in days.values()), "near_earth_objects": days}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def approach_keys(obj):
    """The natural key of every close approach in one asteroid: (asteroid id, approach time).

    Same rule as dbt/models/marts/fact_close_approaches.sql: the epoch, or the date if it is missing.
    """
    for ca in obj.get("close_approach_data", []):
        epoch = ca.get("epoch_date_close_approach")
        yield str(obj["id"]), str(epoch if epoch is not None else ca["close_approach_date"])


@pytest.fixture
def first_window(tmp_path):
    """feed_2026-09-01_2026-09-07.json, a real response."""
    return write_feed(tmp_path / "feed_2026-09-01_2026-09-07.json", real_feed_days("feed_2026-09-01_2026-09-07.json"))


@pytest.fixture
def overlapping_window(tmp_path):
    """feed_2026-09-02_2026-09-08.json: six days it shares with the first window, plus 09-08."""
    first = real_feed_days("feed_2026-09-01_2026-09-07.json")
    second = real_feed_days("feed_2026-09-08_2026-09-10.json")
    days = {d: objs for d, objs in first.items() if d >= "2026-09-02"}
    days["2026-09-08"] = second["2026-09-08"]
    return write_feed(tmp_path / "feed_2026-09-02_2026-09-08.json", days)


# ---- layer 1: load_raw upserts -------------------------------------------

def test_same_feed_file_twice_keeps_one_row(conn, first_window):
    load_raw.load_feed_file(conn, first_window)
    load_raw.load_feed_file(conn, first_window)

    assert count(conn, "raw.neo_feed_responses") == 1


def test_every_attempt_is_logged_even_when_the_data_is_not_duplicated(conn, first_window):
    load_raw.load_feed_file(conn, first_window)
    load_raw.load_feed_file(conn, first_window)

    logged = conn.execute("SELECT count(*) FROM raw.ingestion_log WHERE status = 'success'").fetchone()[0]
    assert logged == 2


def test_same_lookup_file_twice_keeps_one_row(conn):
    path = sorted(LOOKUP_DIR.glob("asteroid_*.json"))[0]

    load_raw.load_lookup_file(conn, path)
    load_raw.load_lookup_file(conn, path)

    assert count(conn, "raw.neo_lookup_responses") == 1


def test_reloading_a_changed_file_replaces_the_row(conn, first_window):
    load_raw.load_feed_file(conn, first_window)

    changed = json.loads(first_window.read_text(encoding="utf-8"))
    changed["marker"] = "second load"
    first_window.write_text(json.dumps(changed), encoding="utf-8")
    load_raw.load_feed_file(conn, first_window)

    assert count(conn, "raw.neo_feed_responses") == 1
    assert conn.execute("SELECT payload ->> 'marker' FROM raw.neo_feed_responses").fetchone()[0] == "second load"


def test_loading_a_whole_folder_twice_gives_the_same_rows_as_once(conn, tmp_path, first_window, overlapping_window):
    def load_folder():
        with contextlib.redirect_stdout(io.StringIO()):  # load_dir prints one line per file
            return load_raw.load_dir(conn, "feed", tmp_path)

    assert load_folder() == (2, 0)
    after_first = count(conn, "raw.neo_feed_responses")
    assert load_folder() == (2, 0)

    assert after_first == 2
    assert count(conn, "raw.neo_feed_responses") == after_first


# ---- layer 2: overlapping dates are stored twice in raw (known, handled by dbt) ----

def test_overlapping_windows_store_the_same_approach_twice_in_raw(conn, first_window, overlapping_window):
    load_raw.load_feed_file(conn, first_window)
    load_raw.load_feed_file(conn, overlapping_window)

    assert count(conn, "raw.neo_feed_responses") == 2  # different date ranges, so both are kept

    seen = [key for path in (first_window, overlapping_window)
            for objs in json.loads(path.read_text(encoding="utf-8"))["near_earth_objects"].values()
            for obj in objs for key in approach_keys(obj)]
    assert len(seen) > len(set(seen)), "the overlap should contain repeated approaches"


# ---- layer 3: dbt removes the duplicates, and a second run changes nothing ----

def dbt_run(db_params, work_dir):
    env = {
        **os.environ,
        "POSTGRES_HOST": db_params["host"], "POSTGRES_PORT": str(db_params["port"]),
        "POSTGRES_USER": db_params["user"], "POSTGRES_PASSWORD": db_params["password"],
        "POSTGRES_DB": db_params["dbname"],
    }
    result = subprocess.run(
        ["dbt", "run", "--profiles-dir", str(DBT_DIR), "--project-dir", str(DBT_DIR),
         "--target-path", str(work_dir / "target"), "--log-path", str(work_dir / "logs")],
        env=env, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, f"dbt run failed:\n{result.stdout[-3000:]}\n{result.stderr[-1000:]}"


@pytest.mark.dbt
@pytest.mark.skipif(shutil.which("dbt") is None, reason='dbt is not installed (pip install -e ".[dbt]")')
def test_dbt_keeps_one_row_per_approach_and_is_repeatable(conn, db_params, tmp_path, first_window, overlapping_window):
    # one asteroid that is in the feed AND has a lookup file: its approach is then in three places
    feed_objects = [o for objs in real_feed_days("feed_2026-09-01_2026-09-07.json").values() for o in objs]
    shared = next((o for o in feed_objects if (LOOKUP_DIR / f"asteroid_{o['id']}.json").exists()), None)
    if shared is None:
        pytest.skip("no feed asteroid has a lookup file in data/raw/asteroid_details")
    lookup_path = LOOKUP_DIR / f"asteroid_{shared['id']}.json"

    load_raw.load_feed_file(conn, first_window)
    load_raw.load_feed_file(conn, overlapping_window)
    load_raw.load_lookup_file(conn, lookup_path)

    everything = [o for path in (first_window, overlapping_window)
                  for objs in json.loads(path.read_text(encoding="utf-8"))["near_earth_objects"].values()
                  for o in objs]
    everything.append(json.loads(lookup_path.read_text(encoding="utf-8")))
    expected_approaches = {key for obj in everything for key in approach_keys(obj)}
    expected_asteroids = {str(obj["id"]) for obj in everything}

    def counts():
        return (
            count(conn, "analytics.fact_close_approaches"),
            count(conn, "analytics.dim_asteroids"),
            conn.execute("SELECT count(DISTINCT approach_id) FROM analytics.fact_close_approaches").fetchone()[0],
        )

    dbt_run(db_params, tmp_path)
    first = counts()
    dbt_run(db_params, tmp_path)
    second = counts()

    fact_rows, dim_rows, distinct_ids = first
    assert fact_rows == len(expected_approaches)
    assert dim_rows == len(expected_asteroids)
    assert distinct_ids == fact_rows  # approach_id is unique
    assert second == first