# International sources for Iraq and Iran conflict — NewsAgent

Nine editorial desks are collected from public, low-volume RSS/index sources:

| Publisher | Collector | Scope |
| --- | --- | --- |
| The New York Times | Publisher-filtered Google News search; no NYT RSS excerpt retrieval | Iraq, Iran conflict |
| The Washington Post | Publisher-filtered Google News search; no WaPo RSS excerpt retrieval | Iraq, Iran conflict |
| Middle East Eye | Official RSS | Iraq, Iran conflict |
| Reuters | Publisher-attributed Google News search RSS | Iraq, Iran conflict |
| Associated Press (AP) | Publisher-attributed Google News search RSS | Iraq, Iran conflict |
| CNN | Publisher-attributed Google News search RSS | Iraq, Iran conflict |
| BBC Persian | Official Persian RSS | Iraq, Iran conflict |
| BBC Arabic | Official Arabic RSS | Iraq, Iran conflict |
| BBC English | Official Middle East RSS; publisher-filtered Google News fallback | Iraq, Iran conflict |

All sources are included in \`international_sources.py\` and called from
\`main.py\` on its usual five-minute schedule.

## Selection criteria

- Iraq: political/security/economic/public news about Iraq, Baghdad,
  Iraqi Kurdistan, Erbil, Kirkuk, Mosul or other named Iraqi locations.
- Iran: relevant *war, attacks, military/security, ceasefire, sanctions,
  nuclear negotiations and the conflict's direct regional effects*.
  An unrelated Iran sports/culture item is **not** relevant.
- Languages: English, Sorani/Persian and Arabic input; the existing NewsAgent
  Sorani quality gate applies before Telegram publication.
- Freshness: normally maximum 36 hours old.
- Intake: maximum four current matching items per desk and poll; no unbounded
  backlog of old headlines.
- Publisher verification: only news items linked to the expected publisher.
  Google News source attribution must match the configured publisher.
- Existing NewsAgent duplicate checks and priority/turn scheduling are reused.

## Sources and rights

This feature reads **public RSS metadata only** and Google News publisher
index headlines, with no full-text extraction, paywall bypass or use of
third-party article photographs. It retains the publisher credit, available
byline, original feed URL or Google News redirect link, and source time.

A public RSS feed is not a license to redistribute its text. For any
commercial republication, obtain rights/permission as required by the
publisher's terms. In particular, some publishers restrict RSS
redistribution. Write original summaries from verified facts, attribute
publishers, and link back to the story. A Google News indexed headline may
lack enough verified facts to safely produce a full article; those items
must not be elaborated with invented details.

## Monitoring and testing

No credentials, Apify credits or paid news APIs are required for this module.
Each collector has an HTTP timeout; an unavailable publisher does not
stop the others.

Run tests:

\`\`\`
python -m unittest -v test_reliability.py test_sorani_quality.py test_apify_keyword_filter.py test_international_sources.py
\`\`\`

A no-publication smoke workflow is at
\`.github/workflows/international_sources_smoke.yml\`, which reports the
number of fresh matching items for each publisher.

## Caveats

- Reuters/AP/CNN are indexed via Google News RSS because their old public
  RSS feeds are not reliably updated. Google News indexing may delay
  reporting or lack a usable summary.
- NYT and The Washington Post restrict automated reuse, translation, or commercial redistribution of RSS text. Their source monitoring uses indexed headlines only, not RSS excerpts. For use of protected article content, acquire appropriate rights.
- Absence of matching news in a single feed poll is not evidence that a
  publisher has no coverage.
- News items do not include unlicensed publisher images.
