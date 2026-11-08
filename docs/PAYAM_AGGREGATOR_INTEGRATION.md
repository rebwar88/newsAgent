# PayamNewsAggregator test ingestion

This is a temporary one-way ingestion handoff. It sends original collected source items to the newsroom panel; it does not send the agent's Kurdish prepared text and does not replace the existing Telegram or WordPress archive flows.

## GitHub Actions secrets

Configure these repository Actions secrets in `rebwar88/newsAgent`:

- `PAYAM_AGGREGATOR_INGEST_URL`: the panel HTTPS endpoint, ending in `/api/v1/agent/news`.
- `PAYAM_AGGREGATOR_INGEST_TOKEN`: a long random bearer token matching `PAYAM_NEWS_AGENT_TOKEN` in the panel's server environment.

Never commit the token or put it in the URL.

## Safe test

Use Actions → Automated News Workflow → Run workflow and enable **aggregator_sync_only**. This collects current items, sends them to the panel, and exits before the Telegram publication loop. Normal scheduled runs keep their existing Telegram behavior and will also send newly collected items when the two secrets are configured.

The agent keeps failed ingestion items in `publication_state.json` and retries them on later runs. The panel accepts batches idempotently using source identity plus the agent item ID.

## Data preserved

The panel receives source name/type, agent item ID, original title/body, source link, and source publication time. It creates no AI rewrite from this handoff; the panel's own rewrite workflow remains responsible for its editorial draft. Exact content matches may be linked as duplicates; distinct updates remain separate records.

## Temporary boundary

This connector is a test bridge, not the permanent panel agent. It is isolated from Telegram publication and the existing WordPress archive integration. A native PayamNewsAggregator agent can replace it later.
