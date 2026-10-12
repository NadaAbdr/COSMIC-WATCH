# 🚀 Raw Schema Creation & Data Loading (PostgreSQL)

This document covers creating the raw schema in PostgreSQL, loading raw JSON response files (Feed & Lookup), and validating the raw layer data.

---

## 1. Database & Raw Schema Setup
Create the `raw` schema and its required tables (`neo_feed_responses`, `neo_lookup_responses`, and `ingestion_log`):

```bash
docker compose up -d postgres
Get-Content sql\001_raw_schema.sql | docker compose exec -T postgres psql -U cosmic -d cosmic_watch -v ON_ERROR_STOP=1