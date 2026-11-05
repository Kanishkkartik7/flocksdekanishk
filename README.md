# Urja Meter API

A clean, documented HTTP API in front of **Urja Meter Ops**, a utility's internal web portal for smart-meter data. The portal has
no API of its own. With this service, other engineers and programs can get meter details, network position, readings and
consumption as clean JSON, without ever touching the portal.

| | |
|---|---|
| **How the portal really works** | [PROTOCOL.md](PROTOCOL.md): login, endpoints, the signed bulk export, quirks, data quality |
| **API spec** | [openapi.json](openapi.json) (OpenAPI 3.1). Interactive docs at `/docs` (Swagger UI) or `/redoc` when the service runs |
| **My reflection** | [REFLECTION.md](REFLECTION.md): the journey, the hard parts and the mistakes |

## The journey in short
1. I explored the portal with Chrome DevTools. The pages carry no data; they fetch JSON from a few `/portal/...` URLs.
2. Getting all the data through the visible pages would need about 827 requests and three parsers. I looked for hidden pages (a
   dead end), then searched the portal's JavaScript for `/portal/` and found a **signed bulk export** that returns all 403 meters
   in one request.
3. I rebuilt the signature in Python, then built the API on top of it.
4. **A later review found two real bugs of mine:** I had assumed every meter reports every 30 minutes (some report once a day),
   and my parser threw away good readings when the portal repeated a row. My tests did not catch this because they used a fake
   portal built from my own assumptions. I fixed both against real data. The full story is in [REFLECTION.md](REFLECTION.md).

## Quick start
Requires Python 3.11 or newer.
```bash
git clone https://github.com/Kanishkkartik7/flocksdekanishk.git
cd flocksdekanishk
python3 -m venv .venv             # Windows: python -m venv .venv
source .venv/bin/activate         # Windows PowerShell: .venv\Scripts\Activate.ps1   Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env              # Windows PowerShell: copy .env.example .env
                                  # then set PORTAL_PASSWORD in .env (the portal password from the assignment email)
uvicorn app.main:app              # → http://localhost:8000/docs
```
If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope Process Bypass` first. That allows scripts for the
current window only.
When it starts, the service logs in to the portal and loads every meter in the background. This takes a second or two.
Run the tests with `pytest`. They are offline and need no credentials.

**Configuration** (environment variables or a `.env` file; see [.env.example](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `PORTAL_URL` | `https://urja-ops.flockenergy.tech` | Portal base URL |
| `PORTAL_USERNAME`, `PORTAL_PASSWORD` | none (required) | Portal login |
| `METADATA_TTL_SECONDS` | `900` | How long meters, hierarchy and transformers stay cached |
| `READINGS_TTL_SECONDS` | `300` | How long each meter's readings stay cached |
| `PORTAL_MAX_ATTEMPTS` / `PORTAL_RETRY_BACKOFF_SECONDS` | `3` / `0.5` | Retries on timeouts, 429 and 502/503/504 (the wait doubles each time) |
| `PORTAL_MAX_CONCURRENCY` | `4` | Most portal requests at the same time |
| `PORTAL_SESSION_MAX_AGE_SECONDS` | `3300` | Log in again before the portal's 1-hour session ends |
| Others | | `PORTAL_TIMEOUT_SECONDS` (15), `REFRESH_RETRY_SECONDS` (30), `WARM_CACHE_ON_STARTUP` (true); all listed in [.env.example](.env.example) |

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
curl "http://localhost:8000/meters/J100000/consumption?interval=1d"     # a 30-minute meter; first two of eight rows shown
```
```json
{
  "meter_id": "J100000", "interval": "1d", "reading_interval_minutes": 30, "total_energy_kwh": 142.05,
  "intervals": [
    {"start": "2026-06-23T00:00:00+05:30", "end": "2026-06-24T00:00:00+05:30", "duration_minutes": 1440, "energy_kwh": 0.42,
     "apparent_energy_kvah": 0.45, "power_factor": 0.933, "interval_count": 1, "flags": ["partial_day"]},
    {"start": "2026-06-24T00:00:00+05:30", "end": "2026-06-25T00:00:00+05:30", "duration_minutes": 1440, "energy_kwh": 20.29,
     "apparent_energy_kvah": 21.92, "power_factor": 0.926, "interval_count": 48, "flags": []}
  ]
}
```
```bash
curl "http://localhost:8000/meters/J100400/consumption"     # a once-a-day meter: 7 intervals, `reading_interval_minutes: 1440`
```
```json
{
  "meter_id": "J100400", "interval": "native", "reading_interval_minutes": 1440, "total_energy_kwh": 159.17,
  "intervals": [
    {"start": "2026-06-23T00:00:00+05:30", "end": "2026-06-24T00:00:00+05:30", "duration_minutes": 1440, "energy_kwh": 22.74,
     "apparent_energy_kvah": 24.56, "power_factor": 0.926, "interval_count": 1, "flags": []}
  ]
}
```
(first of seven intervals shown)
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
| `GET /meters` | Filter by `status`, `make`, `phase`, `installation_type`, `build` or any network code (`zone` … `dt`). Repeating a parameter means OR. Also `q` text search, a `near_lat`/`near_lng`/`radius_km` location search, and `limit`/`offset`. Network codes other than `dt` repeat across branches (e.g. `feeder=F-001` matches two different feeders), so add the parent code to pin one branch: `substation=SS-01&feeder=F-001`. |
| `GET /meters/{id}` | Details, location and full network path. The ID is case-insensitive. |
| `GET /meters/{id}/readings` | Raw register readings (cumulative kWh, kVAh, voltage). Every 30 minutes or once a day, depending on the meter. Optional `start` and `end`. In a URL, write the `+` of a time offset as `%2B`. |
| `GET /meters/{id}/consumption` | Usage per reading interval (`native`, the default) or per IST day (`1d`), with power factor and data-quality flags. The response tells you the meter's step in `reading_interval_minutes` (30 or 1440). |
| `GET /network` | Zone → … → transformer tree with meter counts, plus `issues`: every inconsistency found in the portal's hierarchy. |
| `GET /transformers` | Every distribution transformer with its feeder, capacity (kVA) and number of meters. |
| `GET /health` | Cache state (age, staleness, last error). Does not call the portal. |

**Conventions:** snake_case fields; real numbers; lowercase enum values; ISO-8601 times with an offset (`+05:30`). Every error uses
`{"error": {"code", "message", "details"}}`: `404 not_found`, `422 invalid_request`, `429 portal_rate_limited`, `502 portal_*` (the
portal failed or rejected us), `504 portal_timeout`.

## How it is built
```
HTTP request
  → routers/            thin FastAPI handlers: check parameters, call the service
  → service.py          cache (meter snapshot + per-meter readings), staleness, queries
      → query.py         local filters and near-me search          (pure)
      → network.py       hierarchy tree + data-issue detection     (pure)
      → consumption.py   registers → interval/daily usage          (pure)
  → portal/client.py    the only code that talks to the portal: login, session, retries, signed export
      → portal/signing.py  HMAC-SHA256 request signature            (pure)
      → portal/parsers.py  raw portal JSON → clean models          (pure)
