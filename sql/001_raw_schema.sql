-- sql/001_raw_schema.sql
-- الطبقة الخام (raw): نخزن رد الـ API كما هو بدون تعديل.
-- الملف آمن للتشغيل أكثر من مرة (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS raw;

-- 1) صف لكل رد feed (نطاق تواريخ واحد)
CREATE TABLE IF NOT EXISTS raw.neo_feed_responses (
    id          BIGSERIAL PRIMARY KEY,
    start_date  DATE        NOT NULL,
    end_date    DATE        NOT NULL,
    payload     JSONB       NOT NULL,   --عامود payload من نوع JSONB
    source_file TEXT,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_neo_feed_range UNIQUE (start_date, end_date), --منع التكرار 
    CONSTRAINT chk_feed_range CHECK (end_date >= start_date)
);

-- 2) صف لكل كويكب (تفاصيل lookup)
CREATE TABLE IF NOT EXISTS raw.neo_lookup_responses (
    asteroid_id TEXT        PRIMARY KEY,
    payload     JSONB       NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3) صف لكل محاولة سحب (نجحت أو فشلت)
CREATE TABLE IF NOT EXISTS raw.ingestion_log (
    id            BIGSERIAL PRIMARY KEY,
    endpoint      TEXT        NOT NULL,
    start_date    DATE,
    end_date      DATE,
    status        TEXT        NOT NULL CHECK (status IN ('success', 'failed')),
    http_status   INT,
    error_message TEXT,
    source_file   TEXT,
    attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);