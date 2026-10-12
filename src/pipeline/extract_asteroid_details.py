"""Collect unique IDs from Feed JSON and extract their NeoWs Lookup details."""

import argparse
import getpass
import json
import math
import os
import random
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

# Reuse helpers from the date-based extractor in the same folder.
# Works both as a package (python -m pipeline.cli) and as a plain script.
try:
    from .extract_asteroids import (
        ExtractionError,
        TRANSIENT_STATUS,
        atomic_json,
        file_digest,
        remove_links,
        retry_delay,
        utc_now,
    )
except ImportError:
    from extract_asteroids import (
        ExtractionError,
        TRANSIENT_STATUS,
        atomic_json,
        file_digest,
        remove_links,
        retry_delay,
        utc_now,
    )

# Works both as a package (python -m pipeline.cli) and as a plain script.
try:
    from .proclock import lock_nonblocking
except ImportError:
    from proclock import lock_nonblocking


BASE_URL = "https://api.nasa.gov/neo/rest/v1/neo"


def collect_ids(source):
    """Read Feed files and collect unique asteroid IDs."""
    # Read one file at a time to reduce memory usage
    files = sorted(source.rglob("feed_*.json"))

    if not files:
        raise ExtractionError(f"No Feed JSON files found in {source}.")

    ids = set()
    records = 0

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        objects = payload.get("near_earth_objects")

        if not isinstance(objects, dict):
            raise ExtractionError(f"Invalid Feed response: {path.name}")

        for asteroids in objects.values():
            if not isinstance(asteroids, list):
                raise ExtractionError(f"Invalid records: {path.name}")

            for asteroid in asteroids:
                asteroid_id = str(asteroid.get("id", ""))

                if not asteroid_id.isascii() or not asteroid_id.isdigit():
                    raise ExtractionError(
                        f"Invalid asteroid ID: {path.name}"
                    )

                ids.add(asteroid_id)
                records += 1

    print(
        f"Files read: {len(files)}; "
        f"records: {records}; unique IDs: {len(ids)}"
    )

    return sorted(ids, key=int)


def fetch_details(api_key, asteroid_id, args, record):
    """Fetch one asteroid's details with retries and rate-limit handling."""
    query = urlencode({"api_key": api_key})
    failures = 0
    rate_waits = 0

    while True:
        record("request")

        try:
            with urlopen(
                f"{BASE_URL}/{asteroid_id}?{query}",
                timeout=args.timeout,
            ) as response:
                payload = json.load(response)
                remaining = response.headers.get(
                    "X-RateLimit-Remaining"
                )
                cooldown = retry_delay(
                    response.headers,
                    args.rate_wait,
                )

            # Confirm that NASA returned the requested asteroid
            if (
                not isinstance(payload, dict)
                or str(payload.get("id")) != asteroid_id
            ):
                raise ValueError("Unexpected asteroid response")

            return payload, remaining, cooldown

        except HTTPError as error:
            # Detect rate-limit errors without exposing the request URL
            try:
                body = json.loads(error.read(65536))
                limited = (
                    isinstance(body, dict)
                    and isinstance(body.get("error"), dict)
                    and body["error"].get("code") == "OVER_RATE_LIMIT"
                )

            except (ValueError, OSError):
                limited = False

            status = error.code
            headers = error.headers
            error.close()

            if status == 429 or limited:
                if rate_waits >= args.max_rate_waits:
                    raise ExtractionError(
                        "Rate-limit wait budget exhausted."
                    ) from None

                rate_waits += 1
                delay = retry_delay(headers, args.rate_wait)

                record(
                    "rate_limit",
                    status=status,
                    wait_seconds=delay,
                )

                print(
                    f"Rate limit: waiting {delay:.0f} seconds.",
                    flush=True,
                )

                time.sleep(delay)
                continue

            if status not in TRANSIENT_STATUS:
                raise ExtractionError(
                    f"NASA returned HTTP {status}."
                ) from None

            message = f"Temporary HTTP {status}"
            server_delay = retry_delay(headers, 0)

        except (URLError, TimeoutError, ConnectionError):
            message = "Connection error or timeout"
            server_delay = 0

        except ValueError:
            message = "Invalid JSON or unexpected asteroid response"
            server_delay = 0

        failures += 1

        if failures >= args.max_attempts:
            raise ExtractionError(
                f"{message}; retries exhausted."
            ) from None

        # Gradual waiting plus a small random delay
        delay = max(
            server_delay,
            min(60, 2 ** failures) + random.uniform(0, 1),
        )

        record("retry", error=message, wait_seconds=delay)

        print(
            f"{message}: retrying in {delay:.1f} seconds.",
            flush=True,
        )

        time.sleep(delay)


