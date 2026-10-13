"""Data contracts: check that data looks the way we promised.

Two kinds of check, each driven by a YAML file in contracts/:

  raw     NASA's JSON responses       contracts/neows_browse.yml
  tables  our dim_ / fact_ tables     contracts/silver_tables.yml   (PostgreSQL or CSV files)

The checks exist to catch "string-typed" mistakes: NASA sends numbers as text
("22718.80"), so a number that is still text, or text that is not a number at all
("N/A", "1,234"), must be found before it reaches the tables.

    python -m pipeline.cli contracts raw data/raw
    python -m pipeline.cli contracts tables data/silver        # the CSV copies
    python -m pipeline.cli contracts tables --db               # PostgreSQL schema `analytics`

Exit code 0 = every rule passed, 1 = at least one violation (so CI and Airflow can stop on it).
"""
import argparse
import datetime as dt
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import yaml

MISSING = object()  # marker: the field is not in the JSON at all
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TRUE_TEXT = {"true", "t", "1"}
FALSE_TEXT = {"false", "f", "0"}
MAX_EXAMPLES = 3


# --------------------------------------------------------------------------- results
class Report:
    """Collects violations as (where, rule) -> count + a few example values."""

    def __init__(self):
        self.counts = defaultdict(int)
        self.examples = defaultdict(list)
        self.checked = 0

    def add(self, where, rule, example=None):
        key = (where, rule)
        self.counts[key] += 1
        if example is not None and len(self.examples[key]) < MAX_EXAMPLES:
            self.examples[key].append(example)

    def add_many(self, where, rule, values):
        """Record several bad values at once: counts all of them, keeps a few as examples."""
        values = list(values)
        if values:
            key = (where, rule)
            self.counts[key] += len(values)
            self.examples[key].extend(values[: MAX_EXAMPLES - len(self.examples[key])])

    @property
    def ok(self):
        return not self.counts

    def lines(self):
        for (where, rule), n in sorted(self.counts.items()):
            ex = self.examples.get((where, rule))
            shown = f"  e.g. {', '.join(repr(e)[:60] for e in ex)}" if ex else ""
            yield f"FAIL  {where}: {rule} ({n:,}x){shown}"


def load_contract(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- shared value checks
def _finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool) or _numpy_int(value)


def _numpy_int(value):
    return type(value).__module__ == "numpy" and type(value).__name__.startswith(("int", "uint"))


def _numpy_bool(value):
    return type(value).__module__ == "numpy" and type(value).__name__.startswith("bool")


