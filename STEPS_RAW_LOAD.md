# Cosmic Watch: raw schema, JSON loaders and validation (PostgreSQL)

Commands are for **PowerShell on Windows**, run from the project root (the folder that contains `docker-compose.yml`).
Everything below was run and checked by hand. Expected results are listed after each step.

## What this covers

| File | What it does |
|---|---|
| `sql/001_raw_schema.sql` | Creates schema `raw` and 3 tables (the Bronze layer: data exactly as NASA returned it). |
| `src/load_feed.py` | Loads weekly feed JSON files into `raw.neo_feed_responses`. |
| `src/load_lookup.py` | Loads one JSON file per asteroid into `raw.neo_lookup_responses`. |

Both loaders use only the Python standard library (no `pip install` needed). They send SQL to PostgreSQL through
`docker compose exec ... psql`, so Docker must be running. They are hard-coded to user `cosmic` and database `cosmic_watch`.

> **Note:** `STEPS.md` describes the newer team loader `src/pipeline/load_raw.py`, which loads the same raw tables and,
> according to that file, replaces these two scripts. This document records how the raw layer was built and validated.

## Why the raw tables look like this

| Decision | Reason |
|---|---|
| Store each API response as-is in a `payload jsonb` column | Keep a copy of the source. Fix parsing mistakes later in dbt without calling the API again (it has request limits). |
| One table for feed responses, one for asteroid lookups | They have different shapes: a feed response covers a date range, a lookup describes one asteroid. |
| `UNIQUE (start_date, end_date)` on the feed table | Each date window can exist only once, which is what makes the upsert work. |
| `PRIMARY KEY (asteroid_id)` on the lookup table | Each asteroid exists only once. |
| Upsert (`INSERT ... ON CONFLICT DO UPDATE`) | Re-running a load updates rows instead of duplicating them (idempotent). |
| Replace `api_key=...` with `api_key=REDACTED` before saving | The API links inside responses contain the NASA key. It must never reach the database or Git. |

## Step 1. Start PostgreSQL

```powershell
docker compose up -d
docker compose ps
```
Expected: the `postgres` container is `running`.

## Step 2. Create the raw schema and tables

```powershell
Get-Content sql\001_raw_schema.sql | docker compose exec -T postgres psql -U cosmic -d cosmic_watch -v ON_ERROR_STOP=1
docker compose exec postgres psql -U cosmic -d cosmic_watch -c "\dt raw.*"
```
Expected: 3 tables: `raw.ingestion_log`, `raw.neo_feed_responses`, `raw.neo_lookup_responses`.

Why `Get-Content ... |`: `psql -f` looks for the file **inside the container**, and the `<` operator does not work in PowerShell.

## Step 3. Look at the files before loading

```powershell
dir data\raw
dir data\raw\feed\extract_3_months\2026-07-01_2026-09-30_w7
(Get-ChildItem data\raw\feed\extract_3_months -Recurse -File).Count
dir data\raw\asteroid_details | select -First 5
```
What this shows:
- The 3-month extract (`2026-07-01` to `2026-09-30`) is **14 weekly files**, because NASA allows at most 7 days per request.
  Files are named `feed_<start>_<end>.json`; the loader reads the dates from the file name.
- The folder has 17 files in total: the 14 feed files, an extraction log, and other helper files.
- The extraction log is **JSON Lines** (one JSON object per line). `ConvertFrom-Json` fails on it. The loader only reads files named `feed_*.json`.
- Asteroid details are one file per asteroid: `asteroid_<id>.json`.

## Step 4. Load the feed

Try one file first:
```powershell
python src\load_feed.py data\raw\feed\extract_3_months\2026-07-01_2026-09-30_w7\feed_2026-07-01_2026-07-07.json
```
Expected: `OK   feed_2026-07-01_2026-07-07.json | element_count=32`

Then the whole folder:
```powershell
python src\load_feed.py data\raw\feed\extract_3_months\2026-07-01_2026-09-30_w7
```
Expected: 14 lines starting with `OK` and no `FAIL`.
`element_count` per window: 32, 29, 34, 39, 27, 33, 23, 31, 42, 30, 39, 37, 34, 5 (total 435).

What the loader does: reads the dates from the file name, redacts `api_key`, checks the file is valid JSON, then upserts on `(start_date, end_date)`.

## Step 5. Load the asteroid details

One file first, then the folder:
```powershell
python src\load_lookup.py data\raw\asteroid_details\asteroid_2001620.json
python src\load_lookup.py data\raw\asteroid_details
```
Expected end of output: `OK 435/435` and no `FAIL`.

