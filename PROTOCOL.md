# How the Urja Meter Ops portal works

What I found by exploring https://urja-ops.flockenergy.tech as `operator@urja.local` on 4 Nov 2026. Almost everything
came from the browser (Chrome DevTools) and normal page use. A few read-only checks were made from Python once the client
worked. An AI assistant guided the exploration; see [§9](#9-how-i-investigated). Everything here was **observed**, except
where it says *inferred* or *not verified*. The API in this repo builds on these findings; see the [README](README.md).

## Summary
- The portal is a **SvelteKit** app with **Better Auth** sessions: one cookie, valid for 1 hour. Login is a form post whose
  outcome (`redirect` or `failure`) is in the JSON body. Without a valid session, the data endpoints answer `401` with JSON.
- The pages' HTML contains **no data**. The browser fetches JSON from **six endpoints under `/portal/`**, and one more
  page-data URL (`__data.json`).
- **There is a bulk path.** The *Export all meters* button calls an HMAC-signed endpoint, `/portal/export`, which returns
  **all 403 meters** with hierarchy and location in one response. The API uses it as its main source.
- **Readings** are 30-minute **cumulative** registers (kWh, kVAh, voltage), available one meter at a time, for a fixed week:
  23–30 June 2026.
- The data has **quirks and inconsistencies** that a consumer would trip over: numbers as strings, four naming schemes,
  two record formats, blank and conflicting hierarchy values. See [§7](#7-quirks-and-surprises) and [§8](#8-data-quality).

## 1. What it's built with
- **SvelteKit**, recognisable by `/_app/immutable/...` assets, the `x-sveltekit-action` header on form posts, `__data.json`
  page data, and the `kit.start(...)` bootstrap script.
- **Better Auth**, from the session cookie's name, `__Secure-better-auth.session_token`.
- **Pages**, read from the route list in `/_app/immutable/entry/app.<hash>.js`. A negative number in that list means the
  page has a server-side `load` function. There are no hidden pages.

| Route | Server load | What it shows / where its data comes from |
|---|---|---|
| `/` | yes | empty page, presumably a redirect |
| `/login` | yes | login form (a SvelteKit form action) |
| `/meters` | no | meter table → `GET /portal/meters/search` |
| `/meters/[id]` | yes | one meter → `__data.json`, plus `GET /portal/meters/{id}/geo` and `/energy` |
| `/transformers` | no | transformer table → `GET /portal/dts`, plus the **Export all meters** button |

The logged-in pages share a layout, `(portal)`, which loads the user on the server. "Ops Desk" in the header is just that
user's display name (`{name: "Ops Desk", email: "operator@urja.local"}`), not a link.

## 2. Access and authentication
```
POST /login                                   form-encoded: email, password
  accept: application/json                    → ask SvelteKit for a JSON result instead of a redirect
  x-sveltekit-action: true
  Origin: https://urja-ops.flockenergy.tech   → SvelteKit's CSRF check (see below)

← 200 OK  {"type":"redirect","status":303,"location":"/meters"}
  Set-Cookie: __Secure-better-auth.session_token=<token>.<signature>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax

GET /portal/...   Cookie: __Secure-better-auth.session_token=…   ← that cookie is all the data endpoints need
```
- **Login reports its outcome in the JSON body, not the HTTP status.** Success is the `"redirect"` above. A wrong password
  (tried once, observed) returns:
  ```json
  {"type":"failure","status":401,"data":"[{\"email\":1,\"error\":2},\"operator@urja.local\",\"Invalid email or password.\"]"}
  ```
  `data` is a string in SvelteKit's devalue format, the same as `__data.json`. It decodes to
  `{email: "operator@urja.local", error: "Invalid email or password."}`: the form gets the email back, never the password,
  and the message doesn't say which of the two was wrong. I captured only the body. SvelteKit sends action results as HTTP 200,
  as the successful login showed, so the failure is presumably a 200 too (*inferred*). My client reports `"failure"` as
  `portal_auth_failed` straight away, without retrying, and treats any other answer that isn't `"redirect"` as a failed login too.
- **There's no CSRF token.** SvelteKit instead rejects form posts whose `Origin` doesn't match the site. My client sends a matching
  `Origin`, and logging in from Python works. *Not verified* whether it's actually required.
- **Session lifetime: 1 hour.** No data response ever sent a new `Set-Cookie`, so the session doesn't seem to extend while
  in use (*inferred*). When `Max-Age` runs out, the browser deletes the cookie, and the data endpoints then answer
  **`401` with JSON**: `{"error":"unauthorized","message":"A valid session is required."}` (observed about 1.5 h after login).
  On a 401, my client logs in again once and retries. To be safe, it also treats a redirect, or HTML where JSON was expected,
  as "logged out". It also logs in again after 55 minutes, before the cookie expires.
- **Signing secret:** `GET /portal/keys` (cookie only) returns `{"data":{"signingSecret":"…"}}`, a secret of about 32 characters.
  See [§6](#6-the-bulk-path-signed-export).

## 3. Endpoints
I found these in the Network tab, then confirmed the list by searching every JavaScript chunk for `/portal/`. The client
code uses no others.

| Method | Path | Params | Returns |
|---|---|---|---|
| POST | `/login` | form `email`, `password` | ActionResult JSON: `redirect` plus the cookie, or `failure` (see [§2](#2-access-and-authentication)) |
| GET | `/portal/meters/search` | `q` (meter no. or serial), `page` | `{data, total, page, pageSize}`, 20 per page |
| GET | `/portal/meters/{id}/geo` | – | `{"data":{"latitude":"26.93…","longitude":"75.83…"}}` (strings) |
| GET | `/portal/meters/{id}/energy` | none (no date range) | `{"data":[{timestamp, kwh, kvah, voltR}, …]}`, 337 rows |
| GET | `/meters/{id}/__data.json` | `x-sveltekit-invalidated=001` | page data (devalue format): `meterId`, `detail`, `hierarchy` |
| GET | `/portal/dts` | `page` | `{data:[{code, name, feederCode, capacityKva}], total: 40, page, pageSize: 20}` |
| GET | `/portal/keys` | – | `{"data":{"signingSecret":"…"}}` |
| GET | `/portal/export` | `page`; headers `x-timestamp`, `x-signature` | `{"data":[…403 meters…], "total": 403}` |

```jsonc
// GET /portal/meters/search?q=&page=1   (one of 20 items)
{"meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single","installStatus":"Decommissioned","dtCode":"DT-001"}

// GET /portal/meters/J100000/energy   (one of 337 rows)
{"timestamp":"23/06/2026 23:30","kwh":"48438.74","kvah":"52313.84","voltR":"226"}

// GET /portal/dts?page=1   (one of 20 items)
{"code":"DT-001","name":"Malviya Nagar DT 1","feederCode":"F-001","capacityKva":100}

// GET /portal/export?page=1   (one of 403 items)
{"meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single","installStatus":"Decommissioned",
 "installType":"Whole Current","build":"legacy","dtCode":"DT-001",
 "hierarchy":{"zone":{"name":"Jaipur Zone 1","code":"Z-01"},"circle":{"name":"Circle 1","code":"C-01"}, …,
              "dt":{"name":"Malviya Nagar DT 1","code":"DT-001"}},
 "geo":{"lat":26.938961002479868,"lng":75.83095696146852}}
```
**Errors**
- No valid session gives **401** with `{"error":"unauthorized","message":"A valid session is required."}`.
- An unknown meter on a `/portal/` endpoint gives **404** with `{"error":"not_found","message":"Meter not found"}`.
- An unknown route gives **404 with an HTML** error page.
- So a client must check `content-type` before parsing.

## 4. Data model
### Meters
- **403 meters**, IDs `J100000` … (they look sequential). **IDs are case-sensitive:** `j100000` gives 404.
- **Status:** Installed 238 · Faulty 90 · Decommissioned 75.
- **Installation type:** Whole Current 205 · CT Operated 198.
- **Phase:** single 277 · three 126.
- **Make:** HPL 90 · Genus 88 · Secure 85 · Allied 76 · L&T 64.

### Two record formats (`build`)
The export has a field, `build`, which appears nowhere else: `legacy` (238 meters) or `v2` (165). It decides
how the **meter page** (`__data.json`) sends the details:
- **legacy:** `detail.data` is a list of `{parameterName, parameterValue}` pairs using display labels ("Serial No", "Installation Status").
- **v2:** `detail.classData` is a **JSON string inside the JSON** (double-encoded):
  `"{\"installed_meter\":{\"MeterId\":\"J100004\",\"SerialNo\":\"SE65293\",\"Make\":\"Genus\",…}}"`, using PascalCase keys.
- **Both:** the hierarchy part is the same, `{"Zone":"Jaipur Zone 1 (Z-01)", …, "DT":"Malviya Nagar DT 1 (DT-001)"}`.
- `/energy` and `/geo` look the same for both builds.

`__data.json` itself uses SvelteKit's **devalue** format: one flat array where every number inside an object is an index into
that array, so a repeated value is stored only once. The export carries the same information in one clean format for both builds.

### Network hierarchy
Zone → Circle → Division → Subdivision → Substation → Feeder → DT (distribution transformer) → Meter. Excluding blanks,
there are 3 zones, 6 circles, 10 divisions, 14 subdivisions, 18 substations, 28 feeders and 40 DTs. Each meter carries its whole path.
The DT list adds each transformer's capacity in kVA (63, 100, 250, …). Every DT's `feederCode` agrees with what its meters
report.

### Readings (`/energy`)
- **Every 30 minutes, 337 readings per meter**, over the same fixed window, `23/06/2026 23:30` → `30/06/2026 23:30`, for
  every meter, whatever its status or build. So it's a static snapshot, about four months older than today.
- **`kwh` and `kvah` are cumulative registers.** They only go up, like an odometer. **Usage is the difference between readings**,
  so 337 readings give **336 intervals**. kWh ÷ kVAh ≈ 0.93, which is the power factor.
- **`voltR`** is the R-phase voltage (220–240 V). Even a three-phase meter (J100005) reports only `voltR`, with no Y or B phase.
- **Timestamps** use the format `DD/MM/YYYY HH:MM`, **day first, with no timezone**. I read them as **IST (+05:30)**, since the utility is in Jaipur.
- **Every value is a string**, and trailing zeros are dropped (`"48440"`).

## 5. Paging and search
- `/portal/meters/search?q=&page=N` returns 20 per page; there are 403 meters, so 21 pages. `q` matches the meter number or serial.
  There's no other filtering or sorting.
- `/portal/dts?page=N` returns 20 per page; there are 40, so 2 pages.
- `/portal/export` returned all 403 on page 1, with no `page`/`pageSize` in the response. My client still keeps requesting
  pages until it has `total` records.

## 6. The bulk path: signed export
**How I found it.** The route list showed no hidden pages, so I fetched every JavaScript chunk and searched them for
`/portal/`. The Transformers page code mentions `/portal/keys` and `/portal/export`, neither of which is called when
the page loads. Its *Export all meters* button does this:
```
secret    = GET /portal/keys → data.signingSecret
timestamp = floor(Date.now() / 1000)                                   (Unix seconds)
message   = "GET" + "\n" + "/portal/export" + "\n" + "page=1" + "\n" + timestamp
signature = hex(HMAC-SHA256(key = secret, message))                    (browser WebCrypto)
GET /portal/export?page=1   x-timestamp: <timestamp>   x-signature: <signature>
```
- **Captured example:** `x-timestamp: 1793799552` (13:39:12 UTC; the server's `Date` header said 13:39:11, so the clocks agree),
  and a 64-hex-character signature. The response was 231,658 bytes and took 386 ms.
- **The signature covers the method, path, exact query string and time.** Every page needs its own signature, the query string must
  match character for character, and a retry needs a new signature. The server presumably rejects old timestamps.
  The accepted window is *not verified*.
- **Why it matters:** the export replaces about **827 requests** (21 list pages, 403 meter pages and 403 geo calls) with **2**,
  and it avoids all three meter-page formats.
- **It doesn't include readings.** Those are still one `/energy` call per meter.
- The UI only ever requests `page=1`. *Not verified:* what page 2 returns, and whether the secret rotates.

## 7. Quirks and surprises
- **Login status:** the login's outcome (`redirect` or `failure`) is in the JSON body, not in the HTTP status, and a failure's
  details are a devalue-encoded string rather than an object.
- **Numbers as strings:** geo and energy values are strings, but `capacityKva` and the export's `geo` are real numbers.
- **Four naming schemes for the same data:**
  - display labels (legacy page),
  - PascalCase (v2 page),
  - camelCase (list and export),
  - lowercase (export hierarchy).
  
  Location also appears as both `latitude/longitude` and `lat/lng`.
- **Two detail formats** depending on `build`; v2 is double-encoded JSON.
- **Combined hierarchy strings:** the meter page joins name and code into one string, `"Feeder 1 (F-001)"`. Code padding varies
  (`Z-01` vs `F-001`), the key `Sub Station` doesn't match the value "Substation", and phase is lowercase (`single`/`three`).
- **Case-sensitive meter IDs.**
- **Decommissioned meters** still report a full week of readings and voltage.
- **Intermittent `502 Bad Gateway`:** seen on a static JS file; it worked on reload.
- **`L&T`** contains `&`, which has to be URL-encoded in queries.
- **Serial prefixes don't match makes:** e.g. `L&84997` is a Genus meter. The prefixes look like make codes, but shuffled.
- **Two kinds of 404:** JSON for unknown meters, HTML for unknown routes.
- **Hover preloading** (`data-sveltekit-preload-data="hover"`): hovering over a link fetches its data, which shows up as noise in the Network tab.

## 8. Data quality
From the export (all 403 meters):
- **Blank values:**
  - 6 meters have no circle code; some others have a circle code but no name.
  - 3 have no substation code.
  - 5 have no feeder code; a few others have a feeder code but no name.
- **Conflicting names:** `DT-007` is "Sanganer DT 7" for 10 meters and "Old Malviya Nagar Xfmr" for 3.
- **Codes reused under different parents (54 cases):** `D-01` is under circles C-01 (20 meters), C-03 (10) and C-05 (9);
  `SD-01` is under D-01, D-05 and D-09; `F-001` is under SS-01 and SS-11. Either codes are only unique within their parent, or
  meters are wired inconsistently. The data alone can't tell which, so my API keeps every meter's path as reported and lists these
  cases instead of guessing.
- **Clean parts:** no duplicate IDs or serials; `dtCode` always equals `hierarchy.dt.code`; all coordinates are inside Jaipur.
- **Usage looks generated:** J100000 uses 20.29–20.30 kWh every full day, at a constant power factor of 0.926, with no daily load shape.

## 9. How I investigated
1. Opened DevTools before logging in (Preserve log on), then logged in and clicked through one meter, watching the requests:
   login, the `/portal/...` fetches and `__data.json`.
2. Used the Console with top-level `await` to check single things: counts, the first and last readings, error bodies,
   lowercase IDs, a second meter for comparison. That was quicker than pasting whole responses.
3. Read the page source (empty HTML, user in the layout data) and SvelteKit's route list (no hidden pages).
4. Searched every JS chunk for `/portal/`. That found `keys` and `export`. I read the code before calling either, to make sure they were
   plain GETs, then used the portal's own button.
5. Compared a `legacy` and a `v2` meter to find out what `build` changes.
6. Once the client worked, made a few read-only checks from Python. For example, three-phase meter J100005 reports only `voltR`.
7. Settled the last two questions in the browser: what an expired session returns (waited it out), and what a wrong password
   returns (one try).

**The AI assistant's part:** I did the browsing myself. Claude Code suggested each step and the Console snippets, and helped
me read what came back: the devalue format, the route list, the signing code. A claim stayed *inferred* or *not verified*
until a live response showed it. There's more in the README's [How I used AI](README.md#how-i-used-ai).

I treated the portal as read-only throughout: normal page use and GETs only, one request at a time.

## 10. Open questions
- With no cookie, the answer is a 401 (observed). Does a session that expired on the server, with the cookie still present, give the same 401?
- Do repeated failed logins lead to a lockout or a rate limit? (Deliberately not tested.)
- Is the `Origin` header required for login?
- How long does an export signature stay valid? Does the secret rotate, or is it tied to the session? What does `page=2` return?
- Is the data really static? (Everything seen so far ends on 30 June 2026.)
- What does `build` mean physically: firmware, or a data migration?
