# Reflection

This is the honest story of the assignment: what I assumed, where I got stuck, what I got wrong, and what I would still
criticise. The mistakes are the most useful part, so I have not hidden them.

## The journey in short

1. **Looked at the portal like a normal user.** I logged in with DevTools open and watched the Network tab. The pages had no
   data in their HTML. The data came as JSON from a few `/portal/...` URLs, so I would not need to scrape anything.
2. **Hit a wall.** The list gives 20 meters per page, and each meter's details come in one of two odd formats. Building all the
   data from those pages would take about 827 requests and three different parsers. I checked SvelteKit's route list for hidden
   pages. There were none. That was a dead end.
3. **Found the way out.** I searched every JavaScript file for `/portal/`. Two URLs showed up that the pages never call by
   themselves: `/portal/keys` and `/portal/export`. The *Export all meters* button uses them to download all 403 meters in one
   request, with a signature on it.
4. **Worked out the signature.** I read the portal's own code, copied the recipe into Python, and checked it against Node.
   The live portal accepted it.
5. **Built the API** (FastAPI): login and session handling, retries, cache, filters, location search and the network tree.
6. **Wrote the docs**, and thought the work was nearly done.
7. **A later review found two real bugs** in how I handled readings (see "mistakes" below). I checked them against the live
   portal, fixed them, and corrected the docs that had been wrong.

## What assumptions did you make?
- **Timestamps are IST.** The portal sends `23/06/2026 23:30` with no timezone. I read it as IST (+05:30) because the utility is in
  Jaipur and India has no daylight saving. If that is wrong, every daily total moves by five and a half hours. Every time the API
  returns has its offset written out, so at least the assumption is visible.
- **The signed export is the source of truth for meter details.** I compared it with the list and meter pages for one meter of
  each format (J100000 `legacy`, J100004 `v2`), not for all 403.
- **The data is a static snapshot.** The eight meters whose readings I fetched all cover 23–30 June 2026, so I treated the cache
  times as a way to protect the portal, not to keep up with new data. I did not check the other 395 meters.
- **Hierarchy codes are not unique everywhere.** I deliberately did not assume they are. 54 codes appear under more than one
  parent, so the API keeps each meter's path as reported and lists the problems.
- **Smaller ones:**
  - Meter IDs can be accepted in any case, because no two differ only by case.
  - Odd data, such as readings from decommissioned meters, is passed through, not hidden.
  - A falling register is flagged, not treated as a meter reset.
  - Every data endpoint answers an expired session the same way. I only checked one of them.
- **An assumption that turned out wrong:** that every meter reports every 30 minutes. More on that below.

## Which part was the most difficult, and how did you get unstuck?
**Finding a bulk way to get the data.** The visible pages are slow and awkward. Twenty meters per page, then one request per meter
for details, and two different formats for those details (a list of label/value pairs for `legacy` meters, and a JSON string hidden
inside the page data for `v2` meters). I was not even sure a better route existed.

My first idea was to look for hidden pages in SvelteKit's route list. That found nothing.

What worked was searching all the loaded JavaScript for `/portal/`. The Transformers page's code mentioned two URLs that nothing
calls when the page loads: `/portal/keys` and `/portal/export`. I read that code before calling either one. It signs the export
request with HMAC-SHA256, using a secret that comes from `/portal/keys`. I clicked the portal's own export button to see what it
sent, then rebuilt the same request in Python. One response gave me all 403 meters with their full hierarchy and location.

The lesson: when the screen runs out of answers, the page's own code is the documentation.

**The second hard part** was the messy hierarchy. The same code (for example `D-01`) shows up under several parents. At first I
thought the data couldn't say whether codes are only unique inside a parent or the data is simply wrong. A closer look settled
it: every transformer has exactly one upstream path, so the codes are reused per parent. The API keeps each meter's path exactly
as reported and lists every reused code under `issues`.

## What mistakes did you make while solving this?
In the order they hurt:

