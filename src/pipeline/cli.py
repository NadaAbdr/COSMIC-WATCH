"""Single entry point for the Cosmic Watch pipeline.

Usage:
    python -m pipeline.cli <command> [options]

Commands:
    extract    Download NeoWs feed data for a date range (raw JSON)
    details    Download orbital details for each asteroid ID
    transform  Turn raw JSON into silver CSVs (dim_asteroids, fact_close_approaches)
    validate   Run quality checks on the silver CSVs
    load       Load raw JSON into PostgreSQL: load feed|lookup|all <path>
    contracts  Check raw JSON or tables against contracts/*.yml: contracts raw|tables <path>

Every command passes its remaining options to the underlying script, e.g.
    python -m pipeline.cli extract --help
"""
import importlib
import sys
from pathlib import Path

COMMANDS = {
    "extract": "pipeline.extract_asteroids",
    "details": "pipeline.extract_asteroid_details",
    "transform": "pipeline.transform_raw",
    "load": "pipeline.load_raw",
    "contracts": "pipeline.contracts",
}
HELP = __doc__


def validate(silver_dir="data/silver"):
    """Quality checks on the silver tables. Returns 0 when all pass, 1 otherwise."""
    import pandas as pd

    silver = Path(silver_dir)
    dim_path = silver / "dim_asteroids.csv"
    fact_path = silver / "fact_close_approaches.csv"
    if not dim_path.exists() or not fact_path.exists():
        print(f"Silver files not found in {silver.resolve()}. Run 'transform' first.")
        return 1

    dim = pd.read_csv(dim_path)
    fact = pd.read_csv(fact_path)

    checks = {
        "dim asteroid_id is unique": dim["asteroid_id"].is_unique,
        "dim asteroid_id has no nulls": not dim["asteroid_id"].isna().any(),
        "no fact rows without a dim asteroid": bool(
            fact["asteroid_id"].isin(dim["asteroid_id"]).all()
        ),
        "fact has no nulls in key columns": not fact[
            ["asteroid_id", "close_approach_date"]
        ].isna().any().any(),
    }
    duplicates = int(
        fact.duplicated(["asteroid_id", "close_approach_date", "velocity_kmh"]).sum()
    )

    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    print(f"INFO  duplicate fact rows: {duplicates}")
    print(f"INFO  dim rows: {len(dim)} | fact rows: {len(fact)}")
    return 0 if all(checks.values()) else 1


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0

    command, rest = args[0], args[1:]

    if command == "validate":
        return validate()

    if command not in COMMANDS:
        print(f"Unknown command '{command}'.\n")
        print(HELP)
        return 2

    module = importlib.import_module(COMMANDS[command])
    sys.argv = [f"pipeline {command}", *rest]  # let the script's own argparse run
    return module.main() or 0


if __name__ == "__main__":
    raise SystemExit(main())