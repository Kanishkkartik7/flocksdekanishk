from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.errors import InvalidRequestError
from app.models import ConsumptionResponse, Meter, MeterPage, ReadingsResponse
from app.query import MeterFilter, Near
from app.routers.deps import NOT_FOUND, UPSTREAM_ERRORS, ServiceDep

router = APIRouter(prefix="/meters", tags=["meters"])

Codes = Annotated[list[str] | None, Query()]
TIME_HELP = "ISO-8601. Without an offset, the time is taken as IST (+05:30). In a URL write `+` as `%2B`."


@router.get("", response_model=MeterPage, summary="List, filter and search meters", responses=UPSTREAM_ERRORS)
async def list_meters(
    service: ServiceDep,
    q: Annotated[str | None, Query(description="Case-insensitive substring of the meter ID or serial number.")] = None,
    status: Annotated[list[str] | None, Query(description="`installed`, `faulty`, `decommissioned`. Repeat for OR.")] = None,
    make: Annotated[list[str] | None, Query(description="Manufacturer, e.g. `HPL`, `L&T` (URL-encode `&`).")] = None,
    phase: Annotated[list[str] | None, Query(description="`single` or `three`.")] = None,
    installation_type: Annotated[list[str] | None, Query(description="`whole_current` or `ct_operated`.")] = None,
    build: Annotated[list[str] | None, Query(description="`legacy` or `v2`.")] = None,
    zone: Codes = None,
    circle: Codes = None,
    division: Codes = None,
    subdivision: Codes = None,
    substation: Codes = None,
    feeder: Codes = None,
    dt: Annotated[list[str] | None, Query(description="Network codes, e.g. `dt=DT-001`. Same for every level.")] = None,
    near_lat: Annotated[float | None, Query(ge=-90, le=90, description="Find meters near this point.")] = None,
    near_lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=500, description="Search radius for `near_lat`/`near_lng`.")] = 1.0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeterPage:
    """Filters combine with AND; repeating a parameter means OR (`status=faulty&status=decommissioned`).
    With `near_lat`/`near_lng`, results are sorted by distance and include `distance_km`."""
    if (near_lat is None) != (near_lng is None):
        raise InvalidRequestError("near_lat and near_lng must be given together")
    network = {
        level: frozenset(codes or [])
        for level, codes in {
            "zone": zone, "circle": circle, "division": division, "subdivision": subdivision,
            "substation": substation, "feeder": feeder, "dt": dt,
        }.items()
    }  # fmt: skip
    meter_filter = MeterFilter(
        q=q,
        status=frozenset(status or []),
        make=frozenset(make or []),
        phase=frozenset(phase or []),
        installation_type=frozenset(installation_type or []),
        build=frozenset(build or []),
        network=network,
        near=Near(near_lat, near_lng, radius_km) if near_lat is not None and near_lng is not None else None,
    )
    return await service.list_meters(meter_filter, limit=limit, offset=offset)


@router.get("/{meter_id}", response_model=Meter, summary="Get one meter", responses=NOT_FOUND | UPSTREAM_ERRORS)
async def get_meter(meter_id: str, service: ServiceDep) -> Meter:
    """Details, location and network path. The ID is case-insensitive (`j100000` works)."""
    return await service.get_meter(meter_id)


@router.get(
    "/{meter_id}/readings",
    response_model=ReadingsResponse,
    summary="Raw meter readings (cumulative registers)",
    responses=NOT_FOUND | UPSTREAM_ERRORS,
)
async def get_readings(
    meter_id: str,
    service: ServiceDep,
    start: Annotated[datetime | None, Query(description=f"Inclusive. {TIME_HELP}")] = None,
    end: Annotated[datetime | None, Query(description=f"Inclusive. {TIME_HELP}")] = None,
) -> ReadingsResponse:
    """The portal's readings as they are, cleaned up: real numbers and ISO timestamps. Most meters report once a day;
    a few report every 30 minutes. `energy_kwh` and `apparent_energy_kvah` are running totals; use `/consumption`
    for usage per interval."""
    return await service.get_readings(meter_id, start, end)


@router.get(
    "/{meter_id}/consumption",
    response_model=ConsumptionResponse,
    summary="Energy used per interval",
    responses=NOT_FOUND | UPSTREAM_ERRORS,
)
async def get_consumption(
    meter_id: str,
    service: ServiceDep,
    start: Annotated[datetime | None, Query(description=f"Readings from this time (inclusive). {TIME_HELP}")] = None,
    end: Annotated[datetime | None, Query(description=f"Readings up to this time (inclusive). {TIME_HELP}")] = None,
    interval: Annotated[
        Literal["native", "1d"],
        Query(
            description="`native`: one row per pair of readings, in the meter's own step (30 minutes or 1 day, see "
            "`reading_interval_minutes` in the response). `1d`: sum per IST calendar day."
        ),
    ] = "native",
) -> ConsumptionResponse:
    """Differences between consecutive readings (n readings give n-1 intervals), with power factor. Problems are
    flagged rather than hidden: a register that goes down gives `energy_kwh: null` and the `negative_delta` flag, and
    readings that are further apart than usual give one long interval flagged `gap` (and `spans_multiple_days`
    beyond 24 hours). Missing readings are never filled in. `interval=1d` isn't available for a meter whose normal
    step is longer than a day (422)."""
    return await service.get_consumption(meter_id, start, end, interval)
