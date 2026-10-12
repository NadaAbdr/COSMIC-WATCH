import json
import re
import subprocess
import sys
from pathlib import Path

PSQL = ["docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", "cosmic", "-d", "cosmic_watch", "-v", "ON_ERROR_STOP=1"]
NAME = re.compile(r"^feed_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json$")


def load_file(path: Path) -> None:
    m = NAME.match(path.name)
    if not m:
        print(f"SKIP (مو ملف feed): {path.name}")
        return
    start, end = m.groups()

    text = path.read_text(encoding="utf-8")
    # نشيل مفتاح NASA لو ظهر في الروابط
    text = re.sub(r"api_key=[A-Za-z0-9]+", "api_key=REDACTED", text)
    data = json.loads(text)  # يتأكد إن الملف JSON سليم قبل الإدخال

    sql = f"""
INSERT INTO raw.neo_feed_responses (start_date, end_date, payload, source_file)
VALUES ('{start}', '{end}', $json${text}$json$::jsonb, '{path.as_posix()}')
ON CONFLICT (start_date, end_date) DO UPDATE
SET payload = EXCLUDED.payload,
    source_file = EXCLUDED.source_file,
    fetched_at = now();
"""
    r = subprocess.run(PSQL, input=sql.encode("utf-8"), capture_output=True)
    if r.returncode != 0:
        print(f"FAIL {path.name}: {r.stderr.decode('utf-8', 'ignore')}")
        sys.exit(1)
    print(f"OK   {path.name} | element_count={data.get('element_count')}")


if __name__ == "__main__":
    target = Path(sys.argv[1])
    files = sorted(target.rglob("feed_*.json")) if target.is_dir() else [target]
    for f in files:
        load_file(f)