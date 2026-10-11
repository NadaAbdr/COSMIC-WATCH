import json
import re
import subprocess
import sys
from pathlib import Path

PSQL = ["docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", "cosmic", "-d", "cosmic_watch", "-v", "ON_ERROR_STOP=1"]
BATCH = 50  # كم ملف في كل مرة نكلم فيها القاعدة


def build_insert(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"api_key=[A-Za-z0-9]+", "api_key=REDACTED", text)
    data = json.loads(text)
    asteroid_id = str(data["id"])
    if not asteroid_id.isdigit():
        raise ValueError(f"id غريب في {path.name}: {asteroid_id}")
    return (
        "INSERT INTO raw.neo_lookup_responses (asteroid_id, payload) "
        f"VALUES ('{asteroid_id}', $json${text}$json$::jsonb) "
        "ON CONFLICT (asteroid_id) DO UPDATE "
        "SET payload = EXCLUDED.payload, fetched_at = now();"
    )


def run_sql(sql: str) -> None:
    r = subprocess.run(PSQL, input=sql.encode("utf-8"), capture_output=True)
    if r.returncode != 0:
        print("FAIL:", r.stderr.decode("utf-8", "ignore"))
        sys.exit(1)


if __name__ == "__main__":
    target = Path(sys.argv[1])
    files = sorted(target.glob("asteroid_*.json")) if target.is_dir() else [target]
    done = 0
    for i in range(0, len(files), BATCH):
        chunk = files[i:i + BATCH]
        stmts = [build_insert(f) for f in chunk]
        run_sql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;")
        done += len(chunk)
        print(f"OK {done}/{len(files)}")