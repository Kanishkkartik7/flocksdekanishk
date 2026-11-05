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
    intervals = consumption.between_readings(readings)
    assert len(intervals) == len(readings) - 1
    first = intervals[0]
    assert first.energy_kwh == 0.42  # 48439.16 - 48438.74
    assert first.apparent_energy_kvah == 0.45  # 52314.29 - 52313.84
    assert first.power_factor == 0.933
    assert first.flags == []


def test_register_going_down_is_flagged_not_reported_as_usage():
    intervals = consumption.between_readings([reading(0, 100.0), reading(30, 99.0)])
    assert intervals[0].energy_kwh is None
    assert "negative_delta" in intervals[0].flags


def test_gaps_and_missing_values_are_flagged():
    intervals = consumption.between_readings([reading(0, 100.0), reading(90, 101.5), reading(120, None)])
    assert intervals[0].flags == ["gap"]
    assert intervals[0].energy_kwh == 1.5
    assert "missing_value" in intervals[1].flags


def test_daily_totals_flag_partial_days():
    readings = [reading(m, 100 + m / 60) for m in range(0, 24 * 60 + 1, 30)]  # one full day: 49 readings
    readings.append(reading(24 * 60 + 30, 100 + (24 * 60 + 30) / 60))  # one interval into the next day
    days = consumption.daily(consumption.between_readings(readings), consumption.reading_step(readings))
    assert [d.interval_count for d in days] == [48, 1]
    assert days[0].energy_kwh == 24.0
    assert days[0].flags == []
    assert days[1].flags == ["partial_day"]
    assert days[0].start == START and days[0].end == START + timedelta(days=1)


def test_total_skips_missing_values():
    intervals = consumption.between_readings([reading(0, 1.0), reading(30, 2.0), reading(60, None), reading(90, 4.0)])
    assert consumption.total_kwh(intervals) == 1.0


def test_step_is_read_from_the_data_not_assumed():
    assert consumption.reading_step([reading(0, 1.0), reading(30, 2.0), reading(60, 3.0)]) == timedelta(minutes=30)
    assert consumption.reading_step([reading(0, 1.0), reading(1440, 2.0), reading(2880, 3.0)]) == timedelta(days=1)
    assert consumption.reading_step([reading(0, 1.0)]) is None


def test_once_a_day_meter_has_full_day_intervals_and_no_false_flags():
    # J100400 reports once a day (real portal values): 8 readings, 7 intervals of 24 h.
    readings = parse_readings(load_fixture("energy_J100400.json")["data"])
    step = consumption.reading_step(readings)
    assert step == timedelta(days=1)
    intervals = consumption.between_readings(readings, step)
    assert len(intervals) == 7
    assert all(i.flags == [] for i in intervals)  # no `gap`: a day is this meter's normal step
    days = consumption.daily(intervals, step)
    assert [d.interval_count for d in days] == [1] * 7
    assert all(d.flags == [] for d in days)  # no `partial_day` either
    assert consumption.total_kwh(days) == consumption.total_kwh(intervals)


def test_a_real_gap_is_still_flagged_on_a_once_a_day_meter():
    intervals = consumption.between_readings([reading(0, 1.0), reading(1440, 2.0), reading(4320, 5.0)])
    assert [i.flags for i in intervals] == [[], ["gap", "spans_multiple_days"]]  # 2 days between the last two readings
    assert [i.duration_minutes for i in intervals] == [1440, 2880]
    assert intervals[1].energy_kwh == 3.0  # one long interval; nothing is filled in


def test_duration_is_the_real_elapsed_time_for_both_schedules():
    half_hour = consumption.between_readings(parse_readings(load_fixture("energy_J100000.json")["data"]))
    daily_meter = consumption.between_readings(parse_readings(load_fixture("energy_J100400.json")["data"]))
    assert {i.duration_minutes for i in half_hour} == {30}
    assert {i.duration_minutes for i in daily_meter} == {1440}
    assert "spans_multiple_days" not in {f for i in daily_meter for f in i.flags}  # exactly 24 h is the normal step


def test_negative_delta_and_missing_values_are_flagged_on_a_once_a_day_meter_too():
    negative = consumption.between_readings([reading(0, 100.0), reading(1440, 90.0), reading(2880, 95.0)])
    assert negative[0].energy_kwh is None and negative[0].flags == ["negative_delta"]
    assert negative[1].energy_kwh == 5.0 and negative[1].flags == []  # compared with the previous reading, not the max

    missing = consumption.between_readings([reading(0, 100.0), reading(1440, None), reading(2880, 103.0)])
    assert all("missing_value" in i.flags for i in missing) and all("gap" not in i.flags for i in missing)


def test_two_readings_give_one_interval_that_cannot_be_a_gap():
    intervals = consumption.between_readings([reading(0, 1.0), reading(1440, 2.0)])
    assert len(intervals) == 1 and intervals[0].flags == []
    assert consumption.reading_step([reading(0, 1.0)]) is None
    assert consumption.between_readings([reading(0, 1.0)]) == []


def test_missing_readings_on_a_30_minute_meter_stay_one_long_interval_not_extra_usage_in_a_day():
    readings = [reading(m, 100 + m / 60) for m in range(0, 24 * 60 + 1, 30)]  # one full day
    readings.append(reading(24 * 60 + 3 * 24 * 60, 100 + (4 * 24 * 60) / 60))  # next reading is 3 days later
    step = consumption.reading_step(readings)
    assert step == timedelta(minutes=30)
    intervals = consumption.between_readings(readings, step)
    assert intervals[-1].flags == ["gap", "spans_multiple_days"] and intervals[-1].duration_minutes == 3 * 24 * 60

    days = consumption.daily(intervals, step)
    assert len(days) == 2  # the full day, and the long interval on its own: not added to the day it started on
    assert days[0].interval_count == 48 and days[0].flags == [] and days[0].energy_kwh == 24.0
    assert days[1].interval_count == 1 and days[1].duration_minutes == 3 * 24 * 60
    assert days[1].start == START + timedelta(days=1) and days[1].end == START + timedelta(days=4)
    assert days[1].energy_kwh == 72.0
    assert consumption.total_kwh(days) == consumption.total_kwh(intervals)  # same usage, nothing lost or invented


def test_daily_rows_for_a_once_a_day_meter_are_complete_days():
    readings = parse_readings(load_fixture("energy_J100400.json")["data"])
    step = consumption.reading_step(readings)
    days = consumption.daily(consumption.between_readings(readings, step), step)
    assert [d.duration_minutes for d in days] == [1440] * 7
