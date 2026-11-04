# Urja Meter API

A clean, documented HTTP API in front of **Urja Meter Ops**, a utility's internal web portal for smart-meter data that has
no API of its own. Other engineers and programs can use this service to get meter details, network position, readings and
consumption as clean JSON, without ever touching the portal.

| | |
|---|---|
| **How the portal works underneath** | [PROTOCOL.md](PROTOCOL.md): auth, endpoints, the signed bulk export, quirks, data quality |
| **API spec** | [openapi.json](openapi.json) (OpenAPI 3.1). Interactive docs at `/docs` (Swagger UI) or `/redoc` when running |
| **Reflection** | [REFLECTION.md](REFLECTION.md) |

## Quick start
Requires Python 3.11+.
```bash
git clone https://github.com/Kanishkkartik7/flocksdekanishk.git && cd flocksdekanishk
python -m venv .venv
source .venv/bin/activate         # Windows PowerShell: .venv\Scripts\Activate.ps1   Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env              # then set PORTAL_PASSWORD (the portal login from the assignment)
uvicorn app.main:app              # → http://localhost:8000/docs
```
On startup the service logs in to the portal and loads every meter in the background, which takes a second or two.
Run the tests with `pytest`; they're offline and need no credentials.

**Configuration** (environment variables or `.env`; see [.env.example](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `PORTAL_URL` | `https://urja-ops.flockenergy.tech` | Portal base URL |
| `PORTAL_USERNAME`, `PORTAL_PASSWORD` | – (required) | Portal login |
| `METADATA_TTL_SECONDS` | `900` | How long meters, hierarchy and transformers are cached |
| `READINGS_TTL_SECONDS` | `300` | How long each meter's readings are cached |
| `PORTAL_MAX_ATTEMPTS` / `PORTAL_RETRY_BACKOFF_SECONDS` | `3` / `0.5` | Retries on timeouts and 502/503/504 (backoff doubles) |
| `PORTAL_MAX_CONCURRENCY` | `4` | Most portal requests in flight at once |
| `PORTAL_SESSION_MAX_AGE_SECONDS` | `3300` | Log in again before the portal's 1-hour session ends |

## Sample requests
```bash
curl http://localhost:8000/meters/J100000
```
```json
{
  "meter_id": "J100000", "serial_number": "SE33962", "make": "HPL", "phase": "single",
  "status": "decommissioned", "installation_type": "whole_current", "build": "legacy",
  "location": {"latitude": 26.938961002479868, "longitude": 75.83095696146852},
  "network": {
    "zone": {"code": "Z-01", "name": "Jaipur Zone 1"}, "circle": {"code": "C-01", "name": "Circle 1"},
    "division": {"code": "D-01", "name": "Division 1"}, "subdivision": {"code": "SD-01", "name": "Subdivision 1"},
    "substation": {"code": "SS-01", "name": "Substation 1"}, "feeder": {"code": "F-001", "name": "Feeder 1"},
    "dt": {"code": "DT-001", "name": "Malviya Nagar DT 1"}
  }
}
```
```bash
curl "http://localhost:8000/meters/J100000/consumption?interval=1d"     # first two of eight rows shown
```
```json
{
  "meter_id": "J100000", "interval": "1d", "total_energy_kwh": 142.05,
  "intervals": [
    {"start": "2026-06-23T00:00:00+05:30", "end": "2026-06-24T00:00:00+05:30", "energy_kwh": 0.42,
     "apparent_energy_kvah": 0.45, "power_factor": 0.933, "interval_count": 1, "flags": ["partial_day"]},
    {"start": "2026-06-24T00:00:00+05:30", "end": "2026-06-25T00:00:00+05:30", "energy_kwh": 20.29,
     "apparent_energy_kvah": 21.92, "power_factor": 0.926, "interval_count": 48, "flags": []}
  ]
}
```
More examples:
```bash
curl "http://localhost:8000/meters?status=faulty&make=L%26T&limit=5"           # 18 faulty L&T meters
curl "http://localhost:8000/meters?near_lat=26.9124&near_lng=75.7873&radius_km=2"  # nearest first, with distance_km
curl "http://localhost:8000/meters?dt=DT-007&status=installed"                  # meters on one transformer
curl "http://localhost:8000/meters/j100000/readings?start=2026-06-24T00:00&end=2026-06-24T06:00"
curl  http://localhost:8000/network        # hierarchy tree + list of data issues
curl  http://localhost:8000/transformers   # 40 DTs with capacity and meter count
curl  http://localhost:8000/meters/J999999 # {"error":{"code":"not_found","message":"Meter J999999 not found","details":null}}
```

## Endpoints
| Endpoint | What it gives you |
|---|---|
| `GET /meters` | Filter by `status`, `make`, `phase`, `installation_type`, `build` or any network code (`zone` … `dt`). Repeating a parameter means OR. Also `q` text search, a `near_lat`/`near_lng`/`radius_km` location search, and `limit`/`offset`. |
| `GET /meters/{id}` | Details, location, full network path. The ID is case-insensitive. |
| `GET /meters/{id}/readings` | Raw 30-minute register readings (cumulative kWh, kVAh, voltage), optionally between `start` and `end`. |
| `GET /meters/{id}/consumption` | Usage per interval (`30m`) or per IST day (`1d`), with power factor and data-quality flags. |
| `GET /network` | Zone → … → transformer tree with meter counts, plus `issues`: every inconsistency found in the portal's hierarchy. |
| `GET /transformers` | Every distribution transformer with its feeder, capacity (kVA) and number of meters. |
| `GET /health` | Cache state (age, staleness, last error). Doesn't call the portal. |

**Conventions:** snake_case fields; real numbers; lowercase enum values; ISO-8601 times with offset (`+05:30`). Every error uses
`{"error": {"code", "message", "details"}}`: `404 not_found`, `422 invalid_request`, `502 portal_*` (the portal failed or
rejected us), `504 portal_timeout`.

## How it's built
```
HTTP request
  → routers/            thin FastAPI handlers: validate params, call the service
  → service.py          cache (metadata snapshot + per-meter readings), staleness, queries
      → query.py         local filters and near-me search          (pure)
      → network.py       hierarchy tree + data-issue detection     (pure)
      → consumption.py   registers → interval/daily usage          (pure)
  → portal/client.py    the only code that talks to the portal: login, session, retries, signed export
      → portal/signing.py  HMAC-SHA256 request signature            (pure)
      → portal/parsers.py  raw portal JSON → clean models          (pure)
models.py               Pydantic response schemas = the API contract (openapi.json is generated from these)
```
The rule: **everything about the portal's messy protocol stays in `portal/`**. Nothing outside that package knows about
SvelteKit, cookie names, `DD/MM/YYYY` or string-typed numbers. `openapi.json` is generated by `python scripts/export_openapi.py`,
and a test fails if the committed copy gets out of date.

### Where the data comes from
1. **Meter metadata** (details, hierarchy, location) for all 403 meters comes from **one signed request to the portal's hidden
   bulk export**, plus 2 pages of the transformer list. That gives an in-memory snapshot, which answers every `/meters`, `/network`
   and `/transformers` request locally.
2. **Readings** are fetched per meter **on demand** from `/portal/meters/{id}/energy` and cached briefly.
3. **Session:** the client logs in like the browser does (a form post, then the session cookie). It logs in again before the hour
   is up, or immediately if a response looks logged out.

## Assumptions
- **Timestamps are IST.** The portal's `23/06/2026 23:30` has no timezone. The utility is in Jaipur, so the times are read as
  IST (+05:30), a fixed offset because India has no daylight saving time.
- **The export is the source of truth for meter metadata.** It matched the list and meter pages for the meters I compared
  (J100000 legacy, J100004 v2), and it's the only source that's clean for both record formats.
- **The data is a static snapshot.** Every meter's readings cover the same week, 23–30 June 2026. The TTLs are therefore about
  protecting the portal, not about catching updates.
- **IDs:** the portal's meter IDs are uppercase and case-sensitive. Accepting any case is safe because no two differ only by case.
- **Odd data is passed through, not "fixed".** Decommissioned meters still have readings; those are returned as-is.
- **Session expiry:** once the 1-hour cookie is gone, the portal answers `401` with JSON (observed). I assume every data endpoint
  behaves the same. To be safe, the client also treats a redirect, or an HTML page, as "logged out".

## Design decisions and trade-offs
- **Bulk export over scraping meter pages.** Building the same data from the list and the meter pages takes about 827 requests and
  three parsers (devalue, legacy labels, v2 double-encoded JSON). The export takes 2 requests and has one format.
  - *Cost:* the client depends on an endpoint the UI only reaches through one button, plus its HMAC signing. If it disappears,
    metadata goes stale. The meter pages would be the fallback; it's documented but not built.
- **Hierarchy keyed by path, with problems reported rather than repaired.** The portal's hierarchy isn't a clean tree:
  - codes appear under several parents,
  - some codes are blank,
  - one transformer has two names.
  
  Re-parenting nodes would silently move meters, so `/network` keeps each meter's path as reported and lists every anomaly in
  `issues`, so the data's owners can fix it at the source.
  - *Cost:* a node can't be looked up by code alone.
- **Two readings endpoints.** `/readings` returns the raw cumulative registers, for people who need the source values.
  `/consumption` returns the differences, which is what most consumers actually want. 337 readings give 336 intervals. A
  register that goes down is flagged `negative_delta` and returns `null`, rather than a negative usage value.
- **One clean contract.** The portal mixes four naming schemes and sends numbers as strings. All of that is normalised in one
  tested module (`portal/parsers.py`). Unknown enum values pass through as snake_case instead of failing, in case the
  portal adds new ones.
- **In-memory index instead of a database.** 403 meters is about 230 kB, and filtering them with a linear scan takes well under a
  millisecond. A database would add setup and moving parts for no benefit at this size (see [Scale](#scale-where-this-would-struggle)).
- **Be gentle with the portal.** At most 4 requests at a time. A burst of concurrent requests for the same meter shares one portal call.
  Metadata is fetched once per TTL, not once per request.

### Freshness and failure handling
- **TTLs:** metadata is cached for 15 minutes; readings for 5 minutes per meter. `/health` reports the cache's age and whether it's stale.
- **Stale-on-error:** if a refresh fails (the portal is down, or the export is rejected), the service **keeps serving the last good data**
  and `/health` turns `degraded`. It waits 30 seconds before trying the portal again, so an outage doesn't make every request slow.
  The same wait applies if the very first load fails. During those 30 seconds, requests get the same error straight away, so a
  wrong password can't turn into a stream of failed logins.
- **Retries:** timeouts, connection errors and 502/503/504 are retried up to 3 times with exponential backoff (the portal does return
  occasional 502s). Signed requests get a new signature on each retry.
- **Errors:** anything that still fails becomes a clean `502`/`504` with a stable error code. Raw portal errors are never passed through.

### Scale: where this would struggle
The current design is right for hundreds to a few thousand meters. Around **10⁵ meters** it would struggle:
- **Export size:** the export becomes a 50+ MB response that has to be re-downloaded on every refresh.
- **Readings for analytics:** a question across all meters needs one portal call per meter.
- **One process only:** each uvicorn worker holds its own cache and its own portal session (run a single worker today).

What I'd change at that scale:
- persist to SQLite or Postgres (PostGIS for location search), with indexes on the filter fields;
- move refreshes into a background sync job instead of doing them at request time;
- ingest readings incrementally into a time-series table;
- share the cache between workers (Redis), or run one sync worker plus stateless API workers.

## What I intentionally left out
- **The `__data.json` fallback.** It would need a devalue decoder and both meter-page formats. The export covers the same data,
  so I documented the fallback rather than building a second code path.
- **Bulk readings and time-series storage.** Readings are fetched on demand. Pulling all 403 meters on a schedule would be easy, but it's
  only worth doing for cross-meter analytics, which nobody has asked for yet.
- **Auth, rate limiting and CORS on this API.** It's an internal service; in production it would sit behind the platform's gateway.
- **A web client.** I chose to spend the time on the protocol write-up and the robustness work.
- **Persisting anything to disk.** The cache is rebuilt from the portal in about a second.

## What I'd improve with more time
- Settle the remaining protocol questions: whether a session that expired on the server also gives a 401, how long a signature
  stays valid, whether the secret rotates. Then tighten the client to match what the portal actually does.
- Build the `__data.json` fallback so metadata survives the export being withdrawn, plus a scheduled job that checks the export
  against the meter pages.
- A small web client: a map of meters coloured by status, consumption charts, a hierarchy explorer that highlights `issues`.
- Background refresh and a persistent store, as above. Metrics (portal latency, retries, re-logins) alongside `/health`.
- Contract tests that run against the live portal on a schedule, to catch protocol changes early.

## Testing
`pytest` runs 46 offline tests in under a second:
- **Units:** parsers, consumption maths, the hierarchy and issue detection, filters and distance. **Signing** is checked against a
  value computed with Node's WebCrypto, the same API the portal's own JavaScript uses.
- **API tests** run against a **fake portal** (`tests/conftest.py`) that copies the real protocol: form-action login, cookie
  sessions, signature verification, JSON vs HTML 404s. They cover:
  - retries on 502,
  - re-login after expiry (three styles),
  - stale data served during an outage,
  - a failed first load that isn't retried on every request,
  - wrong credentials,
  - the error shape.
- **Drift check:** `openapi.json` must match the code.
- **Fixtures** are real portal records chosen to include each data anomaly.

## How I used AI
I worked with Claude Code (Anthropic's AI coding assistant) in VS Code for the whole assignment. This is how the work was split.

**What I did**
- **Explored the portal myself** in Chrome DevTools: logging in, clicking through the pages, running Console snippets, reading
  the page source and JS chunks, and pasting the results back. Claude never used the browser. Everything marked *observed* in
  [PROTOCOL.md](PROTOCOL.md) comes from that, or from a live response.
- **Made the scope calls:** Python + FastAPI; the core API plus the hierarchy, filtering, freshness and robustness extensions;
  no web client; the last stretch of time spent on docs and review rather than new features.
- **Settled the questions that guessing couldn't:** I waited out a session to see what an expired one returns, and tried one
  wrong password.
- **Edited the drafts** of this README, PROTOCOL.md and [REFLECTION.md](REFLECTION.md).

**What Claude did**
- Read the brief and planned the work with me. It then guided the investigation one step at a time: which request to look at
  next, the Console snippets to run, and what the answers meant. That covered SvelteKit's data format and route list, the HMAC
  signing code in the Transformers page, and the metering terms (cumulative registers, kVAh, power factor).
- Analysed the export I downloaded for data-quality problems, and picked the test fixtures from it.
- Proposed the design and wrote nearly all of the code and tests. The reasoning is under
  [Design decisions](#design-decisions-and-trade-offs).
- Once the client worked, it made a few read-only checks against the live portal from Python, at a low request rate. It also
  ran the service against the portal to capture the sample responses above.
- Drafted this README, PROTOCOL.md and REFLECTION.md in my voice, for me to edit. The reflection draws only on what actually
  happened during the work: my assumptions, my dead ends, my mistakes. Claude also kept a working journal and decision log in a
  notes folder that isn't part of this repo. The decisions that matter are written out in this README.

**Checking the AI's work**
- Claude's guesses about the portal stayed marked *inferred* or *not verified* until I checked them in the browser. The
  expired-session answer turned out to be a `401` with JSON, which the client already handled. The failed-login answer
  (`{"type":"failure","status":401,…}`) led to a clearer error message, and a test that a wrong password is never retried.
- It got things wrong too:
  - It once stated a fact before checking it: that three-phase meters report only one voltage. It flagged the slip itself, and a
    live check then confirmed the claim.
  - It gave me pseudo-code that I ran in the Console, where it failed.
  - Its first version loaded the meters and the transformers in parallel, which could leave a request running after the other
    one failed. It's now sequential.
- The tests run offline against a fake portal that copies the real protocol, and the service was run against the live portal
  before submitting.
- I read every file before submitting, and can explain how it works and why it was built this way.