def parse_args():
    """Read command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
    )

    # Project root for COSMIC-WATCH/src/pipeline/<this file>
    root = Path(__file__).resolve().parents[2]

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "data" / "raw" / "asteroid_details",
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum pending IDs to try this run",
    )

    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--rate-wait", type=float, default=3600)
    parser.add_argument("--max-rate-waits", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1)

    args = parser.parse_args()

    if (
        not args.input_dir.is_dir()
        or (args.limit is not None and args.limit < 1)
        or not all(
            math.isfinite(value)
            for value in (args.timeout, args.rate_wait, args.delay)
        )
        or args.timeout <= 0
        or args.rate_wait <= 0
        or args.delay < 0
        or args.max_attempts < 1
        or args.max_rate_waits < 0
    ):
        parser.error("Check input folder and numeric options.")

    return args


def run(args):
    """Prevent simultaneous writes to the same output folder."""
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    with (output / "details.lock").open("a") as lock:
        try:
            lock_nonblocking(lock)

        except BlockingIOError:
            raise ExtractionError(
                "Another details extraction is using this folder."
            ) from None

        return run_locked(args, output)


def run_locked(args, output):
    """Collect IDs, fetch details, and maintain resume records."""
    ids = collect_ids(args.input_dir)

    # Save the IDs from the selected input folder
    atomic_json(output / "unique_asteroid_ids.json", ids)

    manifest = output / "details_manifest.json"

    if manifest.exists():
        state = json.loads(manifest.read_text(encoding="utf-8"))

        if (
            not isinstance(state, dict)
            or state.get("endpoint") != BASE_URL
            or not isinstance(state.get("asteroids"), dict)
        ):
            raise ExtractionError("Invalid details manifest.")

    else:
        state = {
            "endpoint": BASE_URL,
            "asteroids": {},
        }

    state["latest_input_dir"] = str(args.input_dir.resolve())
    atomic_json(manifest, state)

    def record(asteroid_id, event, **details):
        """Record extraction events without saving the API key."""
        row = {
            "time": utc_now(),
            "asteroid_id": asteroid_id,
            "event": event,
            **details,
        }

        with (output / "details_events.jsonl").open(
            "a",
            encoding="utf-8",
        ) as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()

        entry = state["asteroids"][asteroid_id]
        entry["updated_at"] = row["time"]

        if event == "request":
            entry["requests"] = entry.get("requests", 0) + 1

        atomic_json(manifest, state)

    pending = []
    skipped = 0

    # Reuse successfully saved details, including across later runs
    for asteroid_id in ids:
        target = output / f"asteroid_{asteroid_id}.json"
        old = state["asteroids"].get(asteroid_id, {})

        if (
            old.get("status") == "success"
            and target.is_file()
            and old.get("sha256") == file_digest(target)
        ):
            skipped += 1

        else:
            pending.append(asteroid_id)

    selected = (
        pending[:args.limit]
        if args.limit is not None
        else pending
    )

    print(
        f"Already saved: {skipped}; "
        f"pending: {len(pending)}; this run: {len(selected)}"
    )

    if not selected:
        print("All requested asteroid details are already saved.")
        return 0

    api_key = os.environ.get("NASA_API_KEY", "").strip()

    if not api_key:
        api_key = getpass.getpass(
            "NASA API key (hidden): "
        ).strip()

    if not api_key:
        raise ExtractionError("NASA API key is required.")

    good = 0
    failed = 0

    for index, asteroid_id in enumerate(selected):
        filename = f"asteroid_{asteroid_id}.json"
        old = state["asteroids"].get(asteroid_id, {})

        entry = {
            "status": "running",
            "file": filename,
            "requests": old.get("requests", 0),
        }

        state["asteroids"][asteroid_id] = entry
        atomic_json(manifest, state)

        try:
            payload, remaining, cooldown = fetch_details(
                api_key,
                asteroid_id,
                args,
                lambda event, **kwargs: record(
                    asteroid_id,
                    event,
                    **kwargs,
                ),
            )

            target = output / filename

            # Preserve details, removing navigation links containing secrets
            atomic_json(target, remove_links(payload))

            orbital = payload.get("orbital_data")
            orbital = orbital if isinstance(orbital, dict) else {}

            # Missing fields are recorded rather than invented
            entry.update(
                status="success",
                sha256=file_digest(target),
                has_orbital_data=bool(orbital),
                has_observation_count=(
                    orbital.get("observations_used") is not None
                ),
                completed_at=utc_now(),
            )

            record(asteroid_id, "success", file=filename)
            good += 1

            print(
                f"Saved asteroid {asteroid_id} "
                f"({index + 1}/{len(selected)})",
                flush=True,
            )

            if not orbital:
                print(
                    "  Orbital data missing; original response preserved.",
                    flush=True,
                )

        except KeyboardInterrupt:
            entry["status"] = "interrupted"
            record(asteroid_id, "interrupted")

            print("Stopped safely. Run the same command to resume.")
            return 130

        except (ExtractionError, OSError, ValueError) as error:
            entry.update(status="failed", error=str(error))
            record(asteroid_id, "failed", error=str(error))
            failed += 1

            print(
                f"Failed {asteroid_id}: {error}. "
                "Run again to resume."
            )

            break

        if index + 1 < len(selected):
            delay = cooldown if remaining == "0" else args.delay

            if remaining == "0":
                record(
                    asteroid_id,
                    "quota_wait",
                    wait_seconds=delay,
                )

                print(
                    f"Quota exhausted: waiting {delay:.0f} seconds.",
                    flush=True,
                )

            time.sleep(delay)

    print(
        f"New successes: {good}; "
        f"previously saved: {skipped}; failed: {failed}"
    )
    print(f"Remaining from this input: {len(pending) - good}")
    print(f"Output folder: {output}")

    return 1 if failed else 0


def main():
    """Start extraction and handle top-level errors."""
    try:
        return run(parse_args())

    except KeyboardInterrupt:
        print(
            "Stopped. Saved files remain available; "
            "run again to resume."
        )
        return 130

    except (ExtractionError, OSError, ValueError) as error:
        print(f"Extraction stopped: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
