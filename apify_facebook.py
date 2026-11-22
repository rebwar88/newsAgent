"""Facebook page posts through Apify (paid per returned post). Cost is capped by interval and results limit."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone

import requests

DEFAULT_PAGES = [
    "https://www.facebook.com/Rudaw.net",
    "https://www.facebook.com/avamediatv",
    "https://www.facebook.com/diplomatic.media",
    "https://www.facebook.com/profile.php?id=100077778374527",
    "https://www.facebook.com/channel8corp",
]
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
APIFY_ACTOR = os.getenv("APIFY_ACTOR", "apify~facebook-posts-scraper")
FACEBOOK_PAGE_URLS = [u.strip() for u in os.getenv("FACEBOOK_PAGE_URLS", ",".join(DEFAULT_PAGES)).split(",") if u.strip()]
APIFY_INTERVAL = timedelta(minutes=int(os.getenv("APIFY_INTERVAL_MINUTES", "120")))
APIFY_RESULTS_LIMIT = int(os.getenv("APIFY_RESULTS_LIMIT", "2"))  # maximum returned posts per Page
APIFY_FILTER_ENABLED = os.getenv("APIFY_FILTER_ENABLED", "").lower() in ("1", "true", "yes")
APIFY_FILTER_ACTOR = "topapi~facebook-page-posts-scraper"
APIFY_PAGE_BATCH_SIZE = max(1, min(50, int(os.getenv("APIFY_PAGE_BATCH_SIZE", "10"))))
APIFY_MAX_ITEMS_PER_RUN = max(1, min(100, int(os.getenv("APIFY_MAX_ITEMS_PER_RUN", "20"))))
APIFY_MAX_CHARGE_USD = max(0.01, min(1.0, float(os.getenv("APIFY_MAX_CHARGE_USD", "0.05"))))
APIFY_KEYWORDS_FILE = Path(__file__).with_name("facebook_news_keywords.json")
MAX_POST_AGE = timedelta(hours=12)


def apify_slot_available(state, now):
    last = state.get("last_apify_at")
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last) >= APIFY_INTERVAL
    except ValueError:
        return True


def parse_post_time(raw):
    for key in ("time", "timestamp", "date", "publishedAt", "published_at", "created_time"):
        value = raw.get(key)
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value / 1000 if value > 1e12 else value, tz=timezone.utc)
            if isinstance(value, str) and value:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
    return None


def extract_media_urls(media):
    urls = []
    if isinstance(media, (str, dict)):
        media = [media]
    for entry in media or []:
        candidates = []
        if isinstance(entry, str):
            candidates.append(entry)
        elif isinstance(entry, dict):
            for key in ("uri", "photo_image", "image", "thumbnail", "photo", "preview_image", "url"):
                value = entry.get(key)
                if isinstance(value, dict):
                    value = value.get("uri") or value.get("url")
                if isinstance(value, str):
                    candidates.append(value)
        for url in candidates[:1]:
            if url.startswith("https://") and url not in urls:
                urls.append(url)
    return urls[:10]


def post_to_item(raw, page_url, now):
    text = str(raw.get("text") or raw.get("message") or "").strip()
    if len(text) < 30:
        return None
    posted = parse_post_time(raw)
    if posted and now - posted > MAX_POST_AGE:
        return None
    post_url = str(raw.get("postUrl") or raw.get("url") or raw.get("topLevelUrl") or "")
    post_id = str(raw.get("postId") or raw.get("post_id") or "") or hashlib.sha1((post_url or text).encode("utf-8")).hexdigest()[:16]
    images = extract_media_urls(raw.get("media")) or extract_media_urls(raw.get("images")) or extract_media_urls(raw.get("image"))
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    page = urlparse(page_url)
    fallback_name = parse_qs(page.query).get("id", [""])[0] or page.path.strip("/").split("/")[0]
    page_name = raw.get("pageName") or raw.get("source_name") or user.get("name") or fallback_name or "نەناسراو"
    return {"id": "fb:" + post_id, "source": "facebook:" + page_url.rstrip("/").rsplit("/", 1)[-1], "title": "",
            "source_type": "facebook", "source_name": page_name,
            "source_published_at": posted.isoformat() if posted else None, "fetched_at": now.isoformat(),
            "content": text, "link": post_url, "image": images[0] if images else "", "images": images, "video": ""}


def fetch_facebook_via_apify(seen_ids, state, now=None):
    if not APIFY_TOKEN or not FACEBOOK_PAGE_URLS:
        return []
    if APIFY_FILTER_ENABLED:
        return fetch_filtered_facebook_via_apify(seen_ids, state, now)
    now = now or datetime.now(timezone.utc)
    if not apify_slot_available(state, now):
        return []
    state["last_apify_at"] = now.isoformat()  # set before the paid call so failures cannot cause a cost loop
    try:
        res = requests.post(f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items",
                            headers={"Authorization": f"Bearer {APIFY_TOKEN}"}, params={"timeout": 180},
                            json={"startUrls": [{"url": u} for u in FACEBOOK_PAGE_URLS], "resultsLimit": APIFY_RESULTS_LIMIT}, timeout=200)
        if not res.ok:
            print(f"Apify request failed (HTTP {res.status_code}).")
            return []
        rows = res.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Apify request failed ({type(exc).__name__}).")
        return []
    items = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        page = str(raw.get("inputUrl") or raw.get("sourceUrl") or raw.get("facebookUrl") or "")
        item = post_to_item(raw, page, now)
        if item and item["id"] not in seen_ids:
            items.append(item)
    items.reverse()  # oldest first
    print(f"Apify returned {len(rows) if isinstance(rows, list) else 0} posts; {len(items)} are new.")
    return items



def load_news_keywords():
    """Read the newsroom's editorial triggers. Fail closed if missing or empty."""
    try:
        values = json.loads(APIFY_KEYWORDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Apify keyword filter disabled: cannot load keyword file ({type(exc).__name__}).")
        return []
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(word.strip() for word in values if isinstance(word, str) and word.strip()))


