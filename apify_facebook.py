"""Facebook page posts through Apify (paid per returned post). Cost is capped by interval and results limit."""
import hashlib
import os
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
APIFY_RESULTS_LIMIT = int(os.getenv("APIFY_RESULTS_LIMIT", "2"))  # newest posts per page per call = the cost cap
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
    for key in ("time", "timestamp", "date", "publishedAt", "created_time"):
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
    for entry in media or []:
        candidates = []
        if isinstance(entry, str):
            candidates.append(entry)
        elif isinstance(entry, dict):
            for key in ("photo_image", "image", "thumbnail", "photo", "preview_image", "url"):
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
    images = extract_media_urls(raw.get("media"))
    return {"id": "fb:" + post_id, "source": "facebook:" + page_url.rstrip("/").rsplit("/", 1)[-1], "title": "",
            "content": text, "link": post_url, "image": images[0] if images else "", "images": images, "video": ""}


def fetch_facebook_via_apify(seen_ids, state, now=None):
    if not APIFY_TOKEN or not FACEBOOK_PAGE_URLS:
        return []
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
        page = str(raw.get("inputUrl") or raw.get("sourceUrl") or raw.get("facebookUrl") or FACEBOOK_PAGE_URLS[0])
        item = post_to_item(raw, page, now)
        if item and item["id"] not in seen_ids:
            items.append(item)
    items.reverse()  # oldest first
    print(f"Apify returned {len(rows) if isinstance(rows, list) else 0} posts; {len(items)} are new.")
    return items
