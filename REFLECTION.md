# Reflection

## What assumptions did I make?

- **All timestamps are in Indian Standard Time (IST).** The portal returns timestamps like `23/06/2026 23:30` without specifying a timezone. Since the utility is based in Jaipur, I assumed these timestamps are in IST (+05:30). If this assumption is incorrect, all daily totals could shift by 5 hours and 30 minutes. To make this transparent, the API includes the timezone offset whenever it returns a timestamp.

- **The bulk export is reliable for meter details.** I verified the bulk export against the regular portal pages for two meters: J100000 (legacy format) and J100004 (v2 format). Both matched, so I treated the bulk export as the primary source for meter information.

- **The data represents a fixed snapshot.** All 403 meters have readings covering the same period: 23–30 June 2026 (I surveyed every meter at the end to confirm this). Based on this, I treated caching as a way to reduce unnecessary requests to the portal rather than a mechanism for keeping up with incoming data.

- **A session expiry returns the same error across endpoints.** I observed a `401` response on only one endpoint, `/portal/meters/search`. I assumed other endpoints behave similarly, although this has not been independently verified.

- **Meter IDs can be handled without considering letter case.** The portal treats meter IDs as case-sensitive, but none of the IDs I encountered differed only by uppercase or lowercase letters. So, I assumed this would not cause any conflicts in the current dataset.

- **Unusual data is preserved rather than silently filtered out.** For example, meter J100000 is marked as decommissioned but still contains a complete week of readings. I chose to retain such records instead of removing them automatically.

- **Decreasing meter readings are flagged, not corrected.** If a reading is lower than the previous one, I flag it as an anomaly rather than assuming the meter was reset or changing the value. This avoids making corrections without sufficient evidence.

## Which part was the most difficult, and how did I get unstuck?

The biggest challenge was figuring out how to retrieve all the meter data efficiently. The portal displays only 20 meters per page, and individual meter details come in two different formats. Fetching everything through the normal pages would have required around 827 requests and three separate parsers.

Initially, I tried finding hidden pages through SvelteKit's route list, but that didn't lead anywhere. Claude then suggested searching the portal's JavaScript files for `/portal/` to identify API endpoints that the frontend might not be using directly.

That search revealed two interesting endpoints: `/portal/keys` and `/portal/export`. Both were referenced in the Transformers page code. Looking through that code, I discovered they were actually used by an *Export all meters* button. I had visited the page before but had overlooked this option.

The export endpoint required an HMAC-SHA256 signature, generated using a secret retrieved from `/portal/keys`. Before making any direct API calls, I reviewed the relevant code, clicked the actual export button, and captured its request.

From there, Claude helped recreate the signature-generation logic in Python. We compared its output with the same function in Node.js to make sure both implementations matched. Once everything was verified, the live portal accepted the request.

The breakthrough was that a single export response provided all 403 meters, including their complete hierarchy and location details. This eliminated the need to make hundreds of individual requests and handle multiple data formats separately.

## If I had another day, what would I improve?

There are a few things I'd like to work on if I had one more day:

1. **Verify a few things about the portal.** There are still some unanswered questions, such as how long the signature remains valid, whether the secret changes, what happens when requesting `page=2` from the export endpoint, and whether the `Origin` header is actually required. I'd spend some time testing these in the browser rather than making assumptions.

2. **Add tests against the live portal.** My current tests don't check whether the actual portal is still behaving as expected. I'd add scheduled tests to catch changes in the portal's endpoints or response formats before they cause problems.

3. **Keep a fallback for data extraction.** Right now, the solution depends on the bulk export endpoint. I'd also build a backup method using the normal meter pages, so the system can still retrieve data if the export feature stops working.

4. **Move data refreshing to the background.** Instead of making users wait for the portal to respond, I'd refresh the data in the background and store it in a small database. That would make the application faster and more reliable.

5. **Build a simple dashboard.** I'd like to add a web page showing meters on a map, grouped by their status, along with consumption charts. This would make the collected data much easier to explore and understand.

## What mistake did I make while solving this?

The biggest mistake I made was **assuming that every meter sends readings every 30 minutes.** The first two meters I checked, J100000 and J100001, both had 337 readings at 30-minute intervals. I assumed the same pattern would apply to all 403 meters, but that turned out to be wrong. Some meters, like J100400, report only once a day. When I later surveyed all 403 meters, 363 turned out to be daily, and the 40 half-hourly ones are exactly the first 40 IDs (J100000–J100039). Every meter I had looked at early on came from that group, so my sample was biased from the start.

Because of this, my code started flagging daily readings as missing data. I also documented the assumption in `PROTOCOL.md`, stating that every meter had 337 readings, which was incorrect.

While reviewing the data, I found another issue. For five meters, the portal was returning the last reading twice, with the duplicate having a blank `kwh` value. My parser was keeping the blank row and accidentally discarding the actual reading.

The frustrating part was that **all my tests were passing despite these mistakes.** The reason was that I had built a fake portal based on my own understanding of how the real portal worked. Since both the code and the tests were based on the same assumptions, the tests couldn't catch the problems.

I only discovered these issues when I went back and fetched readings from more meters with Claude. It taught me an important lesson: checking two examples isn't enough to establish a pattern, and tests are only as reliable as the data and assumptions behind them.

There were a few smaller mistakes too:

- I pasted a code snippet into the browser console without properly reading it first. It was only pseudocode and failed harmlessly, but I should have checked it before running anything in a real system.
- I tested session expiry around 40 minutes after logging in, even though the session was valid for an hour. So the test didn't actually verify an expired session.
- I entered `j100000` in lowercase. That resulted in a 404, which helped me discover that meter IDs are case-sensitive and understand how the portal responds to invalid IDs.

## If I were reviewing my own submission, what would I criticise?

- **I relied quite heavily on AI for writing the code.** I handled the browser investigation and made the scope decisions about what to build and what to skip. However, most of the actual code was written with the help of an AI assistant. I understand how it works by reading it and testing it, but I didn't write every part myself.

- **My tests aren't independent enough.** Most of them are based on my own understanding of how the portal works. That's exactly why the reading-schedule issue went unnoticed. I'd also like to have tests that check the actual live portal, which I haven't implemented yet.

- **The solution depends on an endpoint that isn't officially documented.** The bulk export works right now, but if the portal changes its export API or signing mechanism, the meter details could stop updating. I haven't built a proper fallback for that yet.

- **There are still a few things I haven't fully verified.** For example, how long the signature stays valid, what `page=2` returns, and whether the secret changes over time.

- **The reading-frequency calculation could be more reliable.** Currently, I use the most common time gap between readings to estimate a meter's reporting interval. This works for regular patterns, but meters with irregular readings might produce misleading results.

- **The application is currently designed as a single process with an in-memory cache.** That's perfectly reasonable for 403 meters, but if the application needs to run across multiple workers, each worker would have its own cache and login session. I'd need to rethink that part.

- **The documentation could be shorter.** I've documented things in quite a lot of detail, but someone reviewing the project in a hurry might prefer a shorter README that explains the setup, approach, and important decisions without having to go through everything.
