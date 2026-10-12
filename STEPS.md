# Cosmic Watch: load raw JSON into PostgreSQL, then build tables with dbt

Commands are for **PowerShell in VS Code on Windows** (open the terminal in the project root, the folder that contains `Makefile`). Where a `make` shortcut exists it is shown at the end of the step.

## What changed (and why)
| File | What it does |
|---|---|
| `src/pipeline/load_raw.py` | One loader for feed + lookup JSON. Replaces `src/load_feed.py` and `src/load_lookup.py` (you can delete those two). Reads settings from `.env`, redacts API keys, logs every file to `raw.ingestion_log`, safe to re-run. |
| `src/pipeline/cli.py` | `python -m pipeline.cli load ...` now works (it used to say "not implemented"). |
| `dbt/` | dbt project: `dim_asteroids`, `fact_close_approaches` built from the raw tables, with 13 tests. |
| `.env.example` | Now has the POSTGRES_* variables docker-compose needs. |
| `docker-compose.yml` | Mounts `sql/` so a brand-new database creates the raw tables by itself. |
| `Makefile` | `make up / schema / load / transform / run / test` (ticket COS-24). |
| `scripts/scrub_api_key.py` | Removes the NASA key still sitting in 35 files. |

## Step 1. Put the files in your repo (VS Code)
1. `git pull` first, so you have your friend's latest work.
2. `git checkout -b load-and-dbt`
3. Unzip `cosmic-watch-load-and-dbt.zip` into the project root and choose **replace** when asked. Same folder layout as the repo.

## Step 2. Remove the leaked API key from the files
```powershell
python scripts\scrub_api_key.py data\raw
git status
```
Expect "Scrubbed 35 file(s)". Make sure the **old** key is revoked at api.nasa.gov (the key starting `VpWCVWb`). It stays in git history, so revoking is what actually protects you.

## Step 3. Create your `.env`
```powershell
Copy-Item .env.example .env
```
Open `.env` and set `POSTGRES_PASSWORD` and your new `NASA_API_KEY`. `.env` is git-ignored. Do not commit it.

## Step 4. Start PostgreSQL and create the raw tables
```powershell
docker compose up -d postgres
Get-Content sql\001_raw_schema.sql | docker compose exec -T postgres psql -U cosmic -d cosmic_watch -v ON_ERROR_STOP=1
```
(Use your own `POSTGRES_USER` / `POSTGRES_DB` if you changed them.) Safe to run twice. `make up schema` does the same.
Check: `docker compose exec postgres psql -U cosmic -d cosmic_watch -c "\dt raw.*"` should list 3 tables.

## Step 5. Install the Python packages
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dbt]"
```

## Step 6. Load the raw JSON
```powershell
python -m pipeline.cli load all data\raw
```
Expected end of output: `feed: 17 loaded, 0 failed` and `lookup: 435 loaded, 0 failed`. Run it twice. The counts must not change (no duplicates). `make load` does the same.

Check:
```powershell
docker compose exec postgres psql -U cosmic -d cosmic_watch -c "select count(*) from raw.neo_feed_responses; select count(*) from raw.neo_lookup_responses; select status, count(*) from raw.ingestion_log group by 1;"
```

## Step 7. Build the analytics tables with dbt
```powershell
$env:POSTGRES_PASSWORD = "the-password-from-your-.env"
cd dbt
dbt build --profiles-dir .
cd ..
```
Expected: 1 view + 2 tables built and **13 tests passed**. `make transform` does the same (it reads `.env` for you).

Check:
```powershell
docker compose exec postgres psql -U cosmic -d cosmic_watch -c "select count(*) from analytics.dim_asteroids; select count(*) from analytics.fact_close_approaches;"
```
With the data in the repo today: **441 asteroids** and **32,871 close approaches**.

## Step 8. Commit and open a pull request
```powershell
git add -A
git status      # make sure .env is NOT listed
git commit -m "Add raw loader, dbt models, env example, Makefile"
git push -u origin load-and-dbt
```
Then open a PR on GitHub and link the Linear tickets (COS-24 Makefile, COS-25 schema, COS-16..21 as relevant).

## Later: the same thing in Google Cloud
The `cosmic-pipeline` Cloud Run job runs your Docker image. To use the new loader there you need (1) a Cloud SQL for PostgreSQL instance, (2) the image rebuilt and pushed (`pipeline:v3`) so it includes `load_raw.py`, and (3) `POSTGRES_*` set as env variables / secrets on the job. Ask me when you reach this step.

## Things to know
- The silver CSVs and the BigQuery tables (1,000 asteroids) come from the **browse** endpoint. The Postgres tables come from **feed + lookup** (441 asteroids). They are different sets of asteroids, so don't expect the numbers to match. Only 9 asteroids overlap, and those agree.
- NASA gives `orbiting_body` as short codes like `Merc` and `Juptr`. They are kept as-is in the tables.
- The old loaders `src/load_feed.py` and `src/load_lookup.py` still work but are replaced. Delete them once you've tried the new one.
