# How the Urja Meter Ops portal works

This is what I found by exploring https://urja-ops.flockenergy.tech, logged in as `operator@urja.local`, on 4–5 Nov 2026.
Almost everything came from the browser (Chrome DevTools) and normal page use. Later, a few read-only checks were made from
Python. An AI assistant helped me plan the steps and read the results (see [§9](#9-how-i-investigated)).

**How to read the labels.** *Verified* means I saw it in a real response. *Inferred* means it is a sensible reading of what I
saw, but not proven. *Unverified* means I did not check. *SvelteKit convention* means it comes from how SvelteKit works, not from
something I saw on this portal. The counts in [§8](#8-data-quality) were *computed* with a script from the export I downloaded.
If a sentence has no label, it is something I saw directly.

The API in this repo is built on these findings. See the [README](README.md).

## Summary
- The portal is a **SvelteKit** app. Sessions use one cookie, valid for 1 hour. The cookie's name suggests the Better Auth
  library (*inferred*).
- Login is a form post. The result (`redirect` or `failure`) is in the JSON body, **not** in the HTTP status. Without a session
  cookie, `/portal/meters/search` answered `401` with JSON; I assume the other data endpoints do the same.
- The pages' HTML contains **no data**. The browser fetches JSON from **six endpoints under `/portal/`**, plus one page-data URL
  (`__data.json`).
- **There is a bulk path.** The *Export all meters* button calls an HMAC-signed endpoint, `/portal/export`. One response holds
  **all 403 meters** with hierarchy and location. The API uses it as its main source.
- **Readings** are **cumulative** registers (kWh, kVAh, voltage), one meter at a time. **Meters do not all report the same
  way:** J100000 reports every 30 minutes (337 rows), J100400 once a day (8 rows). I checked eight meters, not all 403.
  See [§4](#4-data-model).
- The data has **quirks** a consumer would trip over: numbers as strings, four naming styles, two record formats, blank and
  conflicting hierarchy values, and repeated rows. See [§7](#7-quirks-and-surprises) and [§8](#8-data-quality).

## 1. What it is built with
- **SvelteKit.** I recognised it from `/_app/immutable/...` files, the `x-sveltekit-action` header on form posts, `__data.json`
  page data, and the `kit.start(...)` bootstrap script.
- **Better Auth** (*inferred*), from the cookie name `__Secure-better-auth.session_token`.
- **Pages.** I read the route list in `/_app/immutable/entry/app.<hash>.js`. A negative number in that list means the page has a
  server-side `load` function (*SvelteKit convention*). There are no hidden pages.

| Route | Server load | What it shows, and where the data comes from |
|---|---|---|
| `/` | yes | empty page, presumably a redirect |
| `/login` | yes | login form (a SvelteKit form action) |
| `/meters` | no | meter table, from `GET /portal/meters/search` |
| `/meters/[id]` | yes | one meter, from `__data.json` plus `GET /portal/meters/{id}/geo` and `/energy` |
| `/transformers` | no | transformer table, from `GET /portal/dts`, plus the **Export all meters** button |

All logged-in pages share one layout that loads the user on the server. "Ops Desk" in the header is just that user's display name
(`{name: "Ops Desk", email: "operator@urja.local"}`). It is not a link.

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
- **Login reports its result in the JSON body, not in the HTTP status.** Success is the `"redirect"` above. A wrong password
  (tried once) gives this body:
  ```json
  {"type":"failure","status":401,"data":"[{\"email\":1,\"error\":2},\"operator@urja.local\",\"Invalid email or password.\"]"}
  ```
  `data` is a string in SvelteKit's "devalue" format, the same as `__data.json`. Decoded, it is
  `{email: "operator@urja.local", error: "Invalid email or password."}`. The form gets the email back, never the password, and the
  message does not say which of the two was wrong. I only captured the body, not the HTTP status. SvelteKit sends action results as
  HTTP 200 (the successful login showed this), so the failure is *presumably* a 200 too (*inferred*).
  My client treats `"failure"` as `portal_auth_failed` straight away, without retrying, and treats any answer that is not
  `"redirect"` as a failed login.
- **There is no CSRF token.** SvelteKit instead rejects form posts whose `Origin` does not match the site (*SvelteKit convention*).
  My client sends the right
  `Origin` and logging in from Python works. *Unverified:* whether `Origin` is really required.
- **Session lifetime: 1 hour.** No data response ever sent a new `Set-Cookie`, so the session does not seem to extend while in use
  (*inferred*, from a handful of requests). When `Max-Age` runs out, the browser deletes the cookie. `/portal/meters/search` then answered
  **`401` with JSON**: `{"error":"unauthorized","message":"A valid session is required."}` (seen about 1.5 hours after login, on
  that one endpoint; I assume the other data endpoints answer the same way).
  On a 401, my client logs in again once and retries. As a precaution it also treats a redirect, or HTML where JSON was expected,
  as "logged out", and it logs in again after 55 minutes, before the cookie expires.
- **Signing secret:** `GET /portal/keys` (cookie only) returns `{"data":{"signingSecret":"…"}}`, about 32 characters.
  See [§6](#6-the-bulk-path-signed-export).

## 3. Endpoints
I found these in the Network tab, then checked the list by searching every JavaScript file for `/portal/`. My client uses no others.

| Method | Path | Params | Returns |
|---|---|---|---|
| POST | `/login` | form `email`, `password` | JSON result: `redirect` plus the cookie, or `failure` (see [§2](#2-access-and-authentication)) |
| GET | `/portal/meters/search` | `q` (meter number or serial), `page` | `{data, total, page, pageSize}`, 20 per page |
| GET | `/portal/meters/{id}/geo` | none | `{"data":{"latitude":"26.93…","longitude":"75.83…"}}` (strings) |
| GET | `/portal/meters/{id}/energy` | none (no date range) | `{"data":[{timestamp, kwh, kvah, voltR}, …]}`. 337 rows for J100000 (every 30 min), 8–9 for the daily meters checked |
| GET | `/meters/{id}/__data.json` | `x-sveltekit-invalidated=001` | page data in devalue format: `meterId`, `detail`, `hierarchy` |
| GET | `/portal/dts` | `page` | `{data:[{code, name, feederCode, capacityKva}], total: 40, page, pageSize: 20}` |
| GET | `/portal/keys` | none | `{"data":{"signingSecret":"…"}}` |
| GET | `/portal/export` | `page`; headers `x-timestamp`, `x-signature` | `{"data":[…403 meters…], "total": 403}` |

```jsonc
// GET /portal/meters/search?q=&page=1   (one of 20 items)
{"meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single","installStatus":"Decommissioned","dtCode":"DT-001"}

// GET /portal/meters/J100000/energy   (one of J100000's 337 rows; J100400 has 8, one per day)
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
- No session cookie gives **401** with `{"error":"unauthorized","message":"A valid session is required."}` (seen on
  `/portal/meters/search`; assumed for the others).
- An unknown meter on a `/portal/` endpoint gives **404** with `{"error":"not_found","message":"Meter not found"}`.
- An unknown route gives **404 with an HTML page**.
- So a client must check `content-type` before parsing.

## 4. Data model
### Meters
- **403 meters**, IDs `J100000` and up (they look sequential). **IDs are case-sensitive:** `j100000` gives 404.
- **Status:** Installed 238 · Faulty 90 · Decommissioned 75.
- **Installation type:** Whole Current 205 · CT Operated 198.
- **Phase:** single 277 · three 126.
- **Make:** HPL 90 · Genus 88 · Secure 85 · Allied 76 · L&T 64.

### Two record formats (`build`)
The export has a field `build` that appears nowhere else: `legacy` (238 meters) or `v2` (165). It decides how the **meter page**
(`__data.json`) sends the details:
- **legacy:** `detail.data` is a list of `{parameterName, parameterValue}` pairs with display labels ("Serial No", "Installation Status").
- **v2:** `detail.classData` is a **JSON string inside the JSON** (double-encoded), with PascalCase keys:
  `"{\"installed_meter\":{\"MeterId\":\"J100004\",\"SerialNo\":\"SE65293\",\"Make\":\"Genus\",…}}"`.
- **Both:** the hierarchy part looks the same: `{"Zone":"Jaipur Zone 1 (Z-01)", …, "DT":"Malviya Nagar DT 1 (DT-001)"}`.
- `/energy` and `/geo` looked the same for both builds in the meters I compared.

`__data.json` uses SvelteKit's **devalue** format: one flat array where every number inside an object is a position in that array,
so a repeated value is stored once. The export carries the same information in one clean format for both builds.

### Network hierarchy
Zone → Circle → Division → Subdivision → Substation → Feeder → DT (distribution transformer) → Meter. Not counting blanks, there are
3 zones, 6 circles, 10 divisions, 14 subdivisions, 18 substations, 28 feeders and 40 DTs. Each meter carries its whole path.
The DT list adds each transformer's capacity in kVA (63, 100, 250, …). Every DT's `feederCode` agrees with what its meters report.

### Readings (`/energy`)
**Reading schedules.** Meters do **not** all report the same way. I checked eight meters by fetching `/energy` for each:

| Meter | Rows | First → last timestamp | Schedule |
|---|---|---|---|
| J100000 | 337 | `23/06/2026 23:30` → `30/06/2026 23:30` | every 30 minutes |
| J100001 | 337 | `23/06/2026 23:30` → `30/06/2026 23:30` | every 30 minutes |
| J100400 | 8 | `23/06/2026 00:00` → `30/06/2026 00:00` | once a day |
| J100089, J100134, J100195, J100211, J100330 | 9 each | `23/06/2026 00:00` → `30/06/2026 00:00` | once a day (8 days, and the last row appears twice, see below) |

- **Verified:** these eight meters, and the fact that at least two schedules exist.
- **Unverified:** the schedule of the other 395 meters. I did not fetch them all (that is 403 requests to a portal I treat as
  read-only), so I cannot say how many meters are on each schedule, or that there are only two.
- **A mistake worth knowing about:** my first version assumed every meter had 337 readings every 30 minutes, because the first two
  meters I looked at did. J100400 proved it wrong. I do **not** claim that all 403 meters follow any one schedule.
- **What the API does about it:** it reads each meter's normal step from that meter's own timestamps (the most common gap) and
  reports it as `reading_interval_minutes`. That works for any step. See [README](README.md#design-decisions-and-trade-offs).
- **Time window:** all eight meters cover `23/06/2026` to `30/06/2026` (verified for those eight, unverified for the rest). If that
  holds for the others, the data is a frozen snapshot about four months older than today.

**Duplicate timestamps.** *Verified* on five meters (J100089, J100134, J100195, J100211, J100330): `/energy` returns 9 rows where
the daily schedule would give 8, because the last timestamp, `30/06/2026 00:00`, appears twice. On all five, one of the two rows
has a blank `kwh`. On J100089 I looked at the whole row: `voltR` is blank too, `kvah` repeats unchanged, and the full row comes
first. (I did not compare the other four field by field.)
```json
{"timestamp":"30/06/2026 00:00","kwh":"12732.34","kvah":"13750.93","voltR":"229"}
{"timestamp":"30/06/2026 00:00","kwh":"","kvah":"13750.93","voltR":""}
```
- *Unverified:* whether the full row always comes first, whether other meters have duplicates, and why the portal sends them.
- **How the API handles it:** the row with more non-blank values wins. On a tie, the later row wins. A whole row is chosen and rows
  are never mixed, so every reading the API returns is one the portal really sent. These meters therefore give 8 readings with the
  real `kwh`. (My first version kept the later row, which threw the real reading away.)

**Values.**
- **`kwh` and `kvah` are cumulative registers**, like a car's odometer. **Usage is the difference between readings**, so n readings
  give **n − 1 intervals** (337 give 336, 8 give 7). *Verified* for the four meters whose usage I computed (J100000, J100400,
  J100089, J100134): no register went down. *Unverified* for the rest.
- **kWh ÷ kVAh** is about 0.93 on J100000 and J100400 (0.926–0.933 on the intervals I looked at). That ratio is the power factor.
- **`voltR`** is the R-phase voltage (220–240 V in what I saw). Even a three-phase meter (J100005) reports only `voltR`.
- **Timestamps** look like `DD/MM/YYYY HH:MM`: **day first, no timezone**. Day-first is *verified* (`30/06/2026` cannot be
  month-first). I read them as **IST (+05:30)** because the utility is in Jaipur (*inferred*; the portal never says).
- **Every value is a string**, and trailing zeros are dropped (`"48440"`).

## 5. Paging and search
- `/portal/meters/search?q=&page=N` returns 20 per page. There are 403 meters, so 21 pages. `q` matches the meter number or serial,
  according to the search box's placeholder text.
  There is no other filtering or sorting.
- `/portal/dts?page=N` returns 20 per page. There are 40, so 2 pages.
- `/portal/export` returned all 403 meters on page 1, with no `page` or `pageSize` in the response. My client still asks for pages
  until it has `total` records.

## 6. The bulk path: signed export
**How I found it.** The route list showed no hidden pages, so I fetched every JavaScript file and searched them for `/portal/`. The
Transformers page code mentions `/portal/keys` and `/portal/export`, and neither is called when the page loads. The *Export all
meters* button does this:
```
secret    = GET /portal/keys → data.signingSecret
timestamp = floor(Date.now() / 1000)                                   (Unix seconds)
message   = "GET" + "\n" + "/portal/export" + "\n" + "page=1" + "\n" + timestamp
signature = hex(HMAC-SHA256(key = secret, message))                    (browser WebCrypto)
GET /portal/export?page=1   x-timestamp: <timestamp>   x-signature: <signature>
```
- **A captured example:** `x-timestamp: 1793799552` (13:39:12 UTC; the server's `Date` header said 13:39:11, so the clocks agree)
  and a 64-character hex signature. The response was 231,658 bytes and took 386 ms.
- **The signature covers the method, path, exact query string and time.** So each page needs its own signature, and the query string
  must match character for character. The server presumably rejects old timestamps. *Unverified:* how long a timestamp stays
  valid, so my client signs every page and every retry afresh rather than reusing a signature.
- **Why it matters:** the export replaces about **827 requests** (21 list pages, 403 meter pages, 403 geo calls) with **2**, and it
  avoids decoding the meter pages (devalue plus two detail formats).
- **It does not include readings.** Those still take one `/energy` call per meter, and the schedule differs between meters.
- The UI only ever asks for `page=1`. *Unverified:* what page 2 returns, and whether the secret changes over time.

## 7. Quirks and surprises
- **Login status:** the result (`redirect` or `failure`) is in the JSON body, not the HTTP status, and a failure's details are a
  devalue-encoded string, not an object.
- **Numbers as strings:** geo and energy values are strings, but `capacityKva` and the export's `geo` are real numbers.
- **Four naming styles for the same data:**
  - display labels (legacy page),
  - PascalCase (v2 page),
  - camelCase (list and export),
  - lowercase (export hierarchy).

  Location also appears both as `latitude/longitude` and as `lat/lng`.
- **Two detail formats** depending on `build`; v2 is double-encoded JSON.
- **Combined hierarchy strings:** the meter page joins name and code into one string, `"Feeder 1 (F-001)"`. Code padding varies
  (`Z-01` vs `F-001`), the key `Sub Station` does not match the word "Substation", and phase is lowercase (`single`/`three`).
- **Case-sensitive meter IDs.**
- **Repeated last row on some meters** (see [§4](#readings-energy)).
- **Decommissioned meters still report readings:** J100000 is `Decommissioned` and has a full 337 rows (*verified* for that meter only).
- **Intermittent `502 Bad Gateway`:** seen once on a static JS file; it worked on reload.
- **Rate limiting:** *not observed by me*. I kept to one request at a time. The client still treats `429` as retryable (using
  `Retry-After` if present) and passes a persistent one on as `429`, in case the portal limits bursts.
- **`L&T`** contains `&`, which must be URL-encoded in queries.
- **Serial prefixes do not match makes:** for example `L&84997` is a Genus meter.
- **Two kinds of 404:** JSON for an unknown meter, HTML for an unknown route.
- **Hover preloading** (`data-sveltekit-preload-data="hover"`): hovering over a link fetches its data, which adds noise to the Network tab.

## 8. Data quality
From the downloaded export (all 403 meters), *computed* with a script:
- **Blank values:**
  - 6 meters have no circle code, and some others have a circle code but no name.
  - 3 have no substation code.
  - 5 have no feeder code, and a few others have a feeder code but no name.
- **Conflicting names:** `DT-007` is "Sanganer DT 7" for 10 meters and "Old Malviya Nagar Xfmr" for 3.
- **Codes reused under different parents (54 cases):** `D-01` is under circles C-01 (20 meters), C-03 (10) and C-05 (9);
  `SD-01` is under D-01, D-05 and D-09; `F-001` is under SS-01 and SS-11. By level, that is every division, subdivision and
  substation code, and 12 of the 28 feeder codes. But every one of the 40 DTs has exactly one upstream path. So this looks like
  **codes that are only unique inside their parent** (*inferred*), not meters wired inconsistently. My API keys the tree by full
  path, so each of these is a separate node, and it lists the reused codes in `issues`, because a consumer filtering by code alone
  would otherwise mix branches.
- **Clean parts:** no duplicate IDs or serials; `dtCode` always equals `hierarchy.dt.code`; all coordinates are inside Jaipur.
- **Usage looks generated** (J100000 only, *inferred*): it uses 20.29–20.30 kWh on every full day, at a constant power factor of
  0.926, with no daily load shape.

## 9. How I investigated
Here is the order I worked in, including the dead ends.
1. **Opened DevTools before logging in** (Preserve log on). Logged in, clicked through one meter, and watched the requests: login,
   the `/portal/...` calls and `__data.json`.
2. **Used the Console** with top-level `await` to check single things: counts, first and last readings, error bodies, a lowercase
   ID, a second meter for comparison. This was quicker than pasting whole responses.
3. **Read the page source** (empty HTML, user in the layout data) and SvelteKit's route list. *Dead end:* I hoped to find hidden pages.
   There were none.
4. **Searched every JavaScript file for `/portal/`.** This found `keys` and `export`. I read the code before calling either one, to
   check they were plain GETs, then used the portal's own button.
5. **Compared a `legacy` and a `v2` meter** to see what `build` changes.
6. **Once the client worked**, Claude ran a few read-only checks from Python at my request (for example, three-phase meter J100005
   reports only `voltR`).
7. **Settled two questions in the browser:** what an expired session returns (I waited it out), and what a wrong password returns
   (one try).
8. **Later, in a review of my own code**, I fetched readings for more meters and found that they do not all follow the first two
   I had looked at (different schedule, repeated last rows). That is why [§4](#readings-energy) has labels and a table.

**The AI assistant's part:** I did the browsing myself. Claude Code suggested the next step and the Console snippets, and helped me
read what came back: the devalue format, the route list, the signing code. The later review and fixes were also done with Claude
Code. A claim stayed *inferred* or *unverified* until a live response showed otherwise. More in the README's
[How I used AI](README.md#how-i-used-ai).

I treated the portal as read-only throughout: normal page use and GET requests only, one at a time.

## 10. Open questions
- A request with no cookie gets a 401 (seen). Does a session that expired on the server, with the cookie still present, give the same 401?
- Do repeated failed logins cause a lockout or a rate limit? (Deliberately not tested.)
- Is the `Origin` header required for login?
- How long does an export signature stay valid? Does the secret change, or is it tied to the session? What does `page=2` return?
- How many of the 403 meters report every 30 minutes, and how many once a day? Are there other schedules?
- Why does the portal repeat the last row on some meters, and does it happen elsewhere?
