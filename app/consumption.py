"""Turn cumulative register readings into consumption. Pure functions, no I/O.

The portal's `kwh`/`kvah` values are running totals (like an odometer). Usage in an interval is the later reading
minus the earlier one, so n readings give n-1 intervals.

Nothing is invented: a missing reading is never filled in. A pair of readings that are further apart than usual stays
one (long) interval, flagged `gap`, and `spans_multiple_days` when it covers more than 24 hours.
"""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from app.models import ConsumptionInterval, Reading
from app.portal.parsers import IST

ONE_DAY = timedelta(days=1)


def reading_step(readings: list[Reading]) -> timedelta | None:
    """How far apart a meter's readings normally are: the most common gap between consecutive readings.

    The portal has two schedules (every 30 minutes, or once a day), so the step is read from the data instead of
    assumed. On a tie the shorter gap wins. `None` when there are fewer than two readings. With exactly two readings
    their one gap is the step, so it can never be flagged `gap`; there is nothing to compare it with.
    """
    gaps = Counter(b.timestamp - a.timestamp for a, b in zip(readings, readings[1:]))
    if not gaps:
        return None
    return min(gaps, key=lambda gap: (-gaps[gap], gap))


def between_readings(readings: list[Reading], step: timedelta | None = None) -> list[ConsumptionInterval]:
    """One interval per pair of consecutive readings, in the meter's own reading step.

    A pair that isn't `step` apart is flagged `gap`, and `spans_multiple_days` if it is more than 24 hours long.
    `duration_minutes` is the real time between the two readings. `step` defaults to `reading_step(readings)`.
    """
    step = step or reading_step(readings)
    intervals = []
    for previous, current in zip(readings, readings[1:]):
        flags: set[str] = set()
        elapsed = current.timestamp - previous.timestamp
        if elapsed != step:
            flags.add("gap")
        if elapsed > ONE_DAY:
            flags.add("spans_multiple_days")
        kwh = _delta(previous.energy_kwh, current.energy_kwh, flags)
        kvah = _delta(previous.apparent_energy_kvah, current.apparent_energy_kvah, flags)
        intervals.append(
            ConsumptionInterval(
                start=previous.timestamp,
                end=current.timestamp,
                duration_minutes=int(elapsed.total_seconds() // 60),
                energy_kwh=kwh,
                apparent_energy_kvah=kvah,
                power_factor=_power_factor(kwh, kvah, flags),
                interval_count=1,
                flags=sorted(flags),
            )
        )
    return intervals


def daily(intervals: list[ConsumptionInterval], step: timedelta | None) -> list[ConsumptionInterval]:
    """Sum intervals per IST calendar day (by interval start).

    A day is complete when it holds one day's worth of `step`s (48 for 30-minute meters, 1 for once-a-day meters);
    fewer than that gives `partial_day`. The step must not be longer than a day (the caller checks this).

    An interval longer than 24 hours (readings were missing) can't honestly be assigned to one calendar day, and
    splitting it would invent a daily pattern. It is returned as its own row, with its real start and end, instead of
    being added to the day it started on.
    """
    per_day = max(1, round(ONE_DAY / step)) if step else 1
    by_day: dict = defaultdict(list)
    rows = []
    for interval in intervals:
        if interval.duration_minutes > ONE_DAY.total_seconds() // 60:
            rows.append(interval.model_copy(update={"interval_count": 1}))
            continue
        by_day[interval.start.astimezone(IST).date()].append(interval)

    for day in sorted(by_day):
        items = by_day[day]
        flags = {flag for item in items for flag in item.flags}
        if len(items) < per_day:
            flags.add("partial_day")
        kwh = _sum(item.energy_kwh for item in items)
        kvah = _sum(item.apparent_energy_kvah for item in items)
        start = datetime.combine(day, time(0), tzinfo=IST)
        rows.append(
            ConsumptionInterval(
                start=start,
                end=start + timedelta(days=1),
                duration_minutes=24 * 60,
                energy_kwh=kwh,
                apparent_energy_kvah=kvah,
                power_factor=_power_factor(kwh, kvah, flags),
                interval_count=len(items),
                flags=sorted(flags),
            )
        )
    return sorted(rows, key=lambda row: row.start)


def total_kwh(intervals: list[ConsumptionInterval]) -> float:
    return _sum(i.energy_kwh for i in intervals) or 0.0


def _delta(earlier: float | None, later: float | None, flags: set[str]) -> float | None:
    if earlier is None or later is None:
        flags.add("missing_value")
        return None
    difference = round(later - earlier, 3)
    if difference < 0:
        flags.add("negative_delta")  # a register going down is a reset, meter swap or bad data; don't report it as usage
        return None
    return difference


def _power_factor(kwh: float | None, kvah: float | None, flags: set[str]) -> float | None:
    if kwh is None or not kvah:
        return None
    factor = round(kwh / kvah, 3)
    if factor > 1:
        flags.add("power_factor_above_1")  # physically impossible; usually rounding on tiny intervals
    return factor


def _sum(values) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present), 3) if present else None
