"""Response schemas. These classes are the API contract; openapi.json is generated from them."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

NETWORK_LEVELS = ("zone", "circle", "division", "subdivision", "substation", "feeder", "dt")


# --- Meters ---------------------------------------------------------------------------------------------------------


class NetworkNode(BaseModel):
    code: str | None = Field(description="Portal code, e.g. `F-001`. `null` when the portal left it blank.")
    name: str | None = Field(description="Human-readable name. `null` when the portal left it blank.")


class NetworkPath(BaseModel):
    """Where a meter sits in the distribution network, from zone (top) to distribution transformer (bottom)."""

    zone: NetworkNode
    circle: NetworkNode
    division: NetworkNode
    subdivision: NetworkNode
    substation: NetworkNode
    feeder: NetworkNode
    dt: NetworkNode = Field(description="Distribution transformer the meter is connected to.")


class Location(BaseModel):
    latitude: float = Field(examples=[26.938961])
    longitude: float = Field(examples=[75.830957])


class Meter(BaseModel):
    meter_id: str = Field(examples=["J100000"])
    serial_number: str | None = Field(examples=["SE33962"])
    make: str | None = Field(description="Manufacturer, as reported by the portal.", examples=["HPL"])
    phase: str | None = Field(description="Known values: `single`, `three`.", examples=["single"])
    status: str | None = Field(
        description="Installation status. Known values: `installed`, `faulty`, `decommissioned`.",
        examples=["installed"],
    )
    installation_type: str | None = Field(
        description="Known values: `whole_current`, `ct_operated`.", examples=["whole_current"]
    )
    build: str | None = Field(
        description="Which portal record format the meter uses (`legacy` or `v2`). Normalised away here; "
        "kept for traceability.",
        examples=["legacy"],
    )
    location: Location | None = Field(description="`null` if the portal has no usable coordinates.")
    network: NetworkPath


class MeterListItem(Meter):
    distance_km: float | None = Field(
        default=None, description="Distance from (`near_lat`, `near_lng`). Only set for location searches."
    )


class MeterPage(BaseModel):
    items: list[MeterListItem]
    total: int = Field(description="Number of meters matching the filters, before `limit`/`offset`.")
    limit: int
    offset: int


# --- Readings and consumption ---------------------------------------------------------------------------------------


class Reading(BaseModel):
    """One raw meter reading. Energy values are cumulative registers (like an odometer), not usage."""

    timestamp: datetime = Field(description="ISO-8601 with offset. The portal's times are interpreted as IST (+05:30).")
    energy_kwh: float | None = Field(description="Cumulative active-energy register, kWh.")
    apparent_energy_kvah: float | None = Field(description="Cumulative apparent-energy register, kVAh.")
    voltage_r_v: float | None = Field(description="R-phase voltage at the reading time, volts.")


class ReadingsResponse(BaseModel):
    meter_id: str
    count: int
    readings: list[Reading]


class ConsumptionInterval(BaseModel):
    start: datetime
    end: datetime
    energy_kwh: float | None = Field(description="Active energy used in the interval. `null` if it can't be derived.")
    apparent_energy_kvah: float | None = Field(description="Apparent energy in the interval.")
    power_factor: float | None = Field(description="`energy_kwh / apparent_energy_kvah`, rounded to 3 decimals.")
    interval_count: int = Field(description="Number of 30-minute intervals summed into this row (1 for `30m`).")
    flags: list[str] = Field(
        description="Data-quality flags: `gap` (readings not 30 min apart), `missing_value`, `negative_delta` "
        "(register went down), `power_factor_above_1`, `partial_day` (daily rows with fewer than 48 intervals)."
    )


class ConsumptionResponse(BaseModel):
    meter_id: str
    interval: Literal["30m", "1d"]
    total_energy_kwh: float = Field(description="Sum of `energy_kwh` over all rows (nulls skipped).")
    intervals: list[ConsumptionInterval]


# --- Network and transformers ---------------------------------------------------------------------------------------


class NetworkTreeNode(BaseModel):
    level: str = Field(examples=["feeder"])
    code: str | None
    name: str | None = Field(description="Most common name reported for this node (see `issues` for conflicts).")
    meter_count: int
    capacity_kva: float | None = Field(default=None, description="Transformer capacity; only on `dt` nodes.")
    children: list["NetworkTreeNode"] = Field(default_factory=list)


class DataIssue(BaseModel):
    kind: str = Field(
        description="`blank_code`, `blank_name`, `conflicting_names`, `multiple_parents`, `feeder_mismatch` "
        "or `unknown_transformer`.",
        examples=["conflicting_names"],
    )
    level: str = Field(examples=["dt"])
    code: str | None = Field(examples=["DT-007"])
    message: str
    meter_ids: list[str] = Field(default_factory=list, description="Affected meters, where it's a short list.")


class NetworkResponse(BaseModel):
    levels: list[str] = Field(description="Hierarchy levels, top to bottom.")
    zones: list[NetworkTreeNode]
    issues: list[DataIssue] = Field(description="Inconsistencies found in the portal's hierarchy data.")


class Transformer(BaseModel):
    code: str = Field(examples=["DT-001"])
    name: str | None = Field(examples=["Malviya Nagar DT 1"])
    feeder_code: str | None = Field(examples=["F-001"])
    capacity_kva: float | None = Field(examples=[100])
    meter_count: int = 0


class TransformerList(BaseModel):
    items: list[Transformer]
    total: int


# --- Health and errors ----------------------------------------------------------------------------------------------


class MetadataStatus(BaseModel):
    loaded: bool
    meter_count: int
    fetched_at: datetime | None = Field(description="When meter metadata was last loaded from the portal (UTC).")
    age_seconds: float | None
    stale: bool = Field(description="True when the cached data is older than its TTL.")
    last_error: str | None = Field(description="Error from the most recent failed refresh, if any.")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(description="`degraded` when the last portal refresh failed.")
    portal_url: str
    metadata: MetadataStatus
    readings_cached_meters: int


class ErrorDetail(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["Meter J999999 not found"])
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
