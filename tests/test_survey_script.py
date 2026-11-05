"""The survey script's per-meter summary, checked on real portal responses (no network)."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

_spec = importlib.util.spec_from_file_location("survey_readings", ROOT / "scripts" / "survey_readings.py")
assert _spec is not None and _spec.loader is not None
survey_readings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(survey_readings)


def summarise(name: str) -> dict:
    rows = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["data"]
    return survey_readings.summarise_meter(rows)


def test_half_hourly_meter():
    summary = summarise("energy_J100000.json")  # the first 8 of J100000's 337 rows
    assert summary["step_minutes"] == 30
    assert summary["readings"] == summary["raw_rows"] == 8
    assert summary["duplicate_timestamps"] == summary["gaps"] == summary["negative_deltas"] == 0


def test_daily_meter():
    summary = summarise("energy_J100400.json")
    assert summary["step_minutes"] == 1440
    assert summary["readings"] == 8
    assert summary["first"].startswith("2026-06-23T00:00") and summary["last"].startswith("2026-06-30T00:00")


def test_repeated_last_row_is_counted_and_collapsed():
    summary = summarise("energy_J100089.json")
    assert summary["raw_rows"] == 9 and summary["readings"] == 8
    assert summary["duplicate_timestamps"] == 1
    assert summary["rows_with_missing_values"] == 1
    assert summary["step_minutes"] == 1440 and summary["gaps"] == 0
