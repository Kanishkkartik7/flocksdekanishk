from datetime import datetime, timedelta

from app.portal.parsers import (
    IST,
    normalize_meter_id,
    parse_export_meter,
    parse_portal_timestamp,
    parse_readings,
    parse_transformer,
    slug,
    to_float,
)
from tests.conftest import load_fixture


def export_record(meter_id: str) -> dict:
    return next(r for r in load_fixture("export_sample.json") if r["meterId"] == meter_id)


def test_timestamps_are_day_first_and_ist():
    parsed = parse_portal_timestamp("01/02/2026 23:30")
    assert parsed == datetime(2026, 2, 1, 23, 30, tzinfo=IST)  # 1 February, not 2 January
    assert parsed.utcoffset() == timedelta(hours=5, minutes=30)


def test_numbers_sent_as_strings_become_floats():
    assert to_float("48440") == 48440.0
    assert to_float("52315.2") == 52315.2
    assert to_float(250) == 250.0
    assert to_float("") is None
    assert to_float("n/a") is None
    assert to_float(None) is None
    assert to_float("nan") is None


def test_enum_values_become_snake_case():
    assert slug("Whole Current") == "whole_current"
    assert slug("CT Operated") == "ct_operated"
    assert slug("Decommissioned") == "decommissioned"
    assert slug("  ") is None


def test_meter_ids_are_case_insensitive():
    assert normalize_meter_id(" j100000 ") == "J100000"


def test_legacy_export_record():
    meter = parse_export_meter(export_record("J100000"))
    assert meter.meter_id == "J100000"
    assert meter.serial_number == "SE33962"
    assert meter.status == "decommissioned"
    assert meter.installation_type == "whole_current"
    assert meter.build == "legacy"
    assert meter.network.dt.code == "DT-001"
    assert meter.network.dt.name == "Malviya Nagar DT 1"
    assert meter.location.latitude == 26.938961002479868


def test_v2_export_record_has_the_same_clean_shape():
    meter = parse_export_meter(export_record("J100004"))
    assert meter.build == "v2"
    assert meter.status == "faulty"
    assert meter.installation_type == "ct_operated"


def test_blank_hierarchy_fields_become_null():
    meter = parse_export_meter(export_record("J100011"))  # blank circle code in the portal data
    assert meter.network.circle.code is None


def test_missing_dt_in_hierarchy_falls_back_to_dt_code():
    raw = export_record("J100000") | {"hierarchy": {}}
    assert parse_export_meter(raw).network.dt.code == "DT-001"


def test_missing_geo_gives_no_location():
    raw = export_record("J100000") | {"geo": {"lat": "", "lng": None}}
    assert parse_export_meter(raw).location is None


def test_readings_are_parsed_sorted_and_deduplicated():
    rows = [
        {"timestamp": "24/06/2026 00:00", "kwh": "10.5", "kvah": "11", "voltR": "230"},
        {"timestamp": "23/06/2026 23:30", "kwh": "10", "kvah": "10.5", "voltR": "229"},
        {"timestamp": "24/06/2026 00:00", "kwh": "10.6", "kvah": "11.1", "voltR": "231"},  # duplicate time
        {"timestamp": "not a date", "kwh": "1"},  # unusable: skipped
    ]
    readings = parse_readings(rows)
    assert [r.timestamp.hour for r in readings] == [23, 0]
    assert readings[1].energy_kwh == 10.6
    assert readings[0].voltage_r_v == 229.0


def test_duplicate_timestamp_keeps_the_row_with_values_not_the_blank_one():
    # J100089 (real portal data): the last row appears twice, the second time with blank kwh and voltR.
    rows = load_fixture("energy_J100089.json")["data"]
    assert len(rows) == 9
    readings = parse_readings(rows)
    assert len(readings) == 8
    assert readings[-1].energy_kwh == 12732.34
    assert readings[-1].voltage_r_v == 229.0


def _row(kwh, kvah, volt):
    return {"timestamp": "30/06/2026 00:00", "kwh": kwh, "kvah": kvah, "voltR": volt}


def test_blank_row_loses_whichever_order_the_portal_sends_it_in():
    full, blank = _row("12732.34", "13750.93", "229"), _row("", "13750.93", "")  # J100089's two rows
    for rows in ([full, blank], [blank, full]):
        (reading,) = parse_readings(rows)
        assert (reading.energy_kwh, reading.voltage_r_v) == (12732.34, 229.0)


def test_equal_quality_duplicates_keep_the_later_row():
    # Synthetic rows: only the tie-break is under test.
    first, second = _row("10", "11", "230"), _row("10.5", "11.5", "231")
    assert parse_readings([first, second])[0].energy_kwh == 10.5
    assert parse_readings([second, first])[0].energy_kwh == 10.0
    one_value_each = parse_readings([_row("10", "", ""), _row("", "", "231")])[0]
    assert (one_value_each.energy_kwh, one_value_each.voltage_r_v) == (None, 231.0)  # later row wins


def test_duplicates_are_never_merged_into_a_reading_the_portal_did_not_send():
    only_kwh, only_volt = _row("10", "", ""), _row("", "", "231")
    (reading,) = parse_readings([only_kwh, only_volt])
    assert not (reading.energy_kwh is not None and reading.voltage_r_v is not None)


def test_transformer_capacity_is_a_number_and_codeless_rows_are_dropped():
    transformer = parse_transformer({"code": "DT-001", "name": "Malviya Nagar DT 1", "feederCode": "F-001", "capacityKva": 100})
    assert transformer.capacity_kva == 100.0
    assert transformer.feeder_code == "F-001"
    assert parse_transformer({"code": "", "name": "x"}) is None
