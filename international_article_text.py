"""Bounded article evidence enrichment, only for publishers explicitly approved.

No paywall bypass, login, headless browser, proxy or Google cache. The retrieved
source text is evidence for creating an original attributed Sorani report, not a
license to republish verbatim copyrighted reporting.
"""
import json
import os
import re
from functools import lru_cache
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

# Only MEE has been technically tested for public HTML/robots access. Other
# publishers need independently established collection/reuse authorization.
DEFAULT_APPROVED_DOMAINS = ("middleeasteye.net",)
MAX_HTML_BYTES = 1_600_000
MAX_SOURCE_CHARS = 17000
MIN_REPORT_WORDS = 125
MIN_REPORT_PARAGRAPHS = 3
MIN_REPORT_CHARS = 680
HEADERS = {
    "User-Agent": "NewsAgent/1.0 (+public editorial source reading; no bulk archiving)",
    "Accept": "text/html,application/xhtml+xml",
}
CONTENT_SELECTORS = (
    "article .article-body",
    "article .article__body",
    "article .field--name-body",
    "article .field--name-field-body",
    "article .wysiwyg",
    "article .rich-text",
    "article",
)
BLOCKED_DOMAINS = {
    "nytimes.com", "washingtonpost.com", "bbc.com", "bbc.co.uk",
    "reuters.com", "apnews.com", "cnn.com",
}


def _allowed_hostname(url, approved):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_DOMAINS):
        return False
    return any(host == domain or host.endswith("." + domain) for domain in approved)


@lru_cache(maxsize=32)
def _robots_allowed(host, path):
    """Fail closed when robots policy is unavailable or disallows a URL."""
    try:
        response = requests.get(f"https://{host}/robots.txt", headers=HEADERS,
                                timeout=(4, 9), allow_redirects=False)
        if response.status_code == 404:
            return True
        if response.status_code != 200 or len(response.content) > 500_000:
            return False
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch(HEADERS["User-Agent"], f"https://{host}{path}")
    except requests.RequestException:
        return False


def _paragraphs_from_html(raw_html):
    soup = BeautifulSoup(raw_html, "html.parser")
    # JSON-LD full article evidence, when publisher explicitly offers it.
    for node in soup.select("script[type='application/ld+json']"):
        try:
            parsed = json.loads(node.string or node.get_text())
        except (ValueError, TypeError):
            continue
        records = parsed if isinstance(parsed, list) else [parsed]
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("@graph"), list):
                records = records + record["@graph"]
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("@type") not in ("NewsArticle", "Article", "ReportageNewsArticle"):
                continue
            body = record.get("articleBody")
            if isinstance(body, str) and len(body) > MIN_REPORT_CHARS:
                return [part.strip() for part in re.split(r"\n{2,}", body)
                        if part.strip()][:30]
    for selector in CONTENT_SELECTORS:
        root = soup.select_one(selector)
        if not root:
            continue
        for bad in root.select(
            "script, style, nav, header, footer, aside, form, figure, figcaption, "
            ".related, .share, .social, .newsletter, .advertisement, .ad-container, "
            ".byline, .author, .date, .tags"
        ):
            bad.decompose()
        parts = []
        for p in root.select("p"):
            text = " ".join(p.get_text(" ", strip=True).split())
            if len(text) >= 35 and text not in parts:
                parts.append(text)
        if parts:
            return parts[:48]
    return []


def full_story_text(html):
    """Only return substantive article evidence, not a short live update."""
    paragraphs = _paragraphs_from_html(html)
    text = "\n\n".join(paragraphs).strip()
    # Never silently truncate the publisher report and call it complete.
    if len(text) > MAX_SOURCE_CHARS:
        return None
    words = re.findall(r"\S+", text)
    if (len(paragraphs) < MIN_REPORT_PARAGRAPHS or
            len(words) < MIN_REPORT_WORDS or len(text) < MIN_REPORT_CHARS):
        return None
    return text


def enrich_international_item(item, approved_domains=DEFAULT_APPROVED_DOMAINS):
    """Read one publicly accessible, permitted publisher article before rewriting.

    Returns None if only a brief/Google headline is available. No publication
    is possible without verified substantive source evidence.
    """
    if not isinstance(item, dict) or not item.get("international_source"):
        return None
    url = str(item.get("link") or "")
    if not _allowed_hostname(url, approved_domains):
        return None
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not _robots_allowed(parsed.hostname.lower(), path + (("?" + parsed.query) if parsed.query else "")):
        print("International source page skipped: robots access not permitted or not verifiable.")
        return None
    try:
        res = requests.get(url, headers=HEADERS, timeout=(5, 12),
                           allow_redirects=False, stream=True)
        try:
            if res.status_code != 200:
                print(f"International source page returned HTTP {res.status_code}; not republishing excerpt.")
                return None
            if "text/html" not in res.headers.get("Content-Type", "").lower():
                return None
            raw = bytearray()
            for chunk in res.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > MAX_HTML_BYTES:
                    print("International source page too large; skipped.")
                    return None
            text = full_story_text(bytes(raw).decode(res.encoding or "utf-8", errors="replace"))
        finally:
            res.close()
    except requests.RequestException as exc:
        print(f"International full article unavailable ({type(exc).__name__}).")
        return None
    if not text:
        print("International source is a short update or a partial page; full-story publishing skipped.")
        return None
    enriched = dict(item)
    enriched["content"] = text
    enriched["international_full_evidence"] = True
    enriched["source_word_count"] = len(text.split())
    return enriched


def has_complete_international_evidence(item):
    """Prevent old short-feed pending items from being mistaken for full news."""
    return bool(not item.get("international_source") or (
        item.get("international_full_evidence") and
        len(str(item.get("content") or "").split()) >= MIN_REPORT_WORDS and
        len(str(item.get("content") or "")) >= MIN_REPORT_CHARS
    ))
