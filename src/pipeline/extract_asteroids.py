"""Extract NASA NeoWs Feed data using date windows.

Run the same command again to resume an interrupted extraction.
Saved responses omit navigation links that may contain API keys.
"""

import argparse
import fcntl
import getpass
import hashlib
import json
import math
import os
import random
import socket
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


BASE_URL = "https://api.nasa.gov/neo/rest/v1/feed"

# HTTP errors that may succeed when retried
TRANSIENT_STATUS = {408, 425, 500, 502, 503, 504}


class ExtractionError(Exception):
    """An error message that does not expose the API key or request URL."""


def utc_now():
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def split_date_range(start, end, window_days=7):
    """Split an inclusive date range without gaps or overlaps."""
    if start > end or not 1 <= window_days <= 7:
        raise ValueError("Invalid date range or window size.")

    current = start

    while current <= end:
        last = min(current + timedelta(days=window_days - 1), end)
        yield current, last

        if last == end:
            break

        # Start the next window immediately after the previous one
        current = last + timedelta(days=1)


def remove_links(value):
    """Remove navigation links while preserving scientific data."""
    if isinstance(value, dict):
        return {
            key: remove_links(item)
            for key, item in value.items()
            if key != "links"
        }

    if isinstance(value, list):
        return [remove_links(item) for item in value]

    return value


def atomic_json(path, value):
    """Save complete JSON before replacing the destination file."""
    temporary = path.with_name(path.name + ".tmp")

    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(
            value,
            stream,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    # An interrupted write cannot leave a partial destination JSON file
    os.replace(temporary, path)


def file_digest(path):
    """Calculate a file hash to detect changes or corruption."""
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)

    return digest.hexdigest()


def retry_delay(headers, fallback):
    """Read Retry-After as seconds or an HTTP date."""
    value = headers.get("Retry-After") if headers else None

    if value:
        try:
            seconds = float(value)

            if math.isfinite(seconds) and seconds >= 0:
                return max(1, seconds)

        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)

                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)

                return max(
                    1,
                    (parsed - datetime.now(timezone.utc)).total_seconds(),
                )

            except (TypeError, ValueError, OverflowError):
                pass

    return fallback


def validate_payload(payload, start, end):
    """Check the response structure and dates before saving."""
    if not isinstance(payload, dict):
        raise ExtractionError("Response is not a JSON object.")

    objects = payload.get("near_earth_objects")

    if not isinstance(objects, dict):
        raise ExtractionError("Response is missing near_earth_objects.")

    for day, records in objects.items():
        try:
            parsed = date.fromisoformat(day)

        except (TypeError, ValueError):
            raise ExtractionError(
                "Response contains an invalid date."
            ) from None

        if not start <= parsed <= end or not isinstance(records, list):
            raise ExtractionError("Response dates or records are invalid.")

    # This counts dated records, not distinct asteroids across all windows
    return sum(len(records) for records in objects.values())


def fetch_date_range(api_key, start, end, args, record):
    """Fetch one date window with retries and rate-limit handling."""
    query = urlencode({
        "api_key": api_key,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "detailed": "true",
    })

    failures = 0
    rate_waits = 0

    while True:
        record("request")

        try:
            with urlopen(
                f"{BASE_URL}?{query}",
                timeout=args.timeout,
            ) as response:
                payload = json.load(response)
                headers = dict(response.headers.items())
                remaining = response.headers.get("X-RateLimit-Remaining")
                cooldown = retry_delay(response.headers, args.rate_wait)

            validate_payload(payload, start, end)

            return payload, {
                "remaining": remaining,
                "cooldown": cooldown,
                "limit": next(
                    (
                        value
                        for key, value in headers.items()
                        if key.lower() == "x-ratelimit-limit"
                    ),
                    None,
                ),
            }

        except HTTPError as error:
            # Some NASA gateways report rate exhaustion as HTTP 403
            try:
                body = json.loads(error.read(65536))
                rate_limited = (
                    isinstance(body, dict)
                    and isinstance(body.get("error"), dict)
                    and body["error"].get("code") == "OVER_RATE_LIMIT"
                )

            except (ValueError, OSError):
                rate_limited = False

            headers = error.headers
            status = error.code
            error.close()

            if status == 429 or rate_limited:
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

            # Stop immediately for errors such as an invalid API key
            if status not in TRANSIENT_STATUS:
                raise ExtractionError(
                    f"NASA returned HTTP {status}."
                ) from None

            message = f"Temporary HTTP {status}"
            server_delay = retry_delay(headers, 0)

        except (URLError, TimeoutError, socket.timeout, ConnectionError):
            message = "Connection error or timeout"
            server_delay = 0

        except (ValueError, ExtractionError):
            message = "Invalid JSON or unexpected response"
            server_delay = 0

        failures += 1

        if failures >= args.max_attempts:
            raise ExtractionError(
                f"{message}; {failures} failed attempts."
            ) from None

        # Gradually increase the wait and add a small random offset
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


