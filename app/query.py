"""Filter and search meters locally, which the portal itself can't do (it only offers a single text search).
Pure functions, no I/O."""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.models import Meter
from app.portal.parsers import slug

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Near:
    latitude: float
    longitude: float
    radius_km: float


@dataclass(frozen=True)
class MeterFilter:
    q: str | None = None
    # Each set means "any of these"; an empty set means "don't filter".
    status: frozenset[str] = field(default_factory=frozenset)
    make: frozenset[str] = field(default_factory=frozenset)
    phase: frozenset[str] = field(default_factory=frozenset)
    installation_type: frozenset[str] = field(default_factory=frozenset)
    build: frozenset[str] = field(default_factory=frozenset)
    network: dict[str, frozenset[str]] = field(default_factory=dict)  # level -> codes
    near: Near | None = None


def filter_meters(meters: Iterable[Meter], f: MeterFilter) -> list[tuple[Meter, float | None]]:
    """Matching meters, with distance (km) when `near` is set. Sorted by distance, otherwise by meter ID."""
    enum_filters = {
        "status": {slug(v) for v in f.status},
        "phase": {slug(v) for v in f.phase},
        "installation_type": {slug(v) for v in f.installation_type},
        "build": {slug(v) for v in f.build},
    }
    makes = {v.casefold() for v in f.make}
    network = {level: {c.casefold() for c in codes} for level, codes in f.network.items() if codes}
    needle = f.q.strip().casefold() if f.q and f.q.strip() else None

    results: list[tuple[Meter, float | None]] = []
    for meter in meters:
        if needle and needle not in meter.meter_id.casefold() and needle not in (meter.serial_number or "").casefold():
            continue
        if any(wanted and getattr(meter, name) not in wanted for name, wanted in enum_filters.items()):
            continue
        if makes and (meter.make or "").casefold() not in makes:
            continue
        if any((getattr(meter.network, level).code or "").casefold() not in codes for level, codes in network.items()):
            continue
        distance = None
        if f.near:
            if meter.location is None:
                continue
            distance = haversine_km(f.near.latitude, f.near.longitude, meter.location.latitude, meter.location.longitude)
            if distance > f.near.radius_km:
                continue
        results.append((meter, distance))

    if f.near:
        results.sort(key=lambda pair: pair[1] or 0.0)  # always set when `near` is used
    else:
        results.sort(key=lambda pair: pair[0].meter_id)
    return results


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))
