# Apify keyword pre-filter (NewsAgent)

This opt-in implementation does **not** activate automatically. The existing
Facebook scraper stays in use until the NewsAgent owner explicitly enables the
feature for a low-cost live test.

## Actor and billing

- Actor: `topapi/facebook-page-posts-scraper`
- Inputs: `startUrls`, `keywordFilter` (comma-separated alternatives),
  `monitorMode`, `startDate`, `maxPostsPerPage`, `maxItems`.
- Advertised price: **$1.20 per 1,000 returned posts**, no start fee
  (https://apify.com/topapi/facebook-page-posts-scraper). Confirm pricing and
  billing before paying for a production run.
- This is **filtering inside the Actor**, not native Facebook search.
  Filtering may miss significant posts without one of the 16 selected phrases.

## Activation

After reviewing the test results and the Apify credit balance, visit
GitHub > Settings > Secrets and variables > Actions > Variables.

Set `APIFY_FILTER_ENABLED=true` only when ready to run the new Actor.
Optional variables (if blank, defaults below apply):
- `FACEBOOK_PAGE_URLS`: comma-separated Facebook Page links, default 5
- `APIFY_PAGE_BATCH_SIZE=10`: how many Pages per Apify call
- `APIFY_MAX_ITEMS_PER_RUN=20`: charged-result cap per call
- `APIFY_MAX_CHARGE_USD=0.05`: maximum spend per call, NOT per month
- `APIFY_INTERVAL_MINUTES=120`: minutes between calls

The same private GitHub Secret `APIFY_TOKEN` is used. Never place it in
repository code or documents. The 16 newsroom phrases are stored in
`facebook_news_keywords.json`; modify that file to change matches.

For 50 Pages, requests rotate 10 Pages at a time, advancing the cursor only
after a valid response. The Actor monitors new posts to reduce duplicates.
No post-details add-on is requested.

**Safety:** per-run caps do not set a monthly budget. An enabled feature can
spend credit on every eligible run. For free-tier accounts, verify real billing
and Page coverage before reducing the interval. No token rotation or multiple
account workaround is included.

## Testing (no real charges)

```bash
python -m unittest -v test_reliability.py test_sorani_quality.py test_apify_keyword_filter.py
```

All new tests simulate the API and do not connect to Apify. Real scrape results,
keywords' recall and charges still require a separately authorized live test.
