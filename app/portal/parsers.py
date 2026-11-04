"""Turn raw portal JSON into the clean API models. Pure functions, no I/O.

The portal's quirks handled here (see PROTOCOL.md): numbers sent as strings, `DD/MM/YYYY HH:MM` timestamps with
no timezone, mixed naming and casing, blank hierarchy fields, and case-sensitive meter IDs.
"""

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models import NETWORK_LEVELS, Location, Meter, NetworkNode, NetworkPath, Reading, Transformer

log = logging.getLogger(__name__)

# The portal sends local times with no offset. The utility is in Jaipur, so we read them as IST. India has had no
# daylight saving time since 1945, so a fixed offset is exact (and avoids needing tz data on Windows).
IST = timezone(timedelta(hours=5, minutes=30), "IST")
PORTAL_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M"


def normalize_meter_id(raw: str) -> str:
    """The portal's IDs are uppercase and case-sensitive; we accept any case."""
    return raw.strip().upper()


def slug(value: Any) -> str | None:
    """'Whole Current' -> 'whole_current', 'CT Operated' -> 'ct_operated'. Blank -> None."""
    text = clean_text(value)
    if text is None:
        return None
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_float(value: Any) -> float | None:
    """The portal sends most numbers as strings ("48440.42"). Unparseable or non-finite values become None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_portal_timestamp(value: str) -> datetime:
    """'23/06/2026 23:30' -> 2026-06-23T23:30:00+05:30. Day comes first."""
    return datetime.strptime(value.strip(), PORTAL_TIMESTAMP_FORMAT).replace(tzinfo=IST)


def parse_network_path(raw: dict[str, Any]) -> NetworkPath:
    nodes = {}
    for level in NETWORK_LEVELS:
        item = raw.get(level) or {}
        nodes[level] = NetworkNode(code=clean_text(item.get("code")), name=clean_text(item.get("name")))
    return NetworkPath(**nodes)


def parse_export_meter(raw: dict[str, Any]) -> Meter:
    """One record from GET /portal/export."""
    network = parse_network_path(raw.get("hierarchy") or {})
    dt_code = clean_text(raw.get("dtCode"))
    if network.dt.code is None and dt_code:
        network.dt.code = dt_code  # the flat field and the hierarchy agree in all data seen; use it as a backup

    geo = raw.get("geo") or {}
    lat, lng = to_float(geo.get("lat")), to_float(geo.get("lng"))
    location = Location(latitude=lat, longitude=lng) if lat is not None and lng is not None else None

    return Meter(
        meter_id=normalize_meter_id(raw["meterId"]),
        serial_number=clean_text(raw.get("serialNo")),
        make=clean_text(raw.get("make")),
        phase=slug(raw.get("phaseType")),
        status=slug(raw.get("installStatus")),
        installation_type=slug(raw.get("installType")),
        build=slug(raw.get("build")),
        location=location,
        network=network,
    )


def parse_transformer(raw: dict[str, Any]) -> Transformer | None:
    """One record from GET /portal/dts. Records without a code can't be referenced, so they're dropped."""
    code = clean_text(raw.get("code"))
    if code is None:
        return None
    return Transformer(
        code=code,
        name=clean_text(raw.get("name")),
        feeder_code=clean_text(raw.get("feederCode")),
        capacity_kva=to_float(raw.get("capacityKva")),
    )


def parse_readings(rows: list[dict[str, Any]]) -> list[Reading]:
    """Rows from GET /portal/meters/{id}/energy, sorted by time. A row without a usable timestamp is skipped,
    because it can't be placed in time; duplicate timestamps keep the last row."""
    by_time: dict[datetime, Reading] = {}
    for row in rows:
        try:
            timestamp = parse_portal_timestamp(row["timestamp"])
        except (KeyError, TypeError, ValueError, AttributeError):
            log.warning("Skipping reading with unusable timestamp: %r", row)
            continue
        by_time[timestamp] = Reading(
            timestamp=timestamp,
            energy_kwh=to_float(row.get("kwh")),
            apparent_energy_kvah=to_float(row.get("kvah")),
            voltage_r_v=to_float(row.get("voltR")),
        )
    return [by_time[t] for t in sorted(by_time)]
