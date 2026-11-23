# International reporting must have complete source evidence

## Why this change exists

The first international feed implementation used at most **750 characters**
of RSS description or a Google News headline. AI then treated that snippet
as sufficient to prepare a news report. This can omit essential facts or
encourage unsupported expansion.

A direct read-only check on the user-reported Middle East Eye Live Update
(`report-netanyahu-calls-trump-launch-attack-iran-soon-possible`) found
**two paragraphs totalling ~273 visible characters in the article element**.
The underlying source itself is a *brief live-blog update*, not a long report.

## Updated editorial flow

1. RSS / Google News is used only for discovering potentially relevant news.
2. The collector attempts to read a public **article page** only from a
   publisher for which automated access has been evaluated and configured.
3. The page must pass robots policy, HTTPS/host checks, content-type,
   response-size checks, and the absence of redirects/paywalls.
4. Paragraph evidence is extracted from the publisher article, not sidebars
   or related-story headlines.
5. A full story must have >= 3 paragraphs, >= 125 whitespace-delimited words
   and >= 680 characters. Sources beyond the input-size budget are rejected,
   **not silently truncated**.
6. Without this verified article evidence, NewsAgent does not publish the
   international item as a complete report. Previously queued incomplete
   international entries are purged.
7. With source evidence, Gemini/OpenRouter writes an **original** Sorani
   factual report (normally 3–5 paragraphs), attributing claims correctly
   and retaining the original publisher/byline/story URL. It must not
   copy the publisher's article wholesale or make up missing facts.
8. Rejected brief updates are not repeatedly downloaded every five minutes:
   a 30-minute check cooldown is persisted to publication state.

## Current publisher coverage

| Publisher | Discovery | Direct complete-body reference |
| --- | --- | --- |
| Middle East Eye | Official RSS | **Enabled** for openly accessible article pages when robots permits |
| New York Times | Publisher-verified Google News index | Not enabled without authorized access/licence |
| Washington Post | Publisher-verified Google News index | Not enabled without authorized access/licence |
| Reuters | Publisher-verified Google News index | Not enabled without authorized access/licence |
| Associated Press | Publisher-verified Google News index | Not enabled without authorized access/licence |
| CNN | Publisher-verified Google News index | Not enabled without authorized access/licence |
| BBC Persian / Arabic / English | Official public feeds and publisher index | Not enabled without authorized access/licence |

The non-enabled sources are still *monitored for headlines and topics*, but
will not silently publish short RSS snippets as if they were full stories.
An API or syndication licence that allows full-article reading and downstream
original reporting is required to enable direct content acquisition for
restrictive publishers. Publisher access and redistribution are different
rights; even for publicly readable pages, copyright and terms still apply.
Do **not** bypass login, paywalls or robots exclusions.

## Live validation

The no-publish `full_article_validation.yml` GitHub Actions probe inspected
four latest matching Middle East Eye feed entries. Two had substantive
original articles (237 and 220 words captured); two were short updates and
were refused. The example short live-blog page was not misclassified as
a full article.

Check Github Actions and Telegram on the next production runs before
claiming a particular fresh story was successfully published.

This change does not affect Telegram/Facebook collectors or the Apify token.
It introduces no new paid service.
