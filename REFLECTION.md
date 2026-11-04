# Reflection

## What assumptions did you make?
- **Timestamps are IST.** The portal sends `23/06/2026 23:30` with no timezone. I read it as IST (+05:30) because the utility is in
  Jaipur, and India has no daylight saving to worry about. If that's wrong, every daily total shifts by five and a half hours.
  That's why every time the API returns carries its offset explicitly, so the assumption is at least visible.
- **The signed export is the source of truth for meter metadata.** I compared it with the list and meter pages for one meter of
  each format (J100000 `legacy`, J100004 `v2`), not for all 403.
- **The data is a static snapshot.** Every meter's readings cover the same week, ending 30 June 2026, so I treated the cache TTLs
  as a way to protect the portal rather than to keep up with new data. If the portal went live, those TTLs would become the
  main freshness setting.
- **Hierarchy codes are not globally unique.** I deliberately avoided this assumption: 54 codes appear under more than one parent,
  so the API keys the tree by path and reports those cases instead of guessing which parent is right.
- **Smaller ones:**
  - Meter IDs can be accepted in any case, because no two differ only by case.
  - Odd data, such as readings from decommissioned meters, is passed through, not hidden.
  - A falling register is flagged rather than treated as a meter reset.
  - Every data endpoint answers an expired session the same way. I only checked one of them.

## Which part was the most difficult, and how did you get unstuck?
Finding a bulk path. The visible pages give 20 meters per list page. Each meter's details come in one of two formats: a list of
label/value pairs for `legacy` meters, or a JSON string nested inside the page data for `v2` meters. Building the metadata
that way would take about 827 requests and three parsers, and I wasn't sure a better route existed. My first idea, looking for
hidden pages in SvelteKit's route list, was a dead end: there weren't any.

What got me unstuck was searching every JavaScript chunk for `/portal/`. The Transformers page's code mentioned two endpoints that
are never called when the page loads: `/portal/keys` and `/portal/export`. I read that code before calling either of them. It
signs each export request with HMAC-SHA256 over the method, path, query and a Unix timestamp, using a secret from `/portal/keys`.
Instead of copying the request by hand, I clicked the portal's own *Export all meters* button and captured what it sent. That one
response held all 403 meters with their hierarchy and location. The Python signing was then checked against Node's WebCrypto
(the API the portal's code uses), and the live export accepted it. The lesson I'm taking away: when the UI runs out, the client
code is the documentation.

## If you had another day, what would you improve?
In order of risk:
1. **Settle the open protocol questions** in [PROTOCOL.md §10](PROTOCOL.md#10-open-questions): how long a signature stays valid,
   whether the secret rotates, what `page=2` returns, and whether the `Origin` header is required. Each takes a few minutes in the
   browser, and each would let me replace a defensive guess in the client with known behaviour.
2. **Build the fallback** to the meter pages (`__data.json`, both formats), so metadata survives if the export is withdrawn. Also a
   scheduled check that the export still matches those pages.
3. **Move refreshes into a background sync with a small database**, so requests never wait on the portal and questions across
   meters don't need one portal call per meter.
4. **Add contract tests against the live portal**, run on a schedule, so a protocol change is caught before users notice.
5. **Build a small web client:** a map of meters by status, consumption charts, and a hierarchy view that highlights the data issues.

## What mistake did you make while solving this?
The one I learned most from: I pasted a snippet from my AI assistant into the browser console without reading it. It was
pseudo-code, not runnable JavaScript, so it failed harmlessly (`fetch(...).json is not a function`). But I was logged in to
someone else's production system, and running code I hadn't read is exactly what I shouldn't do there. After that I read every
snippet before running it. I used the same rule a little later, when I read the export code before calling `/portal/export`.

Two smaller ones:
- **Testing session expiry too early.** I ran the expiry test about 40 minutes after logging in, before the one-hour cookie could
  have expired, and got normal data back. I should have worked out the expiry time first. I re-ran it later and got the real answer:
  a `401` with JSON.
- **Typing `j100000` in lowercase.** That one turned out useful: it showed the portal's IDs are case-sensitive, and what its 404
  looks like. Both went straight into the design.

## If you were reviewing your own submission, what would you criticise?
- **Most of the code was written by an AI assistant.** I directed the work, did the portal investigation, and made the scope calls,
  but my understanding of the code comes from reading it, not from writing it. The README says this plainly, and a reviewer should
  weigh it.
- **The tests check the code against my own understanding of the portal.** The fake portal copies the protocol as I understood it.
  If I misunderstood something, the code and the tests are wrong together. There's no live contract test in the repo.
- **It rests on an undocumented endpoint.** The export sits behind one button and its own signing scheme. If it changes, metadata
  goes stale, and the fallback is documented but not built.
- **Some protocol points are still guesses:** the signature window, page 2 and secret rotation. The client is written defensively
  around them, but they're not proven.
- **It's one process with an in-memory cache.** Running several workers would mean several caches and several portal sessions,
  and a restart re-downloads everything. That's fine at 403 meters, but it's a limit.
- **The docs are long.** A reviewer with ten minutes might want a shorter README, with the detail moved into separate files.
