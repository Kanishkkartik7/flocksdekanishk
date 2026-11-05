"""Survey every meter's readings on the live portal, gently, and summarise their reading schedules.

Why: the docs said "some meters report every 30 minutes, some once a day" after looking at eight meters. This checks all
of them, so those claims rest on counts instead of a sample.

Usage:  python scripts/survey_readings.py [--delay 1.0] [--out docs/reading-survey.csv]

It is read-only and slow on purpose. It logs in the way the browser does, downloads the meter list once (the signed
export), then sends one GET /portal/meters/{id}/energy per meter, one at a time, waiting `--delay` seconds between them
(403 meters take about 7 minutes). If the portal answers 429 (too many requests), it waits a minute; if that keeps
happening, it stops instead of pushing on. Needs PORTAL_PASSWORD in .env. Writes one CSV row per meter and prints a summary.
"""

import argparse
import asyncio
import csv
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import consumption  # noqa: E402
from app.config import Settings  # noqa: E402
from app.errors import PortalError, PortalNotFound, PortalRateLimitedError  # noqa: E402
from app.portal.client import PortalClient  # noqa: E402
from app.portal.parsers import parse_export_meter, parse_readings, to_float  # noqa: E402

METER_FIELDS = ["meter_id", "build", "status", "phase", "installation_type", "make"]
READING_FIELDS = [
    "raw_rows",
    "readings",
    "duplicate_timestamps",
    "rows_with_missing_values",
    "step_minutes",
    "first",
    "last",
    "gaps",
    "negative_deltas",
]
FIELDS = METER_FIELDS + READING_FIELDS + ["error"]
RATE_LIMIT_PAUSE_SECONDS = 60
MAX_RATE_LIMITED_IN_A_ROW = 3


def summarise_meter(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What one meter's raw /energy rows look like. Pure: no I/O.

    Uses the API's own parser and step detection, so the survey measures exactly what the API would serve.
    """
    readings = parse_readings(rows)
    step = consumption.reading_step(readings)
    intervals = consumption.between_readings(readings, step)
    raw_timestamps = [row.get("timestamp") for row in rows]
    return {
        "raw_rows": len(rows),
        "readings": len(readings),
        "duplicate_timestamps": len(raw_timestamps) - len(set(raw_timestamps)),
        "rows_with_missing_values": sum(
            1 for row in rows if any(to_float(row.get(key)) is None for key in ("kwh", "kvah", "voltR"))
        ),
        "step_minutes": "" if step is None else int(step.total_seconds() // 60),
        "first": readings[0].timestamp.isoformat() if readings else "",
        "last": readings[-1].timestamp.isoformat() if readings else "",
        "gaps": sum("gap" in interval.flags for interval in intervals),
        "negative_deltas": sum("negative_delta" in interval.flags for interval in intervals),
    }


class _RateLimitCounter(logging.Handler):
    """Counts the client's warnings about HTTP 429, so the summary can say whether the portal ever rate-limited us."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "429" in record.getMessage():
            self.count += 1


async def survey(delay: float) -> tuple[list[dict[str, Any]], int]:
    # Patient retry settings: back off for seconds, not half-seconds, if the portal pushes back.
    # The credentials come from .env; `_env_file` is pydantic-settings' runtime option, which type checkers don't know.
    settings = Settings(  # pyright: ignore[reportCallIssue]
        _env_file=ROOT / ".env",  # pyright: ignore[reportCallIssue]
        portal_max_attempts=4,
        portal_retry_backoff_seconds=5.0,
        portal_max_concurrency=1,
    )
    counter = _RateLimitCounter()
    logging.getLogger("app.portal.client").addHandler(counter)
    client = PortalClient(settings)
    results: list[dict[str, Any]] = []
    try:
        meters = sorted((parse_export_meter(raw) for raw in await client.fetch_export()), key=lambda m: m.meter_id)
        print(f"{len(meters)} meters in the export; fetching readings one at a time, {delay:g} s apart")
        limited_in_a_row = 0
        for number, meter in enumerate(meters, 1):
            row: dict[str, Any] = {field: getattr(meter, field) for field in METER_FIELDS}
            try:
                row.update(summarise_meter(await client.fetch_energy(meter.meter_id)))
                limited_in_a_row = 0
            except PortalRateLimitedError:
                row["error"] = "rate_limited"
                limited_in_a_row += 1
                if limited_in_a_row >= MAX_RATE_LIMITED_IN_A_ROW:
                    results.append(row)
                    print(f"Stopping: the portal rate-limited {limited_in_a_row} meters in a row.")
                    break
                print(f"Rate-limited on {meter.meter_id}; pausing {RATE_LIMIT_PAUSE_SECONDS} s")
                await asyncio.sleep(RATE_LIMIT_PAUSE_SECONDS)
            except PortalNotFound:
                row["error"] = "not_found"
            except PortalError as exc:
                row["error"] = exc.code
            results.append(row)
            if number % 50 == 0:
                print(f"  {number}/{len(meters)} done")
            await asyncio.sleep(delay)
    finally:
        await client.aclose()
    return results, counter.count


def print_summary(rows: list[dict[str, Any]], rate_limit_warnings: int) -> None:
    ok = [row for row in rows if not row.get("error")]
    print(f"\nMeters surveyed: {len(rows)} ({len(ok)} ok)")
    print("Errors:", dict(Counter(row["error"] for row in rows if row.get("error"))) or "none")
    print("HTTP 429 warnings from the client:", rate_limit_warnings)
    print("Reading step (minutes) -> meters:", dict(sorted(Counter(row["step_minutes"] for row in ok).items())))
    print("Readings per meter -> meters:", dict(sorted(Counter(row["readings"] for row in ok).items())))
    print("Raw rows per meter -> meters:", dict(sorted(Counter(row["raw_rows"] for row in ok).items())))
    windows = Counter((row["first"][:16], row["last"][:16]) for row in ok)
    print("Time windows (first, last) -> meters:", dict(windows))
    for label, key in (
        ("duplicate timestamps", "duplicate_timestamps"),
        ("rows with missing values", "rows_with_missing_values"),
        ("gaps against their own step", "gaps"),
        ("negative deltas", "negative_deltas"),
    ):
        hits = [row["meter_id"] for row in ok if row[key]]
        print(f"Meters with {label}: {len(hits)}", hits[:25])
    for field in ("build", "installation_type", "phase", "status", "make"):
        table = Counter((row[field], row["step_minutes"]) for row in ok)
        print(f"Step by {field}:", dict(sorted(table.items(), key=str)))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--delay", type=float, default=1.0, help="seconds to wait between meters (default 1.0)")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "reading-survey.csv", help="CSV file to write")
    args = parser.parse_args()
    if args.delay < 0.5:
        parser.error("--delay below 0.5 s is not allowed; the portal is someone else's production system")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    rows, rate_limit_warnings = asyncio.run(survey(args.delay))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {args.out} ({len(rows)} rows)")
    print_summary(rows, rate_limit_warnings)


if __name__ == "__main__":
    main()
