"""
Silver step: read the team's raw NeoWs 'browse' pages (Bronze) -> clean CSVs.

Input : data/raw/<run_id>/page_XXXXX.json   (from extract_asteroids.py)
Output: data/silver/dim_asteroids.csv
        data/silver/fact_close_approaches.csv

Run:  pip install pandas
      python transform_raw.py
"""
import json
from pathlib import Path

import pandas as pd

RAW = Path("data/raw")
SILVER = Path("data/silver")


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main():
    files = sorted(RAW.glob("*/page_*.json"))
    if not files:
        raise SystemExit(f"No page_*.json files found under {RAW.resolve()}")

    dim, fact = {}, {}
    for f in files:
        payload = json.loads(f.read_text(encoding="utf-8"))
        for a in payload["near_earth_objects"]:
            aid = a["id"]
            diam = a.get("estimated_diameter", {}).get("meters", {})
            orbit = a.get("orbital_data") or {}
            # dict keyed by id -> duplicates across runs are removed (idempotent)
            dim[aid] = {
                "asteroid_id": aid,
                "name": a.get("name"),
                "absolute_magnitude": a.get("absolute_magnitude_h"),
                "diameter_min_m": diam.get("estimated_diameter_min"),
                "diameter_max_m": diam.get("estimated_diameter_max"),
                "is_hazardous": a.get("is_potentially_hazardous_asteroid"),
                "is_sentry": a.get("is_sentry_object"),
                "orbit_class": (orbit.get("orbit_class") or {}).get("orbit_class_type"),
                "eccentricity": to_float(orbit.get("eccentricity")),
                "semi_major_axis_au": to_float(orbit.get("semi_major_axis")),
            }
            for ca in a.get("close_approach_data", []):
                key = (aid, ca.get("close_approach_date_full") or ca.get("close_approach_date"))
                fact[key] = {
                    "asteroid_id": aid,
                    "close_approach_date": ca.get("close_approach_date"),
                    "orbiting_body": ca.get("orbiting_body"),
                    "velocity_kmh": to_float(ca["relative_velocity"]["kilometers_per_hour"]),
                    "miss_distance_km": to_float(ca["miss_distance"]["kilometers"]),
                    "miss_distance_lunar": to_float(ca["miss_distance"]["lunar"]),
                }

    SILVER.mkdir(parents=True, exist_ok=True)
    dim_df = pd.DataFrame(dim.values())
    fact_df = pd.DataFrame(fact.values())
    dim_df.to_csv(SILVER / "dim_asteroids.csv", index=False)
    fact_df.to_csv(SILVER / "fact_close_approaches.csv", index=False)

    print("Files read:", len(files))
    print("dim_asteroids rows:", len(dim_df))
    print("fact_close_approaches rows:", len(fact_df))


if __name__ == "__main__":
    main()