def filtered_apify_request(pages, keywords):
    """Build the paid Actor input from local configuration, before scraping."""
    return {
        "startUrls": list(pages),
        "keywordFilter": ", ".join(keywords),
        "monitorMode": True,
        "monitorStoreName": "newsagent-facebook-news-v1",
        "startDate": "12 hours",
        "maxPostsPerPage": max(1, min(20, APIFY_RESULTS_LIMIT)),
        "maxItems": APIFY_MAX_ITEMS_PER_RUN,
        "includePostDetails": False,
        "captionText": False,
        "outputFormat": "default",
    }


def _filtered_rows_to_items(rows, seen_ids, now):
    items = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        page = str(
            raw.get("input_url") or raw.get("inputUrl") or raw.get("source_url") or
            raw.get("sourceUrl") or raw.get("facebookUrl") or ""
        )
        normalized = dict(raw)
        if not normalized.get("postUrl"):
            normalized["postUrl"] = raw.get("url") or ""
        if not normalized.get("time"):
            normalized["time"] = raw.get("published_at") or raw.get("timestamp")
        if not normalized.get("text"):
            normalized["text"] = raw.get("message") or ""
        item = post_to_item(normalized, page, now)
        if item and item["id"] not in seen_ids:
            items.append(item)
    return list(reversed(items))


def fetch_filtered_facebook_via_apify(seen_ids, state, now=None):
    """Keyword filter INSIDE the Actor: only delivered matches count as post results.

    The legacy scraper remains the default until APIFY_FILTER_ENABLED=true.
    A page cursor spreads large 30-50 Page collections across multiple runs.
    A hard USD ceiling and item cap limit every paid API request.
    """
    now = now or datetime.now(timezone.utc)
    if not APIFY_TOKEN or not FACEBOOK_PAGE_URLS or not apify_slot_available(state, now):
        return []
    keywords = load_news_keywords()
    if not keywords:
        print("Apify filtered run skipped: no editorial keywords configured.")
        return []
    total_pages = len(FACEBOOK_PAGE_URLS)
    cursor = int(state.get("apify_filtered_cursor", 0)) % total_pages
    batch_len = min(total_pages, APIFY_PAGE_BATCH_SIZE)
    pages = [FACEBOOK_PAGE_URLS[(cursor + i) % total_pages] for i in range(batch_len)]
    payload = filtered_apify_request(pages, keywords)
    # Persist cooldown before the paid call to prevent a failure/timeout cost loop.
    state["last_apify_at"] = now.isoformat()
    try:
        res = requests.post(
            f"https://api.apify.com/v2/acts/{APIFY_FILTER_ACTOR}/run-sync-get-dataset-items",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
            params={
                "timeout": 180,
                "maxItems": APIFY_MAX_ITEMS_PER_RUN,
                "maxTotalChargeUsd": APIFY_MAX_CHARGE_USD,
            },
            json=payload,
            timeout=200,
        )
        if not res.ok:
            print(f"Apify filtered request failed (HTTP {res.status_code}); no page rotation.")
            return []
        rows = res.json()
        if not isinstance(rows, list):
            print("Apify filtered request returned unexpected non-list data.")
            return []
    except (requests.RequestException, ValueError) as exc:
        print(f"Apify filtered request failed ({type(exc).__name__}); no page rotation.")
        return []
    state["apify_filtered_cursor"] = (cursor + batch_len) % total_pages
    items = _filtered_rows_to_items(rows, seen_ids, now)
    print(f"Apify filtered: {len(pages)} pages scanned; {len(rows)} matching posts; {len(items)} unseen. "
          f"Next page cursor {state['apify_filtered_cursor']}.")
    return items
