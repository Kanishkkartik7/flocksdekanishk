"""The service layer: caching and answering questions, on top of the portal client.

Freshness: meter metadata (meters, hierarchy, transformers) comes from one export and is cached for
`metadata_ttl_seconds`. Readings are cached per meter for `readings_ttl_seconds`. The portal's data is a fixed
snapshot today, so these TTLs are about not hammering the portal, not about missing updates. If a refresh fails,
the last good data keeps being served (and /health says `degraded`) rather than failing every request. Either way,
the portal isn't asked again for `refresh_retry_seconds`: if the very first load fails, requests in that window get
the same error straight away, so a wrong password can't turn into a stream of failed logins.
"""

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError

from app import consumption
from app.config import Settings
from app.errors import NotFoundError, PortalError, PortalNotFound
from app.models import (
    ConsumptionResponse,
    HealthResponse,
    Meter,
    MeterListItem,
    MeterPage,
    MetadataStatus,
    NetworkResponse,
    Reading,
    ReadingsResponse,
    Transformer,
    TransformerList,
)
from app.network import build_network
from app.portal.client import PortalClient
from app.portal.parsers import IST, normalize_meter_id, parse_export_meter, parse_readings, parse_transformer
from app.query import MeterFilter, filter_meters

log = logging.getLogger(__name__)


@dataclass
class Snapshot:
    meters: dict[str, Meter]
    transformers: dict[str, Transformer]
    network: NetworkResponse
    fetched_at: datetime
    loaded_at: float  # time.monotonic()


