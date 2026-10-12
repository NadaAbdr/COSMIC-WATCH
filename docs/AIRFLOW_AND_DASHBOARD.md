# COSMIC-WATCH: Airflow pipeline and Streamlit dashboard

This page explains what was added on top of the raw loader and dbt models, how to see the dashboard on your own computer, and what the dashboard shows.

**In one paragraph:** an Airflow DAG (`daily_neo`) now runs the whole pipeline in one go (download from NASA, load into PostgreSQL, build the dbt tables), and a Streamlit dashboard reads the finished `analytics` tables and shows them as charts. Both run on your own computer. There is no shared website yet (see [Is it live?](#is-it-live-does-it-update-daily)).

---

## 1. What was added, and why

| File | What it does | Why |
|---|---|---|
| `dags/daily_neo.py` | Airflow DAG: `extract` → `details` → `load` → `dbt_build`, scheduled daily | Replaces running four commands by hand. Each task calls the existing CLI, so no pipeline logic is duplicated. |
| `Dockerfile.airflow` | Airflow image with the pipeline package and dbt installed in their own virtual environment | Airflow pins its own package versions. A separate environment stops dbt and pandas from clashing with them. |
| `compose/airflow.yml` | Adds an `airflow` service next to the existing `postgres` service | One command starts the database and Airflow together, and Airflow can reach the database by the name `postgres`. |
| `apps/streamlit/app.py` | The dashboard | Shows the `analytics.dim_asteroids` and `analytics.fact_close_approaches` tables visually. |
| `dbt/models/marts/schema.yml` | Fixed the `relationships` test (it was placed at the wrong indentation level) | dbt refused to parse the file (exit code 2), so `dbt build` could not run at all. |
| `dbt-postgres>=1.10` in `pyproject.toml` | Raised the minimum dbt version | The `arguments:` test syntax used in `schema.yml` needs dbt 1.10 or newer. |

**What the DAG does each day**

| Task | What happens | Notes |
|---|---|---|
| `extract` | Downloads the NASA feed for the run date (one day) into `data/raw/feed` | Needs `NASA_API_KEY` |
| `details` | Downloads orbit and size details for asteroids not fetched yet, up to 200 per run | Capped because NASA throttles the lookup endpoint. Retries by itself. |
| `load` | Loads the raw JSON into PostgreSQL (`raw` schema). Safe to re-run: it upserts. | |
| `dbt_build` | Builds the `analytics` tables and runs the data tests | A failed test turns the run red. No retry, because bad data will not fix itself. |

---

## 2. See the dashboard on your own computer

You need: **Docker Desktop**, **Python 3.10+**, **Git**. The commands below are for PowerShell on Windows. On Mac or Linux use `source .venv/bin/activate` instead of `.venv\Scripts\Activate.ps1`, and `export POSTGRES_PASSWORD=...` instead of `$env:POSTGRES_PASSWORD = "..."`.

### Option A: quickest (no NASA key needed)

The repo contains a sample of raw NASA data in `data/raw`, enough for a working dashboard.

```powershell
git pull
git checkout load-and-dbt          # or main, once the pull request is merged

Copy-Item .env.example .env        # then open .env and set POSTGRES_PASSWORD
docker compose up -d postgres      # a brand-new database creates the raw tables by itself

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dbt]"
pip install streamlit

python -m pipeline.cli load all data\raw     # raw JSON -> PostgreSQL

$env:POSTGRES_PASSWORD = "the-password-from-your-.env"
cd dbt
dbt build --profiles-dir .                   # builds the analytics tables, runs the tests
cd ..

streamlit run apps/streamlit/app.py
```

Open **http://localhost:8501**. Leave that terminal open while you use the dashboard, and press `Ctrl+C` to stop it.

### Option B: with Airflow (fresh data from NASA)

Get your own free key at **api.nasa.gov** and put it in `.env` as `NASA_API_KEY=...`, together with `POSTGRES_PASSWORD`. Then:

```powershell
docker compose -f docker-compose.yml -f compose/airflow.yml up --build
```

The first build takes a few minutes (it installs dbt). Then:

1. Open **http://localhost:8080** and log in as `admin`. The generated password is printed in the container logs, or run:
   ```powershell
   docker compose -f docker-compose.yml -f compose/airflow.yml exec airflow cat /opt/airflow/standalone_admin_password.txt
   ```
2. Switch on the toggle next to `daily_neo`. New DAGs start paused.
3. Press the play button and choose **Trigger DAG**. Open the **Grid** tab to watch the four tasks turn green. The first run takes around 10 minutes, mostly in `details`.
4. When all four are green, start the dashboard as in Option A (the last command only: `streamlit run apps/streamlit/app.py`).

### Rules

- **Never commit `.env`.** It holds your key and password. It is already in `.gitignore`.
- **Do not commit refreshed `data/raw` files.** Running the pipeline rewrites them, and NASA echoes your API key inside some of the downloaded URLs. Stage files by name (`git add <file>`), not with `git add .` or `git commit -a`.
- **On Windows, `extract` and `details` cannot run directly.** They use `fcntl`, which only exists on Linux and Mac. Run them through Docker or Airflow (Option B).

---

## 3. What the dashboard shows

The dashboard reads two tables built by dbt: `analytics.dim_asteroids` (one row per asteroid: size, hazard flag, orbit class) and `analytics.fact_close_approaches` (one row per close approach: date, speed, miss distance).

**Sidebar filters:** a date range (default: one year back to one year ahead), the body being passed (default: Earth), and a switch for potentially hazardous asteroids only. **Reload data** fetches fresh data from the database.

| What you see | What it means |
|---|---|
| **Headline numbers** | Asteroids in view, number of close approaches, how many are flagged potentially hazardous, the closest pass (in lunar distances, 1 = the distance to the Moon) and the fastest speed (km/h). |
| **Close approaches per month** | How many close approaches fall in each month. *Read this as data coverage, not as how busy the sky was:* months covered by the weekly NASA feed look much busier than the rest, which only include asteroids whose details were fetched. |
| **How fast do they travel?** | A histogram of relative speeds. Most asteroids pass at roughly 20,000 to 60,000 km/h. |
| **Size versus miss distance** | Each dot is one close approach. Both axes use a log scale. Red dots are potentially hazardous. Bigger and closer (top left) is the interesting corner. Up to 3,000 dots are drawn. |
| **15 closest approaches** | The nearest passes in the selected window, with date, distance, speed and diameter. |
| **Asteroids by orbit class** | Apollo (APO), Aten (ATE), Amor (AMO) and Atira (IEO) groups, which describe how the orbit crosses Earth's. |

**Good to know:** "potentially hazardous" is NASA's label for large asteroids that come close to Earth's orbit. It does not mean an impact is expected.

---

## 4. Is it live? Does it update daily?

**Short answer: not yet. It is a local, on-demand dashboard, not a live shared one.**

- **There is no shared website.** The dashboard runs on each person's own computer at `localhost:8501`, and reads that person's own local PostgreSQL. Nobody can open anybody else's dashboard.
- **The DAG is scheduled daily, but only while it is running.** Airflow works only while Docker and the `airflow` container are running on that computer. If the computer is off, that day is skipped.
- **Missed days are not filled in later.** The DAG uses `catchup=False` on purpose, so it does not fire hundreds of NASA requests after a long break. For example, 2 to 10 October are missing from the current data.
- **Each run is small.** It fetches one day of the feed (the day before the run) and up to 200 new asteroid details, so the detail backlog shrinks gradually.
- **The dashboard does not refresh itself.** It caches data for 10 minutes. After a DAG run finishes, press **Reload data** or refresh the browser.
- **Option A users see a snapshot.** The sample data in the repo does not change until they run the pipeline with their own key.

**What it would take to make it truly live** (a possible next step, not done yet):

1. A hosted PostgreSQL (for example Cloud SQL) instead of one per laptop.
2. The daily pipeline run on a schedule in the cloud (Airflow, or Cloud Scheduler triggering the existing `cosmic-pipeline` Cloud Run job). That job currently only prints `--help`.
3. The dashboard hosted somewhere with a shared link (for example Cloud Run).
4. Passwords and the NASA key in Secret Manager.

---

## 5. Troubleshooting

| Problem | What to do |
|---|---|
| Port 5432, 8080 or 8501 already in use | Stop the other program, or change the left-hand port number in the compose file. |
| Airflow banner: "scheduler does not appear to be running" | Usually normal. In this single-container setup, a long task (`details`) can make the heartbeat late. Check the task log. |
| `dbt_build` is red | Click the task, open **Logs**. Exit code 2 means a setup or parse error, exit code 1 means a data test failed. After fixing, use **Clear task** to re-run just that task. |
| Dashboard says "Could not load data from PostgreSQL" | Check that `docker compose up -d postgres` is running, that dbt has built the analytics tables, and that `.env` has `POSTGRES_PASSWORD`. |
| Dashboard opens but shows "tables are empty" | Run the pipeline first (Option A steps `load` and `dbt build`, or trigger the DAG). |
| psql output stuck on a `:` prompt | Press `q`. Or add `-P pager=off` to the psql command. |
