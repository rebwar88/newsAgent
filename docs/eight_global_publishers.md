# Eight additional global publishers — Iraq and Iran-conflict news

Configured for the existing NewsAgent global/international collector.
**No Apify credits, extra server, Meta API or paid API are required for
public feed discovery.** Feed headlines do not count as complete articles.

## Publishers and source URLs

| Organization / language desk | Discovery source | Full-source reporting |
| --- | --- | --- |
| AFP | Publisher-attributed Google News RSS (`site:afp.com`) | **Discovery only**; AFP's licensed API/content feed required for dispatches |
| Al Jazeera English | `https://www.aljazeera.com/xml/rss/all.xml` | Open direct articles, robots-approved and substantive only |
| France 24 English | `https://www.france24.com/en/middle-east/rss` | Open direct articles, robots-approved and substantive only |
| France 24 Arabic | `https://www.france24.com/ar/middle-east/rss` | Open direct articles if they contain enough actual article evidence |
| Deutsche Welle (DW) | `https://rss.dw.com/xml/rss-en-world` | Open direct articles, robots-approved and substantive only |
| Bloomberg News | Publisher-attributed Google News RSS (`site:bloomberg.com`) | **Discovery only** pending permission/license |
| Al-Monitor | `https://www.al-monitor.com/rss` | Open direct articles, robots-approved and substantive only |
| Iran International English | `https://www.iranintl.com/en/feed` | Open direct articles, robots-approved and substantive only |
| Iran International Persian | `https://www.iranintl.com/feed` | Open direct articles, robots-approved and substantive only |
| Financial Times | Publisher-attributed Google News RSS (`site:ft.com`) | **Discovery only** pending permission/license |

Together these represent **eight additional news organizations across ten
language desks**, on top of the earlier nine desks. Both existing and new
feeds are restricted to Iraq and Iranian war/conflict/security/diplomacy
stories by `is_relevant`.

## Full-story quality requirements

RSS headlines are used to *discover* stories, not to fabricate long articles.
Only permitted, accessible original article paragraphs are supplied to the
Sorani writing model; the existing article evidence gate requires at least
three paragraphs, 125 words and 680 characters (maximum 17,000 chars).
The model must produce original, attributed Kurdish reporting, preserve
uncertainty, and never claim details absent from the verified source.
Live blog snippets, photo galleries, video-only pages, login/paywall
challenges, and pages that robots excludes are not full stories.

Copyright and publisher terms still apply: access does **not** authorize
verbatim re-publication, translation of protected expression, media reuse, or
bulk archiving. The bot publishes factual summaries based on the available
reporting rather than copying source paragraphs. License protected dispatches
before ingesting them, especially AFP/Bloomberg/FT.

## Cost and network controls

- Every eligible global feed source is checked at most every **10 minutes**.
- At most **10** directly accessible full article pages per eligible run.
- Those checks are shared across source desks, avoiding one noisy desk
  consuming all attempts.
- Unchanged short/unavailable article URLs are rechecked no sooner than
  30 minutes; cached queue items aren't re-scraped while awaiting publishing.
- Each URL is HTTPS checked, host-allowlisted, robots checked and response
  bounded. **No login, proxies, paywall bypass, unofficial AFP dispatch
  scraping or paid news APIs.**
- Normal NewsAgent deduplication, Sorani QA, editorial prioritization,
  citations and five-minute publication slots are unchanged.

## Tests

The `eight_publishers_probe.yml` and `publisher_html_probe.yml`
read-only GitHub Actions workflows test RSS reachability, robots policies,
actual article containers and source depth without publishing.
The core test suite checks feed coverage, publisher verification, freshness,
controlled page reads, cross-publisher fairness, and full-content gating.

Only `main` merge activates the new sources. This feature must not be
confused with activation of the independent Facebook Apify feature flag.