class MeterService:
    def __init__(self, client: PortalClient, settings: Settings):
        self._client = client
        self._settings = settings
        self._snapshot: Snapshot | None = None
        self._refresh_lock = asyncio.Lock()
        self._last_error: str | None = None
        self._last_failure: PortalError | None = None
        self._last_failure_at: float | None = None
        self._readings: dict[str, tuple[float, list[Reading]]] = {}
        self._readings_locks: dict[str, asyncio.Lock] = {}

    # --- Metadata ---------------------------------------------------------------------------------------------------

    async def snapshot(self) -> Snapshot:
        current = self._snapshot
        if current is not None and not self._needs_refresh(current):
            return current
        async with self._refresh_lock:
            current = self._snapshot
            if current is not None and not self._needs_refresh(current):
                return current
            if current is None and self._last_failure is not None and self._cooling_down():
                # The first load failed moments ago (portal down, wrong password). Answer with the same error
                # instead of hitting the portal, and logging in again, on every request.
                raise type(self._last_failure)(self._last_failure.message)
            try:
                self._snapshot = await self._load_snapshot()
                self._last_error = None
                self._last_failure = None
                self._last_failure_at = None
            except PortalError as exc:
                self._last_error = exc.message
                self._last_failure = exc
                self._last_failure_at = time.monotonic()
                if current is None:
                    raise
                log.warning("Metadata refresh failed; serving data from %s: %s", current.fetched_at, exc.message)
                return current
            return self._snapshot

    async def warm(self) -> None:
        try:
            await self.snapshot()
        except PortalError as exc:
            log.warning("Could not preload metadata at startup: %s", exc.message)

    def _needs_refresh(self, snap: Snapshot) -> bool:
        if time.monotonic() - snap.loaded_at < self._settings.metadata_ttl_seconds:
            return False
        # Stale. After a failed refresh, wait a little before hitting the portal again.
        return not self._cooling_down()

    def _cooling_down(self) -> bool:
        """True for `refresh_retry_seconds` after a failed load from the portal."""
        return (
            self._last_failure_at is not None
            and time.monotonic() - self._last_failure_at < self._settings.refresh_retry_seconds
        )

    async def _load_snapshot(self) -> Snapshot:
        raw_meters = await self._client.fetch_export()
        raw_transformers = await self._client.fetch_transformers()
        meters: dict[str, Meter] = {}
        for raw in raw_meters:
            try:
                meter = parse_export_meter(raw)
            except (KeyError, TypeError, ValidationError) as exc:
                log.warning("Skipping unparseable export record %r: %s", raw.get("meterId"), exc)
                continue
            meters[meter.meter_id] = meter

        counts = Counter(m.network.dt.code for m in meters.values())
        transformers = {}
        for raw in raw_transformers:
            transformer = parse_transformer(raw)
            if transformer is not None:
                transformers[transformer.code] = transformer.model_copy(
                    update={"meter_count": counts.get(transformer.code, 0)}
                )

        log.info("Loaded %d meters and %d transformers from the portal", len(meters), len(transformers))
        return Snapshot(
            meters=dict(sorted(meters.items())),
            transformers=dict(sorted(transformers.items())),
            network=build_network(meters.values(), transformers),
            fetched_at=datetime.now(UTC),
            loaded_at=time.monotonic(),
        )

    # --- Queries ----------------------------------------------------------------------------------------------------

    async def list_meters(self, f: MeterFilter, limit: int, offset: int) -> MeterPage:
        snap = await self.snapshot()
        matches = filter_meters(snap.meters.values(), f)
        page = matches[offset : offset + limit]
        items = [MeterListItem(**m.model_dump(), distance_km=None if d is None else round(d, 3)) for m, d in page]
        return MeterPage(items=items, total=len(matches), limit=limit, offset=offset)

    async def get_meter(self, meter_id: str) -> Meter:
        snap = await self.snapshot()
        meter = snap.meters.get(normalize_meter_id(meter_id))
        if meter is None:
            raise NotFoundError(f"Meter {normalize_meter_id(meter_id)} not found")
        return meter

    async def get_readings(self, meter_id: str, start: datetime | None, end: datetime | None) -> ReadingsResponse:
        meter = await self.get_meter(meter_id)
        readings = _within(await self._readings_for(meter.meter_id), start, end)
        return ReadingsResponse(meter_id=meter.meter_id, count=len(readings), readings=readings)

    async def get_consumption(
        self, meter_id: str, start: datetime | None, end: datetime | None, interval: Literal["30m", "1d"]
    ) -> ConsumptionResponse:
        meter = await self.get_meter(meter_id)
        readings = _within(await self._readings_for(meter.meter_id), start, end)
        rows = consumption.half_hourly(readings)
        if interval == "1d":
            rows = consumption.daily(rows)
        return ConsumptionResponse(
            meter_id=meter.meter_id, interval=interval, total_energy_kwh=consumption.total_kwh(rows), intervals=rows
        )

    async def network(self) -> NetworkResponse:
        return (await self.snapshot()).network

    async def transformers(self) -> TransformerList:
        items = list((await self.snapshot()).transformers.values())
        return TransformerList(items=items, total=len(items))

    def health(self) -> HealthResponse:
        snap = self._snapshot
        age = None if snap is None else time.monotonic() - snap.loaded_at
        metadata = MetadataStatus(
            loaded=snap is not None,
            meter_count=0 if snap is None else len(snap.meters),
            fetched_at=None if snap is None else snap.fetched_at,
            age_seconds=None if age is None else round(age, 1),
            stale=age is None or age > self._settings.metadata_ttl_seconds,
            last_error=self._last_error,
        )
        return HealthResponse(
            status="degraded" if self._last_error else "ok",
            portal_url=self._settings.portal_url,
            metadata=metadata,
            readings_cached_meters=len(self._readings),
        )

    # --- Readings cache ---------------------------------------------------------------------------------------------

    async def _readings_for(self, meter_id: str) -> list[Reading]:
        cached = self._readings.get(meter_id)
        if cached and time.monotonic() - cached[0] < self._settings.readings_ttl_seconds:
            return cached[1]
        lock = self._readings_locks.setdefault(meter_id, asyncio.Lock())
        async with lock:  # concurrent requests for the same meter share one portal call
            cached = self._readings.get(meter_id)
            if cached and time.monotonic() - cached[0] < self._settings.readings_ttl_seconds:
                return cached[1]
            try:
                rows = await self._client.fetch_energy(meter_id)
            except PortalNotFound as exc:
                raise NotFoundError(f"Meter {meter_id} has no readings on the portal") from exc
            readings = parse_readings(rows)
            self._readings[meter_id] = (time.monotonic(), readings)
            return readings


def _within(readings: list[Reading], start: datetime | None, end: datetime | None) -> list[Reading]:
    """Inclusive time filter. Times without an offset are taken as IST, like the portal's own."""
    start, end = _as_ist(start), _as_ist(end)
    return [r for r in readings if (start is None or r.timestamp >= start) and (end is None or r.timestamp <= end)]


def _as_ist(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=IST)
