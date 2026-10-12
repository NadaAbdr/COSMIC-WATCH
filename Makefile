# Usage: make <target>.  Needs: Docker, Python 3.10+, `pip install -e .`, `pip install dbt-postgres`.
# On Windows without make, run the commands inside each target by hand (see STEPS.md).
-include .env
export

DBT = cd dbt && dbt

.PHONY: up down schema load transform run test

up:          ## start PostgreSQL in Docker
	docker compose up -d postgres

down:        ## stop PostgreSQL (data is kept in the Docker volume)
	docker compose down

schema:      ## create/refresh the raw tables (safe to run again)
	docker compose exec -T postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -v ON_ERROR_STOP=1 < sql/001_raw_schema.sql

load:        ## raw JSON files -> PostgreSQL raw.* tables
	python -m pipeline.cli load all data/raw

transform:   ## raw.* -> analytics.dim_asteroids / fact_close_approaches (dbt) and run its tests
	$(DBT) build --profiles-dir .

run: up schema load transform   ## everything, in order

test:        ## data quality tests only
	$(DBT) test --profiles-dir .