def _as_float(value):
    """Return float(value) for numbers and numeric text, or None if it is neither."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _range_error(number, rule):
    if "min" in rule:
        low = rule["min"]
        if rule.get("exclusive_min") and number <= low:
            return f"must be greater than {low}"
        if not rule.get("exclusive_min") and number < low:
            return f"must be at least {low}"
    if "max" in rule and number > rule["max"]:
        return f"must be at most {rule['max']}"
    return None


# --------------------------------------------------------------------------- RAW JSON contract
def objects_in(payload):
    """Yield every near-Earth object from a feed, browse or lookup response."""
    neos = payload.get("near_earth_objects") if isinstance(payload, dict) else None
    if isinstance(neos, dict):  # feed: {date: [objects]}
        for day_objects in neos.values():
            yield from day_objects
    elif isinstance(neos, list):  # browse: [objects]
        yield from neos
    elif isinstance(payload, dict) and "id" in payload:  # lookup: one object
        yield payload


def resolve(obj, path):
    """All values at a dotted path. "a[].b" visits every item of list a. MISSING if absent."""
    nodes = [obj]
    for part in path.split("."):
        name, is_list = (part[:-2], True) if part.endswith("[]") else (part, False)
        nxt = []
        for node in nodes:
            value = node.get(name, MISSING) if isinstance(node, dict) else MISSING
            if is_list and isinstance(value, list):
                nxt.extend(value)
            else:
                nxt.append(value)
        nodes = nxt
    return nodes


def check_json_value(value, rule):
    """Return a problem description, or None when the value satisfies the rule."""
    kind = rule["type"]
    if kind == "string":
        return None if isinstance(value, str) and value != "" else "expected text"
    if kind == "digit_string":
        return None if isinstance(value, str) and value.isdigit() else "expected text of digits"
    if kind == "boolean":
        return None if isinstance(value, bool) else "expected true/false"
    if kind == "integer":
        return None if _is_int(value) else "expected a whole number"
    if kind == "number":
        if not _finite_number(value):
            return "expected a number" + (" (got text)" if isinstance(value, str) else "")
        return _range_error(value, rule)
    if kind == "numeric_string":
        if not isinstance(value, str):
            return "expected a number written as text"
        number = _as_float(value) if value.strip() else None
        if number is None:
            return "text is not a number"
        return _range_error(number, rule)
    if kind == "date_string":
        if isinstance(value, str) and DATE_RE.match(value):
            try:
                dt.date.fromisoformat(value)
                return None
            except ValueError:
                pass
        return "expected a date like 2026-07-01"
    return f"unknown type {kind!r} in contract"


def validate_object(contract, obj, report, where="object"):
    for rule in contract["fields"]:
        for value in resolve(obj, rule["path"]):
            report.checked += 1
            if value is MISSING or value is None:
                if not rule.get("nullable"):
                    report.add(f"{where}.{rule['path']}", "missing or null")
                continue
            problem = check_json_value(value, rule)
            if problem:
                report.add(f"{where}.{rule['path']}", problem, value)


def validate_payload(contract, payload, report):
    count = 0
    for obj in objects_in(payload):
        validate_object(contract, obj, report)
        count += 1
    return count


# --------------------------------------------------------------------------- TABLE contract
def check_cell(value, kind, text_mode):
    """True if one table value has the declared type.

    text_mode=True  : values come from CSV text, so numbers are checked by whether they PARSE.
    text_mode=False : values come from a real database, so a number must already BE a number;
                      a number stored as text is exactly the string-typed mistake we catch.
    """
    if kind == "id":
        return isinstance(value, str) and value.isdigit()
    if kind == "string":
        return isinstance(value, str)
    if text_mode:
        text = str(value).strip()
        if kind == "float":
            return _as_float(text) is not None
        if kind == "integer":
            return bool(re.fullmatch(r"[+-]?\d+", text))
        if kind == "boolean":
            return text.lower() in TRUE_TEXT | FALSE_TEXT
        if kind == "date":
            return bool(DATE_RE.match(text)) and _valid_iso(text, dt.date.fromisoformat)
        if kind == "timestamp":
            return _valid_iso(text, dt.datetime.fromisoformat)
        return False
    if kind == "float":
        return _finite_number(value) or _numpy_int(value) or _numpy_float(value)
    if kind == "integer":
        return _is_int(value)
    if kind == "boolean":
        return isinstance(value, bool) or _numpy_bool(value)
    if kind in ("date", "timestamp"):
        return isinstance(value, (dt.date, dt.datetime, pd.Timestamp))
    return False


def _numpy_float(value):
    return type(value).__module__ == "numpy" and type(value).__name__.startswith("float") and math.isfinite(value)


def _valid_iso(text, parser):
    try:
        parser(text)
        return True
    except ValueError:
        return False


def validate_table(name, spec, df, report, text_mode=False, others=None):
    """Check one table (a DataFrame) against its contract entry."""
    columns = spec["columns"]
    for col, rule in columns.items():
        where = f"{name}.{col}"
        if col not in df.columns:
            if not rule.get("optional"):
                report.add(where, "column is missing")
            continue
        series = df[col]
        report.checked += len(series)
        nulls = int(series.isna().sum())
        if nulls and not rule.get("nullable"):
            report.add_many(where, "has empty values (nullable is false)", ["(empty)"] * nulls)
        values = series.dropna()
        if values.empty:
            continue
        bad = values[~values.map(lambda v: check_cell(v, rule["type"], text_mode))]
        if not bad.empty:
            note = " (text where a number is required)" if rule["type"] == "float" and bad.map(lambda v: isinstance(v, str)).any() else ""
            report.add_many(where, f"not a valid {rule['type']}{note}", bad)
        good = values.drop(bad.index)
        if rule["type"] in ("float", "integer") and not good.empty and ("min" in rule or "max" in rule):
            numbers = good.map(_as_float)
            problems = numbers.map(lambda n: _range_error(n, rule) if n is not None else None)
            for message in problems.dropna().unique():
                report.add_many(where, message, numbers[problems == message])
        if "allowed" in rule and not good.empty:
            report.add_many(where, f"not one of {rule['allowed']}", good[~good.isin(rule["allowed"])])

    pk = spec.get("primary_key", [])
    if pk and all(c in df.columns for c in pk):
        duplicated = df[df.duplicated(pk, keep="first")]
        report.add_many(f"{name}.{'+'.join(pk)}", "duplicate primary key", duplicated[pk[0]])

    for fk in spec.get("foreign_keys", []):
        ref_table, ref_col = fk["references"].split(".")
        if others and ref_table in others and fk["column"] in df.columns and ref_col in others[ref_table].columns:
            orphans = df[~df[fk["column"]].isin(others[ref_table][ref_col])]
            report.add_many(f"{name}.{fk['column']}", f"no matching row in {fk['references']}", orphans[fk["column"]])


def validate_tables(contract, frames, text_mode=False):
    report = Report()
    for name, spec in contract["tables"].items():
        if name not in frames:
            report.add(name, "table not found")
            continue
        validate_table(name, spec, frames[name], report, text_mode, others=frames)
    return report


# --------------------------------------------------------------------------- command line
RAW_PATTERNS = ("feed_*.json", "page_*.json", "asteroid_*.json")


def run_raw(args):
    contract = load_contract(args.contract or "contracts/neows_browse.yml")
    root = Path(args.path)
    files = [root] if root.is_file() else sorted(f for pat in RAW_PATTERNS for f in root.rglob(pat))
    if not files:
        print(f"No NASA JSON files found under {root}")
        return 2
    report, objects = Report(), 0
    for f in files:
        try:
            objects += validate_payload(contract, json.loads(f.read_text(encoding="utf-8")), report)
        except (OSError, ValueError) as exc:
            report.add(str(f), f"cannot read file: {exc}")
    print(f"Checked {len(files):,} files, {objects:,} asteroids, {report.checked:,} values against {contract['name']}")
    return finish(report)


def run_tables(args):
    contract = load_contract(args.contract or "contracts/silver_tables.yml")
    frames = {}
    if args.db:
        from pipeline.load_raw import connect

        with connect() as conn:
            for name in contract["tables"]:
                cur = conn.execute(f"SELECT * FROM {args.schema}.{name}")  # names come from our own YAML
                frames[name] = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
        text_mode, source = False, f"PostgreSQL schema {args.schema}"
    else:
        folder = Path(args.path or "data/silver")
        for name in contract["tables"]:
            file = folder / f"{name}.csv"
            if file.exists():
                frames[name] = pd.read_csv(file, dtype=str)  # keep text as text, then check it parses
        text_mode, source = True, str(folder)
    report = validate_tables(contract, frames, text_mode)
    rows = ", ".join(f"{n}={len(f):,} rows" for n, f in frames.items()) or "no tables"
    print(f"Checked {source}: {rows}")
    return finish(report)


def finish(report):
    for line in report.lines():
        print(line)
    if report.ok:
        print("PASS  every contract rule holds")
        return 0
    print(f"FAILED  {len(report.counts)} rule(s) broken")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pipeline contracts", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="what", required=True)
    raw = sub.add_parser("raw", help="check NASA JSON files (feed, browse, lookup)")
    raw.add_argument("path", nargs="?", default="data/raw")
    raw.add_argument("--contract")
    tables = sub.add_parser("tables", help="check dim_asteroids / fact_close_approaches")
    tables.add_argument("path", nargs="?", help="folder with <table>.csv files (default data/silver)")
    tables.add_argument("--db", action="store_true", help="read PostgreSQL instead of CSV files")
    tables.add_argument("--schema", default="analytics")
    tables.add_argument("--contract")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return run_raw(args) if args.what == "raw" else run_tables(args)


if __name__ == "__main__":
    raise SystemExit(main())