def parse_args(argv=None):
    """Read and validate command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--start-date",
        required=True,
        type=date.fromisoformat,
    )
    parser.add_argument(
        "--end-date",
        required=True,
        type=date.fromisoformat,
    )
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-attempts", type=int, default=5)

    # Default wait when rate-limited and Retry-After is unavailable
    parser.add_argument("--rate-wait", type=float, default=3600)
    parser.add_argument("--max-rate-waits", type=int, default=3)

    # Pause between successful requests
    parser.add_argument("--delay", type=float, default=1)

    # Locate the project root when this file is inside src/
    root = Path(__file__).resolve().parent

    if root.name == "src":
        root = root.parent

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "data" / "raw" / "feed",
    )

    args = parser.parse_args(argv)

    if args.start_date > args.end_date or not 1 <= args.window_days <= 7:
        parser.error(
            "Dates must be ordered and --window-days must be 1 to 7."
        )

    if (
        not all(
            math.isfinite(value)
            for value in (args.timeout, args.rate_wait, args.delay)
        )
        or args.timeout <= 0
        or args.rate_wait <= 0
        or args.delay < 0
        or args.max_attempts < 1
        or args.max_rate_waits < 0
    ):
        parser.error(
            "Invalid timeout, delay, attempts, or rate-wait settings."
        )

    return args


def run(args):
    """Create the output folder and prevent simultaneous writes."""
    folder = args.output_dir / (
        f"{args.start_date}_{args.end_date}_w{args.window_days}"
    )

    folder.mkdir(parents=True, exist_ok=True)

    # This lock works on Ubuntu/Linux
    with (folder / "extract.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        except BlockingIOError:
            raise ExtractionError(
                "Another extraction is using this output folder."
            ) from None

        return run_locked(args, folder)


def run_locked(args, folder):
    """Extract windows and maintain the resume manifest."""
    state_path = folder / "manifest.json"

    config = {
        "endpoint": BASE_URL,
        "start_date": str(args.start_date),
        "end_date": str(args.end_date),
        "window_days": args.window_days,
    }

    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

        if (
            state.get("config") != config
            or not isinstance(state.get("windows"), dict)
        ):
            raise ExtractionError(
                "Manifest configuration is invalid or does not match."
            )

    else:
        state = {"config": config, "windows": {}}
        atomic_json(state_path, state)

    api_key = None
    good = 0
    failed = 0
    skipped = 0

    print(f"Output folder: {folder}", flush=True)

    def record(key, event, **details):
        """Append an event and update the manifest."""
        row = {
            "time": utc_now(),
            "window": key,
            "event": event,
            **details,
        }

        with (folder / "events.jsonl").open(
            "a",
            encoding="utf-8",
        ) as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()

        entry = state["windows"][key]

        if event == "request":
            entry["requests"] = entry.get("requests", 0) + 1

        entry["updated_at"] = row["time"]
        atomic_json(state_path, state)

    for start, end in split_date_range(
        args.start_date,
        args.end_date,
        args.window_days,
    ):
        key = f"{start}_{end}"
        filename = f"feed_{key}.json"
        target = folder / filename
        old = state["windows"].get(key, {})

        # Skip a successful window only if its file still matches the hash
        if old.get("status") == "success" and target.is_file():
            if old.get("sha256") == file_digest(target):
                skipped += 1
                print(f"Already saved: {key}", flush=True)
                continue

        state["windows"][key] = {
            "status": "running",
            "file": filename,
            "requests": old.get("requests", 0),
        }

        atomic_json(state_path, state)
        entry = state["windows"][key]

        try:
            # Ask for the key only when a request is actually needed
            if api_key is None:
                api_key = os.environ.get("NASA_API_KEY", "").strip()

                if not api_key:
                    api_key = getpass.getpass(
                        "NASA API key (hidden): "
                    ).strip()

                if not api_key:
                    raise ExtractionError("NASA API key is required.")

            payload, limits = fetch_date_range(
                api_key,
                start,
                end,
                args,
                lambda event, **kwargs: record(key, event, **kwargs),
            )

            count = validate_payload(payload, start, end)

            # Save the response without navigation links containing secrets
            atomic_json(target, remove_links(payload))

            entry.update(
                status="success",
                sha256=file_digest(target),
                records=count,
                completed_at=utc_now(),
                rate_remaining=limits["remaining"],
            )

            record(
                key,
                "success",
                records=count,
                file=filename,
            )

            good += 1

            print(
                f"Saved {key}: {count} dated asteroid records",
                flush=True,
            )

        except KeyboardInterrupt:
            entry.update(status="interrupted")
            record(key, "interrupted")

            print(
                "Stopped safely. Run the same command to resume.",
                flush=True,
            )

            return 130

        except ExtractionError as error:
            entry.update(status="failed", error=str(error))
            record(key, "failed", error=str(error))
            failed += 1

            print(
                f"Failed {key}: {error}. Run again to resume.",
                flush=True,
            )

            # Stop after exhausted retries; resume with the same command
            break

        try:
            if end < args.end_date:
                delay = (
                    limits["cooldown"]
                    if limits["remaining"] == "0"
                    else args.delay
                )

                if delay > args.delay:
                    record(key, "quota_wait", wait_seconds=delay)

                    print(
                        f"Quota exhausted: waiting {delay:.0f} seconds.",
                        flush=True,
                    )

                time.sleep(delay)

        except KeyboardInterrupt:
            print(
                "Stopped between windows. Run the same command to resume.",
                flush=True,
            )

            return 130

    print(
        f"New successes: {good}; skipped: {skipped}; failed: {failed}",
        flush=True,
    )

    return 1 if failed else 0


def main():
    """Start extraction and handle top-level errors."""
    try:
        return run(parse_args())

    except KeyboardInterrupt:
        print("Stopped. Run the same command to resume.")
        return 130

    except (ExtractionError, OSError, ValueError) as error:
        print(f"Extraction stopped: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