1. **I assumed every meter reports every 30 minutes.** I looked at two meters, both did, and I wrote the code and the docs as if all
   403 were the same. They are not. J100400 reports once a day. My code then labelled 24-hour intervals as `30m` and flagged every
   one of them as a `gap`. My PROTOCOL.md even said "337 readings for every meter", which was wrong.
   *Fix:* the API now reads each meter's normal step from its own timestamps and reports the real `duration_minutes`.
2. **My parser threw away good data.** For five meters (J100089, J100134, J100195, J100211, J100330) the portal sends the last row
   twice, and the second copy has a blank `kwh`. My code kept "the last row", so the blank one won and the real reading was lost.
   I had written "duplicates keep the last row" as a guess about the portal, and never checked it.
   *Fix:* the row with more values wins (the later row on a tie), and rows are never mixed.
3. **My tests could not catch either bug.** They run against a fake portal that I built from my own understanding, and my fixtures
   had no once-a-day meter and no duplicate row. So the code and the tests were wrong in the same way, and everything was green.
   A separate review, with real data from the live portal, found both bugs. *Fix:* I added fixtures made from real responses
   (J100400, J100089) and tests for both cases.
4. **My docs said more than I had checked.** Examples: "every meter has 337 readings", and "decommissioned meters report a full
   week", which I had seen on one meter only. PROTOCOL.md now labels each claim as verified, inferred or unverified, and says plainly
   that I checked eight meters, not 403.
5. **I ran code I had not read, on someone else's system.** I pasted a snippet from my AI assistant into the browser console
   without reading it. It was pseudo-code, so it failed harmlessly (`fetch(...).json is not a function`). But I was logged in to a
   production system. After that I read every snippet before running it, and I did the same when I read the export code before
   calling `/portal/export`.
6. **I tested session expiry too early.** I tried about 40 minutes after logging in, before the one-hour cookie could expire, and
   got normal data back. I should have worked out the expiry time first. I re-ran it later and got the real answer: a `401` with
   JSON.
7. **I typed `j100000` in lowercase by accident.** This one helped. It showed me that the portal's IDs are case-sensitive and what
   its 404 looks like. Both went straight into the design.

## If you had another day, what would you improve?
In order of risk:
1. **Check all 403 meters' readings, slowly.** Right now the schedule claims cover eight meters. One gentle pass (about one request
   per second) would turn "unverified" into a real count.
2. **Settle the open protocol questions** in [PROTOCOL.md §10](PROTOCOL.md#10-open-questions): how long a signature stays valid,
   whether the secret changes, what `page=2` returns, and whether the `Origin` header is really required. Each takes a few minutes
   in the browser, and each would replace a defensive guess in the client with known behaviour.
3. **Add contract tests against the live portal**, run on a schedule, so a change in the portal is caught before users notice. This
   is the direct answer to mistake 3.
4. **Build the fallback** to the meter pages (`__data.json`, both formats), so meter details survive if the export is withdrawn.
5. **Move refreshes into a background job with a small database**, so requests never wait on the portal.
6. **Build a small web client:** a map of meters by status, consumption charts, and a hierarchy view that shows the data issues.

## If you were reviewing your own submission, what would you criticise?
- **Most of the code was written with an AI assistant.** I did the portal investigation, made the scope decisions and checked the
  results, but my understanding of the code comes from reading and testing it, not from typing it. The README says this plainly.
- **The tests check the code against my own understanding of the portal.** The fake portal copies the protocol as I understood it,
  so a misunderstanding makes the code and the tests wrong together (this already happened once, see mistake 3). There is still no
  live contract test in the repo.
- **It rests on an undocumented endpoint.** The export sits behind one button and has its own signing scheme. If it changes, meter
  details go stale. The fallback is described, not built.
- **Some things are still guesses:** the signature time window, `page=2`, whether the secret changes, and the reading schedule of
  395 meters. The client is written defensively around them, but they are not proven.
- **The reading step is a heuristic.** The API uses the most common gap between readings. A meter with very irregular readings
  could get an odd step.
- **It is one process with an in-memory cache.** Several workers would mean several caches and several portal logins, and a
  restart downloads everything again. That is fine for 403 meters, but it is a limit.
- **The docs are long.** A reviewer with ten minutes might want a shorter README, with the details moved into separate files.
