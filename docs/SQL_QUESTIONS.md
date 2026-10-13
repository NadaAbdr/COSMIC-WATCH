# Asking the data questions with SQL

Three questions about our asteroid data, each answered with one SQL query. Two bonus questions are at the end.
All queries are in [`sql/analysis/01_questions.sql`](../sql/analysis/01_questions.sql). They read the two finished tables:

- `analytics.dim_asteroids`: one row per asteroid (name, size, orbit class, hazardous flag)
- `analytics.fact_close_approaches`: one row per close approach (date, body, speed, miss distance)

Only `SELECT` queries here, so nothing can be changed or broken.

## How to run them

**Before anything:** Docker Desktop is open and the data is built (`docker compose up -d postgres`, then `python -m pipeline.cli load all data/raw`, then `cd dbt; dbt build --profiles-dir .`).

### Option 1: the whole file in PowerShell (quickest)

From the project folder:

```powershell
Get-Content sql\analysis\01_questions.sql | docker compose exec -T postgres psql -U cosmic -d cosmic_watch
```

This prints all five result tables one after another.

### Option 2: one question at a time

```powershell
docker compose exec postgres psql -U cosmic -d cosmic_watch
```

You get a `cosmic_watch=#` prompt. Paste one query, end it with `;`, press Enter. Type `\q` to leave.
(Use `-U` and `-d` with the values from your own `.env` if you changed them.)

### Option 3: in the dashboard's SQL test page

```powershell
streamlit run apps/streamlit/sql_test.py --server.port 8502
```

This page runs the chosen file as one request and may only show the last result, so use Option 1 or 2 for this file. The page works best with files holding a single query.

### Option 4: BigQuery console (the older cloud copy)

In the console, use the tables `cosmic-watch-de.raw.dim_asteroids` and `cosmic-watch-de.raw.fact_close_approaches`.
Replace `analytics.` with `` `cosmic-watch-de.raw.` `` in the query. Two more changes are needed: BigQuery has no `::numeric` (write `ROUND(x, 3)` directly) and no `FILTER` (use `COUNTIF(is_hazardous)`). Those tables come from the older browse data, so the numbers differ from the ones below.

## Question 1: Which asteroids came closest to Earth?

```sql
SELECT d.name,
       f.close_approach_date,
       ROUND(f.miss_distance_lunar::numeric, 3)  AS miss_distance_ld,
       ROUND(f.miss_distance_km::numeric)        AS miss_distance_km,
       ROUND(d.diameter_max_m::numeric)          AS max_diameter_m,
       d.is_hazardous
FROM analytics.fact_close_approaches AS f
JOIN analytics.dim_asteroids         AS d ON d.asteroid_id = f.asteroid_id
WHERE f.orbiting_body = 'Earth'
ORDER BY f.miss_distance_km ASC
LIMIT 10;
```

**What it does:** joins each approach to its asteroid, keeps only passes by Earth, sorts by distance and keeps the 10 smallest.
1 lunar distance (LD) is about 384,400 km.

**Answer on our test data:** the closest pass is **2019 AS5 on 8 Jan 2019 at 0.039 LD (about 15,000 km)**, a tiny rock about 2 m wide. Second is Duende (2012 DA14) on 15 Feb 2013 at 0.089 LD, about 86 m wide. All ten closest are small (under 100 m) and none is flagged hazardous.

## Question 2: Are "potentially hazardous" asteroids more common in some orbit classes?

```sql
SELECT orbit_class,
       COUNT(*)                                                    AS asteroids,
       COUNT(*) FILTER (WHERE is_hazardous)                        AS hazardous,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_hazardous) / COUNT(*), 1) AS pct_hazardous,
       ROUND(AVG(diameter_max_m)::numeric)                         AS avg_max_diameter_m
FROM analytics.dim_asteroids
GROUP BY orbit_class
ORDER BY pct_hazardous DESC;
```

**What it does:** groups asteroids by orbit class and counts how many carry NASA's hazardous flag. `FILTER (WHERE ...)` counts only the matching rows.

**Answer on our test data:**

| Orbit class | Asteroids | Hazardous | % hazardous |
|---|---|---|---|
| APO (Apollo) | 248 | 43 | 17.3 |
| ATE (Aten) | 116 | 5 | 4.3 |
| AMO (Amor) | 76 | 0 | 0.0 |
| IEO (Atira) | 1 | 0 | 0.0 |

Apollo asteroids are the most often flagged hazardous (17%). Amors never are, which makes sense: they stay outside Earth's orbit. The single Atira is too small a sample to say anything.

## Question 3: In which years do the most close approaches happen, and how fast are they?

```sql
SELECT EXTRACT(YEAR FROM close_approach_date)::int AS year,
       COUNT(*)                                    AS approaches,
       COUNT(DISTINCT asteroid_id)                 AS different_asteroids,
       ROUND(AVG(velocity_kmh)::numeric)           AS avg_speed_kmh
FROM analytics.fact_close_approaches
WHERE orbiting_body = 'Earth'
GROUP BY 1
ORDER BY approaches DESC
LIMIT 10;
```

**What it does:** counts approaches per year (`GROUP BY 1` means "group by the first column") and shows the 10 busiest years.

**Answer on our test data:** **2026 is by far the busiest year (531 approaches by 441 different asteroids, average about 47,100 km/h)**, then 2020 (186) and 2017 (165). 2026 stands out because the daily feed we downloaded covers 2026, while other years only come from each asteroid's known past and future passes. So this shows what our data holds, not how busy space really was. Dates up to 2200 are NASA's computed predictions.

## Bonus A: Which asteroids pass Earth most often?

```sql
SELECT d.name,
       COUNT(*)                          AS approaches,
       ROUND(MIN(f.miss_distance_lunar)::numeric, 2) AS closest_ld
FROM analytics.fact_close_approaches AS f
JOIN analytics.dim_asteroids         AS d ON d.asteroid_id = f.asteroid_id
WHERE f.orbiting_body = 'Earth'
GROUP BY d.name
ORDER BY approaches DESC, closest_ld
LIMIT 10;
```

Top result: **2014 OL339 with 502 approaches**, though its closest pass is a distant 50 LD. 2004 KG1 passes 205 times and gets as close as 0.40 LD.

## Bonus B: Do bigger asteroids pass faster?

The query in the file buckets asteroids by size using `CASE WHEN`. On our data the average speed rises with size: about 40,800 km/h under 50 m, 44,200 km/h for 50-140 m, 58,800 km/h for 140 m to 1 km, and 68,800 km/h above 1 km.
This is a pattern in our data, not proof that size causes speed. Large asteroids are only seen when they are near, and the ones in our set are often on more eccentric orbits.

## Reading the results

- Numbers in this page come from the sample in our repo (441 asteroids, 32,871 approaches). Yours will match if you built from the same `data/raw` files. If the DAG has downloaded more days, they will be slightly larger.
- If a query says `relation "analytics.dim_asteroids" does not exist`, dbt has not been run yet.
- If it says `password authentication failed`, the `-U` or the password in `.env` does not match.