models.py               Pydantic response schemas = the API contract (openapi.json is generated from these)
```
The main rule: **everything about the portal's messy protocol stays inside `portal/`**. Nothing outside that folder knows about
SvelteKit, cookie names, `DD/MM/YYYY` dates or numbers sent as strings. "Pure" means no network or disk access, so those parts are
easy to test. `openapi.json` is generated by `python scripts/export_openapi.py`, and a test fails if the committed copy is out of date.

### Where the data comes from
1. **Meter details** (details, hierarchy, location) for all 403 meters come from **one signed request to the bulk export behind the
   portal's *Export all meters* button**, plus 2 pages of the transformer list. This becomes an in-memory snapshot that answers every `/meters`, `/network` and
   `/transformers` request locally.
2. **Readings** are fetched per meter **on demand** from `/portal/meters/{id}/energy` and cached for a short time.
3. **Session:** the client logs in the way the browser does (a form post, then the session cookie). It logs in again before the hour
   is up, or straight away if a response looks logged out.

## Assumptions
Details and the verified / inferred / unverified labels are in [PROTOCOL.md](PROTOCOL.md).
- **Timestamps are IST.** The portal's `23/06/2026 23:30` has no timezone. The utility is in Jaipur, so I read the times as IST
  (+05:30), a fixed offset because India has no daylight saving.
- **The export is the source of truth for meter details.** It matched the list and meter pages for the two meters I compared
  (J100000 legacy, J100004 v2), and it is the only source that is clean for both record formats.
- **The data is a static snapshot.** The eight meters whose readings I fetched all cover 23–30 June 2026. I have not checked the
  rest. If that holds, the cache times protect the portal; they do not catch updates.
- **Meters have different reading schedules.** J100000 and J100001 report every 30 minutes (337 rows). J100400 and five others
  report once a day (8 rows, or 9 with a repeated last row). I did not check the other 395 meters, so the API reads each meter's
  step from its own data and does not assume one. See [PROTOCOL.md §4](PROTOCOL.md#readings-energy).
- **IDs:** the portal's meter IDs are uppercase and case-sensitive. Accepting any case is safe because no two differ only by case.
- **Odd data is passed through, not "fixed".** J100000 is decommissioned yet has a full set of readings. They are returned as they are.
- **Session expiry:** once the 1-hour cookie is gone, the portal answers `401` with JSON (seen). I assume every data endpoint does the
  same. As a precaution, the client also treats a redirect, or an HTML page, as "logged out".

## Design decisions and trade-offs
- **Bulk export instead of scraping meter pages.** Building the same data from the list and meter pages takes about 827 requests and
  three parsers: devalue (SvelteKit's compact page-data format), legacy labels, and v2 double-encoded JSON. The export takes
  2 requests and has one format.
  - *Cost:* the client depends on an endpoint that the UI reaches through one button, plus its HMAC signing. If it disappears,
    meter details go stale. The meter pages would be the fallback; it is described but not built.
- **Hierarchy keyed by path, with problems reported, not repaired.** The portal's hierarchy is not a clean tree:
  - codes appear under several parents,
  - some codes are blank,
  - one transformer has two names.

  Moving nodes to a "best" parent would silently move meters. So `/network` keeps each meter's path as reported and lists every
  problem in `issues`, so the data's owners can fix it at the source.
  - *Cost:* a node cannot be looked up by its code alone.
- **The reading step is read from the data.** The meters I checked report either every 30 minutes or once a day (found late, through
  J100400). So `/consumption` works out each meter's normal step and flags a `gap` only against that. A day is `partial_day` only
  when it has fewer readings than that step implies. *Cost:* "the most common gap" is a heuristic. A meter with very irregular
  readings gets an arbitrary step.
- **Missing readings are never filled in.** Readings further apart than the meter's normal step stay one long interval, flagged
  `gap` (and `spans_multiple_days` beyond 24 hours), with its real `duration_minutes`. In `1d` mode such an interval is its own row
  instead of being added to the day it started on, so totals stay the same and no daily pattern is made up. `1d` is refused (422)
  for a meter that reads less often than daily.
- **Duplicate timestamps keep the row with values.** The portal repeats the last row of five meters I checked (J100089, J100134,
  J100195, J100211, J100330), the second copy with a blank `kwh`. The row with more values wins, the later row on a tie, and rows are
  never mixed. (My first version kept the later row, which threw the real reading away.)
- **Two readings endpoints.** `/readings` gives the raw cumulative registers, for people who need the source values. `/consumption`
  gives the differences, which is what most people want. n readings give n − 1 intervals. A register that goes down is flagged
  `negative_delta` and returns `null`, not a negative usage.
- **One clean contract.** The portal mixes four naming styles and sends numbers as strings. All of that is cleaned in one tested
  module (`portal/parsers.py`). Unknown enum values pass through as snake_case instead of failing, in case the portal adds new ones.
- **In-memory index instead of a database.** 403 meters is about 230 kB, and filtering them with a simple loop takes well under a
  millisecond. A database would add setup and moving parts for no benefit at this size (see [Scale](#scale-where-this-would-struggle)).
- **Be gentle with the portal.** At most 4 requests at a time. Several requests for the same meter at once share one portal call.
  Meter details are fetched once per cache period, not once per request.

### Freshness and failure handling
- **Cache times:** meter details for 15 minutes; readings for 5 minutes per meter. `/health` shows the cache's age and whether it is stale.
- **Stale-on-error (meter details only):** if a refresh of the meter details fails (the portal is down, or the export is rejected),
  the service **keeps serving the last good copy** and `/health` turns `degraded`. Readings are not served stale: if a meter's cached
  readings have expired and the portal fetch fails, that request gets a `502`/`504`.
- **Retry cooldown:** after a failed refresh, the service waits 30 seconds before asking the portal again, so during an outage the
  portal is tried at most once every 30 seconds. Requests that arrive while an attempt is running wait for it. With the default
  15-second timeout and 3 attempts per request, that can be 45 seconds or more. The same wait applies if the very first load fails. During those 30 seconds, requests get the same error straight away, so a wrong
  password cannot turn into a flood of failed logins.
- **Retries:** after a timeout, a connection error, a 429 or a 502/503/504, a request is tried up to 3 times in total (2 retries),
  with a growing wait; the portal does return an occasional 502. Signed requests get a new signature on each retry.
- **Rate limits:** a `429` from the portal is retried, then returned as `429 portal_rate_limited`. I did not see a 429 myself; this is a
  precaution.
- **Errors:** anything else that still fails becomes a clean `502` or `504` with a stable error code. Raw portal errors are never passed on.

### Scale: where this would struggle
The current design is right for hundreds to a few thousand meters. Around **10⁵ meters** it would struggle:
- **Export size:** the export would become a 50+ MB response, downloaded again on every refresh.
- **Readings for analytics:** a question across all meters needs one portal call per meter.
- **One process only:** each uvicorn worker has its own cache and its own portal session (run a single worker today).

What I would change at that scale:
- keep the data in SQLite or Postgres (PostGIS for location search), with indexes on the filter fields;
- move refreshes into a background job instead of doing them while a request waits;
- load readings step by step into a time-series table;
- share the cache between workers (Redis), or run one sync worker plus stateless API workers.

## What I intentionally left out
- **The `__data.json` fallback.** It would need a devalue decoder and both meter-page formats. The export covers the same data, so I
  described the fallback instead of building a second code path.
- **Bulk readings and time-series storage.** Readings are fetched on demand. Pulling all 403 meters on a schedule would be easy, but it
  is only worth it for cross-meter analytics, which nobody has asked for yet.
- **Auth, rate limiting and CORS on this API.** It is an internal service; in production it would sit behind the platform's gateway.
- **A web client.** I chose to spend the time on the protocol write-up and on robustness.
- **Saving anything to disk.** The cache is rebuilt from the portal in about a second.
- **A full survey of every meter's readings.** It would need 403 requests to the portal, which I did not want to send.

## What I would improve with more time
- Check every meter's reading schedule, slowly, and turn the "unverified" labels into real numbers.
- Settle the remaining protocol questions (see [PROTOCOL.md §10](PROTOCOL.md#10-open-questions)), then tighten the client to match.
- Add contract tests that run against the live portal on a schedule, so portal changes are caught early. My current tests cannot do this.
- Build the `__data.json` fallback so meter details survive if the export is withdrawn.
- A small web client: a map of meters coloured by status, consumption charts, and a hierarchy explorer that highlights `issues`.
- Refresh meter details in the background while serving the old copy, so no request ever waits on a slow portal. Serve readings
  stale on error too, as meter details already are.
- A persistent store, as above, plus metrics (portal delay, retries, re-logins) next to `/health`.

## Testing
`pytest` runs 65 offline tests in a couple of seconds:
- **Units:** parsers, consumption maths, the hierarchy and issue detection, filters and distance. **Signing** is checked against a value
  computed with Node's WebCrypto, the same API the portal's own JavaScript uses.
- **API tests** run against a **fake portal** (`tests/conftest.py`) that copies the protocol as I understand it: form-action login,
  cookie sessions, signature checks, JSON and HTML 404s. They cover:
  - retries on 502 and 429,
  - login again after expiry (three styles),
  - stale data served during an outage,
  - a failed first load that is not retried on every request,
  - wrong credentials,
  - the error shape,
  - a once-a-day meter (J100400), a repeated last row (J100089), and a meter reading less often than daily.
- **Drift check:** `openapi.json` must match the code.
- **Fixtures** are real portal responses chosen to include each data oddity. A few unit tests use small made-up readings to test one
  rule at a time (for example, a falling register or a weekly step). Those tests say so in a comment.
- **The honest limit:** the fake portal reflects my own understanding. It cannot prove my understanding is right. That is how the
  two reading bugs got past the first version of the tests. There is no live contract test yet.

## How I used AI
I worked with Claude Code (Anthropic's AI coding assistant) in VS Code. This is how the work was split.

**What I did**
- **Explored the portal myself** in Chrome DevTools: logging in, clicking through pages, running Console snippets, reading the page
  source and JavaScript, and pasting the results back. Everything marked *observed* or *verified* in [PROTOCOL.md](PROTOCOL.md)
  comes from that or from a live response.
- **Made the scope decisions:** Python and FastAPI; the core API plus the hierarchy, filtering, freshness and robustness extras; no
  web client; the last stretch of time spent on docs and review.
- **Settled the questions that guessing could not:** I waited out a session to see what an expired one returns, and tried one wrong
  password.
- **Edited the drafts** of this README, PROTOCOL.md and [REFLECTION.md](REFLECTION.md).

**What Claude did**
- Read the brief and planned the work with me. It guided the investigation one step at a time: which request to look at next, the
  Console snippets to run, and what the answers meant (SvelteKit's data format, the HMAC signing code, and meter terms such as
  cumulative registers, kVAh and power factor).
- Analysed the export I downloaded for data problems and helped pick the test fixtures.
- Proposed the design and wrote most of the code and tests.
- Made a few read-only calls to the live portal from Python, at a low rate, to capture sample responses.
- Drafted this README, PROTOCOL.md and REFLECTION.md in my voice, for me to edit, and rewrote them during the later review.
- **In a later review,** reviewed the finished code, helped me confirm two bugs against the live portal, wrote the fixes and the new
  tests, and corrected the docs. I approved each change before it was made.
