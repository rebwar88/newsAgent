"""Best-effort extraction of publicly accessible Facebook post permalinks.

No Meta API, authentication, cookies, browser evasion or CAPTCHA bypass.
Only accept post-level structured text, never a page description.
"""
import hashlib
import json
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

POST_PATH = re.compile(r"/(?:posts/|videos/|reel/|permalink/|story\.php|photo(?:s)?/|watch/)")
FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsAgent/1.0; public-post-reader)"}


def is_public_facebook_post(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in FACEBOOK_HOSTS and bool(POST_PATH.search(parsed.path))


def extract_text(node):
    if isinstance(node, dict):
        if node.get("@type") in ("SocialMediaPosting", "Article", "NewsArticle"):
            return str(node.get("articleBody") or node.get("text") or "").strip()
        for value in node.values():
            result = extract_text(value)
            if result:
                return result
    elif isinstance(node, list):
        for value in node:
            result = extract_text(value)
            if result:
                return result
    return ""


def fetch_facebook_public_posts(post_urls, seen_ids):
    articles = []
    for url in post_urls[:10]:
        url = url.strip()
        if not is_public_facebook_post(url):
            print("Facebook: skipped non-public-post URL (only https facebook.com post links supported).")
            continue
        item_id = hashlib.sha256(("facebook:" + url).encode("utf-8")).hexdigest()
        if item_id in seen_ids:
            continue
        try:
            response = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=False)
            if response.status_code != 200:
                print(f"Facebook: post unavailable (HTTP {response.status_code}); skipping.")
                continue
            if len(response.content) > 2_000_000:
                print("Facebook: oversized page; skipping.")
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            text = ""
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    text = extract_text(json.loads(script.string or script.get_text()))
                except (ValueError, TypeError):
                    continue
                if text:
                    break
            text = re.sub(r"\\s+", " ", text).strip()
            if len(text) < 80:
                print("Facebook: no accessible structured post text; skipping (no login or bypass).")
                continue
            articles.append({"id": item_id, "source": "Facebook public post", "title": "", "content": text[:5000], "link": url})
        except requests.RequestException as exc:
            print(f"Facebook: request failed ({type(exc).__name__}); skipping.")
    return articles
