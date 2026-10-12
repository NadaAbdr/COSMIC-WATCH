"""Daily NEO pipeline: extract -> details -> load -> dbt build.

Each task calls the existing pipeline CLI / dbt, so no pipeline logic is copied here.
Paths and commands come from environment variables set in compose/airflow.yml.
"""
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# ----------------------------- CONFIG ---------------------------------
PROJECT_DIR = os.environ.get("PROJECT_DIR", "/opt/airflow/project")
CLI = os.environ.get("PIPELINE_CLI", "python -m pipeline.cli")
DBT = os.environ.get("DBT_BIN", "dbt")

RAW_DIR = f"{PROJECT_DIR}/data/raw"
FEED_DIR = f"{RAW_DIR}/feed"
DETAILS_DIR = f"{RAW_DIR}/asteroid_details"
DBT_DIR = f"{PROJECT_DIR}/dbt"

# NASA throttles the Lookup endpoint, so cap how many IDs one run tries.
DETAILS_LIMIT = os.environ.get("DETAILS_LIMIT", "200")

# {{ ds }} is Airflow's run date (YYYY-MM-DD), which date.fromisoformat accepts.
EXTRACT_ARGS = "--start-date {{ ds }} --end-date {{ ds }} --output-dir " + FEED_DIR
DETAILS_ARGS = f"--input-dir {FEED_DIR} --output-dir {DETAILS_DIR} --limit {DETAILS_LIMIT}"
LOAD_ARGS = f"all {RAW_DIR}"  # positional: {feed,lookup,all} <path>
# ----------------------------------------------------------------------

default_args = {
    "owner": "cosmic-watch",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
}

with DAG(
    dag_id="daily_neo",
    description="Daily NeoWs extract, load and dbt build",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule="@daily",
    catchup=False,  # do not backfill days of NASA requests
    max_active_runs=1,
    tags=["cosmic-watch", "neows"],
) as dag:

    # NASA_API_KEY and POSTGRES_* come from the container environment (.env).
    # They are never written in this file.
    extract = BashOperator(
        task_id="extract",
        bash_command=f"cd {PROJECT_DIR} && {CLI} extract {EXTRACT_ARGS}",
    )

    details = BashOperator(
        task_id="details",
        bash_command=f"cd {PROJECT_DIR} && {CLI} details {DETAILS_ARGS}",
        retries=3,
        retry_delay=timedelta(minutes=15),  # give the rate limit time to reset
    )

    # load is an upsert, so re-running a day never creates duplicates.
    load = BashOperator(
        task_id="load",
        bash_command=f"cd {PROJECT_DIR} && {CLI} load {LOAD_ARGS}",
    )

    # dbt build runs the models AND their tests. A failed test turns the run red.
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=f"cd {DBT_DIR} && {DBT} build --profiles-dir {DBT_DIR}",
        retries=0,  # a data-quality failure will not fix itself on retry
    )

    extract >> details >> load >> dbt_build