What the loader does: reads `id` from inside each file, upserts on `asteroid_id`, and sends files in batches of 50 inside
`BEGIN; ... COMMIT;`. A batch is saved completely or not at all, and fewer database connections are opened.

## Step 6. Validate the load

"OK" from a script only means it ran without an error. These checks show the data is complete and correct.

**a) Row count: events in the database match the extraction log**
```powershell
docker compose exec postgres psql -U cosmic -d cosmic_watch -c "SELECT count(*) AS windows, sum((payload->>'element_count')::int) AS total_records FROM raw.neo_feed_responses;"
docker compose exec postgres psql -U cosmic -d cosmic_watch -c "SELECT sum(jsonb_array_length(arr)) AS events FROM raw.neo_feed_responses f, jsonb_each(f.payload->'near_earth_objects') AS d(day, arr);"
```
Expected: `windows = 14`, `total_records = 435`, `events = 435` (the log's per-window `records` add up to 435).

**b) Referential integrity: every asteroid in the feed has details**
```powershell
docker compose exec postgres psql -U cosmic -d cosmic_watch -c "WITH ids AS (SELECT DISTINCT neo->>'id' AS asteroid_id FROM raw.neo_feed_responses f, jsonb_each(f.payload->'near_earth_objects') AS d(day, arr), jsonb_array_elements(d.arr) AS neo) SELECT count(*) AS feed_asteroids, count(l.asteroid_id) AS have_details, count(*) - count(l.asteroid_id) AS missing FROM ids LEFT JOIN raw.neo_lookup_responses l USING (asteroid_id);"
```
Expected: `feed_asteroids = 435`, `have_details = 435`, `missing = 0`.

**c) Sample check against the original file**
```powershell
docker compose exec postgres psql -U cosmic -d cosmic_watch -c "SELECT asteroid_id, payload->>'name' FROM raw.neo_lookup_responses WHERE asteroid_id = '2001620';"
```
Expected: `1620 Geographos (1951 RA)`. Open `data\raw\asteroid_details\asteroid_2001620.json` and compare the `name` field.

**d) Re-run safety (idempotency)**
```powershell
docker compose exec postgres psql -U cosmic -d cosmic_watch -c "SELECT 'feed' t, count(*) FROM raw.neo_feed_responses UNION ALL SELECT 'lookup', count(*) FROM raw.neo_lookup_responses;"
```
Run it once (expected `feed 14`, `lookup 435`), run the Step 4 and Step 5 folder commands again, then run it a second time.
The numbers must be identical.

**e) No API key reached the database**
```powershell
docker compose exec postgres psql -U cosmic -d cosmic_watch -t -c "SELECT count(*) FROM raw.neo_feed_responses WHERE payload::text ~ 'api_key=[A-Za-z0-9]+' AND payload::text !~ 'api_key=REDACTED';"
```
Expected: `0`.

Check the files on disk too (this must print nothing):
```powershell
Get-ChildItem data\raw -Recurse -File | Select-String -Pattern "api_key=" | Where-Object { $_.Line -notmatch "api_key=REDACTED" } | Select-Object -First 3 Path
```

> The expected numbers assume only the 3-month folder and `data\raw\asteroid_details` were loaded with these scripts.
> If the team loader was also run on all of `data\raw`, `STEPS.md` expects 17 feed rows, so the counts will be higher.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `role "postgres" does not exist` | The database user is `cosmic`. PostgreSQL creates the user from the container settings only the first time. | Use `-U cosmic` in every command. |
| `Did not find any relations` | The tables are in schema `raw`, not `public`. | Use `\dt raw.*`. |
| `ConvertFrom-Json` fails on a file | It is the extraction log (JSON Lines). | Read only `feed_*.json` files. |
| `Get-Content ... Path is null` | `feed\` contains only folders. | Add `-Recurse`: `Get-ChildItem ... -Recurse -File`. |
| `Select-String -Recurse` is not found | PowerShell has no such option. | `Get-ChildItem -Recurse -File \| Select-String ...` |

## Known limitations and open items

- These loaders do **not** write to `raw.ingestion_log` (the team loader does, according to `STEPS.md`).
- `UNIQUE (start_date, end_date)` stops identical windows from repeating, but does **not** stop different windows that overlap
  (for example `2026-09-02..09-08` and `2026-09-01..09-07`). Overlapping windows could hold the same event twice.
- In the 3-month data every asteroid appears once (435 asteroids, 435 events). This may change if the date range is extended,
  so do not assume asteroid count equals event count.
- Whether `asteroid_id + approach time + orbiting body` is a unique key for events has not been tested on the data yet.
  Test it before writing a dbt `unique` test for the events table.
- The loaders have the database user and name hard-coded and only work against the local Docker database.
  Pointing them at a cloud database needs a connection change.