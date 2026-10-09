import pandas as pd

dim = pd.read_csv("data/silver/dim_asteroids.csv")
fact = pd.read_csv("data/silver/fact_close_approaches.csv")

print("PK unique (dim):", dim["asteroid_id"].is_unique)
print("Orphans (fact without dim):", (~fact["asteroid_id"].isin(dim["asteroid_id"])).sum())
print("Duplicate facts:", fact.duplicated(["asteroid_id", "close_approach_date", "velocity_kmh"]).sum())
print("Nulls:\n", fact.isna().sum())
print(fact["orbiting_body"].value_counts())
print(fact["close_approach_date"].min(), "->", fact["close_approach_date"].max())