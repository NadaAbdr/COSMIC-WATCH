"""Replace any NASA API key found in JSON files with api_key=REDACTED (edits files in place).

    python scripts/scrub_api_key.py data/raw

Run it, commit, push. The old key stays in git history, so ALSO revoke it at api.nasa.gov.
"""
import re
import sys
from pathlib import Path

KEY = re.compile(r"api_key=(?!REDACTED)[A-Za-z0-9_-]+")
root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
changed = 0
for f in root.rglob("*"):
    if f.is_file() and f.suffix in {".json", ".jsonl"}:
        text = f.read_text(encoding="utf-8")
        new, n = KEY.subn("api_key=REDACTED", text)
        if n:
            f.write_text(new, encoding="utf-8", newline="")
            changed += 1
print(f"Scrubbed {changed} file(s) under {root}")
