"""Load raw NeoWs JSON files into PostgreSQL (schema `raw`, see sql/001_raw_schema.sql).

Usage (from the project root, after `pip install -e .`):
    python -m pipeline.cli load feed   data/raw/feed          # feed_<start>_<end>.json files
    python -m pipeline.cli load lookup data/raw/asteroid_details   # asteroid_<id>.json files
    python -m pipeline.cli load all    data/raw               # both of the above

Connection settings come from environment variables or the .env file:
    POSTGRES_HOST (default localhost)  POSTGRES_PORT (5432)  POSTGRES_DB  POSTGRES_USER  POSTGRES_PASSWORD

Safe to re-run: each file is an upsert, so loading the same file twice never creates duplicates.
Every file is also recorded in raw.ingestion_log (success or failed).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

FEED_NAME = re.compile(r"^feed_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json$")
# NASA echoes the caller's key inside some URLs in the payload. Never store it.
API_KEY = re.compile(r"api_key=[A-Za-z0-9_-]+")

UPSERT_FEED = """
INSERT INTO raw.neo_feed_responses (start_date, end_date, payload, source_file)
VALUES (%s, %s, %s::jsonb, %s)
ON CONFLICT (start_date, end_date) DO UPDATE
SET payload = EXCLUDED.payload, source_file = EXCLUDED.source_file, fetched_at = now()
"""
UPSERT_LOOKUP = """
INSERT INTO raw.neo_lookup_responses (asteroid_id, payload)
VALUES (%s, %s::jsonb)
ON CONFLICT (asteroid_id) DO UPDATE
SET payload = EXCLUDED.payload, fetched_at = now()
"""
LOG = """
INSERT INTO raw.ingestion_log (endpoint, start_date, end_date, status, error_message, source_file)
VALUES (%s, %s, %s, %s, %s, %s)
"""


def load_dotenv(path=".env"):
    """Tiny .env reader so the loader works without python-dotenv. Real env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def connect():
    import psycopg  # imported here so --help and the unit tests work without it installed

    load_dotenv()
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("POSTGRES_PASSWORD is not set (put it in .env or the environment).")
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "cosmic_watch"),
        user=os.environ.get("POSTGRES_USER", "cosmic"),
        password=password,
        autocommit=True,  # each file is its own transaction (see conn.transaction() below)
    )


def read_clean(path: Path) -> tuple[str, dict]:
    """Read a JSON file, strip any API key, and return (clean_text, parsed). Raises on bad JSON."""
    text = API_KEY.sub("api_key=REDACTED", path.read_text(encoding="utf-8"))
    return text, json.loads(text)


def load_feed_file(conn, path: Path) -> str:
    m = FEED_NAME.match(path.name)
    if not m:
        raise ValueError("file name must look like feed_YYYY-MM-DD_YYYY-MM-DD.json")
    start, end = m.groups()
    text, data = read_clean(path)
    days = data.get("near_earth_objects")
    if not isinstance(days, dict):
        raise ValueError("no near_earth_objects in file")
    actual = sum(len(v) for v in days.values())
    if data.get("element_count") != actual:
        raise ValueError(f"element_count {data.get('element_count')} != {actual} objects found")
    with conn.transaction():
        conn.execute(UPSERT_FEED, (start, end, text, path.as_posix()))
        conn.execute(LOG, ("feed", start, end, "success", None, path.as_posix()))
    return f"{start}..{end} ({actual} objects)"


def load_lookup_file(conn, path: Path) -> str:
    text, data = read_clean(path)
    asteroid_id = str(data.get("id", ""))
    if not asteroid_id.isdigit():
        raise ValueError(f"unexpected asteroid id {asteroid_id!r}")
    with conn.transaction():
        conn.execute(UPSERT_LOOKUP, (asteroid_id, text))
        conn.execute(LOG, ("lookup", None, None, "success", None, path.as_posix()))
    return f"asteroid {asteroid_id}"


def log_failure(conn, endpoint: str, path: Path, error: Exception):
    m = FEED_NAME.match(path.name)
    start, end = m.groups() if m else (None, None)
    with conn.transaction():
        conn.execute(LOG, (endpoint, start, end, "failed", str(error)[:500], path.as_posix()))


def load_dir(conn, endpoint: str, target: Path) -> tuple[int, int]:
    pattern = "feed_*.json" if endpoint == "feed" else "asteroid_*.json"
    files = sorted(target.rglob(pattern)) if target.is_dir() else [target]
    loader = load_feed_file if endpoint == "feed" else load_lookup_file
    ok = failed = 0
    for f in files:
        try:
            what = loader(conn, f)
            ok += 1
            if endpoint == "feed" or ok % 50 == 0:
                print(f"OK    {f.name}  {what}")
        except Exception as exc:  # keep going: one bad file must not stop the whole load
            failed += 1
            print(f"FAIL  {f.name}: {exc}")
            log_failure(conn, endpoint, f, exc)
    print(f"{endpoint}: {ok} loaded, {failed} failed, {len(files)} files")
    return ok, failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline load", description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["feed", "lookup", "all"])
    ap.add_argument("path", type=Path, help="a folder (searched recursively) or a single JSON file")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.path.exists():
        print(f"Path not found: {args.path}")
        return 2
    failed = 0
    with connect() as conn:
        for endpoint in ("feed", "lookup") if args.what == "all" else (args.what,):
            sub = args.path
            if args.what == "all":
                sub = args.path / ("feed" if endpoint == "feed" else "asteroid_details")
            failed += load_dir(conn, endpoint, sub)[1]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
