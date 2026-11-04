from datetime import datetime, timedelta

from app import consumption
from app.models import Reading
from app.portal.parsers import IST, parse_readings
from tests.conftest import load_fixture

START = datetime(2026, 6, 24, 0, 0, tzinfo=IST)


def reading(minutes: int, kwh: float | None, kvah: float | None = None) -> Reading:
    return Reading(
        timestamp=START + timedelta(minutes=minutes),
        energy_kwh=kwh,
        apparent_energy_kvah=kvah if kvah is not None else (None if kwh is None else kwh * 1.08),
        voltage_r_v=230,
    )


def test_n_readings_give_n_minus_1_intervals_of_differences():
    readings = parse_readings(load_fixture("energy_J100000.json")["data"])  # real portal values
    intervals = consumption.half_hourly(readings)
    assert len(intervals) == len(readings) - 1
    first = intervals[0]
    assert first.energy_kwh == 0.42  # 48439.16 - 48438.74
    assert first.apparent_energy_kvah == 0.45  # 52314.29 - 52313.84
    assert first.power_factor == 0.933
    assert first.flags == []


def test_register_going_down_is_flagged_not_reported_as_usage():
    intervals = consumption.half_hourly([reading(0, 100.0), reading(30, 99.0)])
    assert intervals[0].energy_kwh is None
    assert "negative_delta" in intervals[0].flags


def test_gaps_and_missing_values_are_flagged():
    intervals = consumption.half_hourly([reading(0, 100.0), reading(90, 101.5), reading(120, None)])
    assert intervals[0].flags == ["gap"]
    assert intervals[0].energy_kwh == 1.5
    assert "missing_value" in intervals[1].flags


def test_daily_totals_flag_partial_days():
    readings = [reading(m, 100 + m / 60) for m in range(0, 24 * 60 + 1, 30)]  # one full day: 49 readings
    readings.append(reading(24 * 60 + 30, 100 + (24 * 60 + 30) / 60))  # one interval into the next day
    days = consumption.daily(consumption.half_hourly(readings))
    assert [d.interval_count for d in days] == [48, 1]
    assert days[0].energy_kwh == 24.0
    assert days[0].flags == []
    assert days[1].flags == ["partial_day"]
    assert days[0].start == START and days[0].end == START + timedelta(days=1)


def test_total_skips_missing_values():
    intervals = consumption.half_hourly([reading(0, 1.0), reading(30, 2.0), reading(60, None), reading(90, 4.0)])
    assert consumption.total_kwh(intervals) == 1.0
