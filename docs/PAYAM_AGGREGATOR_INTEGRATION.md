# PayamNewsAggregator test ingestion

This is a temporary one-way ingestion handoff. It sends original collected source items to the newsroom panel; it does not send the agent's Kurdish prepared text and does not replace the existing Telegram or WordPress archive flows.

## GitHub Actions secrets

Configure these repository Actions secrets in `rebwar88/newsAgent`:

- `PAYAM_AGGREGATOR_INGEST_URL`: the panel HTTPS endpoint, ending in `/api/v1/agent/news`.
- `PAYAM_AGGREGATOR_INGEST_TOKEN`: a long random bearer token matching `PAYAM_NEWS_AGENT_TOKEN` in the panel's server environment.

Never commit the token or put it in the URL.

## Safe test

### Local panel on the same computer

1. Start the panel locally with `php artisan serve --host=127.0.0.1 --port=8000`.
2. Install the newsAgent Python requirements in a local clone of this repository.
3. In PowerShell, set the panel URL, matching token, and the explicit loopback-only HTTP opt-in:

   ```powershell
   $env:PAYAM_AGGREGATOR_INGEST_URL = 'http://127.0.0.1:8000/api/v1/agent/news'
   $env:PAYAM_AGGREGATOR_INGEST_TOKEN = '<same token as panel .env>'
   $env:PAYAM_AGGREGATOR_ALLOW_LOCAL_HTTP = 'true'
   $env:AGGREGATOR_SYNC_ONLY = 'true'
   $env:PUBLISH_ENABLED = 'false'
   python main.py
   ```

   This sends source items from the local agent process and skips Telegram publication. It may collect only items not already in the agent's seen cache, plus items still waiting in its pending queue.

### GitHub Actions

GitHub-hosted Actions cannot reach a developer's `localhost`. For a GitHub Actions test, deploy the panel to a non-production HTTPS URL, configure the two Actions secrets above, then open Actions → Automated News Workflow → Run workflow and enable **aggregator_sync_only**. This collects current pending items, sends them to the panel, and exits before the Telegram publication loop. Normal scheduled runs keep their existing Telegram behavior and will also send newly collected items when the two secrets are configured.

The agent keeps failed ingestion items in `publication_state.json` and retries them on later runs. The panel accepts batches idempotently using source identity plus the agent item ID.

## Data preserved

The panel receives source name/type, agent item ID, original title/body, source link, and source publication time. It creates no AI rewrite from this handoff; the panel's own rewrite workflow remains responsible for its editorial draft. Exact content matches may be linked as duplicates; distinct updates remain separate records.

## Temporary boundary

This connector is a test bridge, not the permanent panel agent. It is isolated from Telegram publication and the existing WordPress archive integration. A native PayamNewsAggregator agent can replace it later.
