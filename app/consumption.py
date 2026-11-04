"""Turn cumulative register readings into consumption. Pure functions, no I/O.

The portal's `kwh`/`kvah` values are running totals (like an odometer). Usage in an interval is the later reading
minus the earlier one, so n readings give n-1 intervals.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta

from app.models import ConsumptionInterval, Reading
from app.portal.parsers import IST

EXPECTED_STEP = timedelta(minutes=30)
INTERVALS_PER_DAY = 48


def half_hourly(readings: list[Reading]) -> list[ConsumptionInterval]:
    intervals = []
    for previous, current in zip(readings, readings[1:]):
        flags: set[str] = set()
        if current.timestamp - previous.timestamp != EXPECTED_STEP:
            flags.add("gap")
        kwh = _delta(previous.energy_kwh, current.energy_kwh, flags)
        kvah = _delta(previous.apparent_energy_kvah, current.apparent_energy_kvah, flags)
        intervals.append(
            ConsumptionInterval(
                start=previous.timestamp,
                end=current.timestamp,
                energy_kwh=kwh,
                apparent_energy_kvah=kvah,
                power_factor=_power_factor(kwh, kvah, flags),
                interval_count=1,
                flags=sorted(flags),
            )
        )
    return intervals


def daily(intervals: list[ConsumptionInterval]) -> list[ConsumptionInterval]:
    """Sum half-hourly intervals per IST calendar day (by interval start). Days with < 48 intervals are `partial_day`."""
    by_day: dict = defaultdict(list)
    for interval in intervals:
        by_day[interval.start.astimezone(IST).date()].append(interval)

    rows = []
    for day in sorted(by_day):
        items = by_day[day]
        flags = {flag for item in items for flag in item.flags}
        if len(items) < INTERVALS_PER_DAY:
            flags.add("partial_day")
        kwh = _sum(item.energy_kwh for item in items)
        kvah = _sum(item.apparent_energy_kvah for item in items)
        start = datetime.combine(day, time(0), tzinfo=IST)
        rows.append(
            ConsumptionInterval(
                start=start,
                end=start + timedelta(days=1),
                energy_kwh=kwh,
                apparent_energy_kvah=kvah,
                power_factor=_power_factor(kwh, kvah, flags),
                interval_count=len(items),
                flags=sorted(flags),
            )
        )
    return rows


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
