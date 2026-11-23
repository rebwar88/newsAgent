"""Discover public Kurdish-language news links from four user-approved publishers.

Read only publicly accessible pages; respect robots directives and site failures.
Articles are inputs for original attributed reporting, not verbatim republication.
"""
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.robotparser import RobotFileParser
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

SITES = (
    {"name": "KNN", "url": "https://www.knnc.net/", "host": "knnc.net",
     "pattern": r"^/Details\.aspx$", "query": "jimare"},
    {"name": "Xendan", "url": "https://www.xendan.org/", "host": "xendan.org",
     "pattern": r"^/detailnews/\d+/?$", "query": None},
    {"name": "Westga News", "url": "https://westganews.net/", "host": "westganews.net",
     "pattern": r"^/dreja\.aspx$", "query": "jmara"},
    {"name": "Payam TV", "url": "https://peyam.net/Home", "host": "peyam.net",
     "pattern": r"^/Details/\d+/?$", "query": None},
)
HEADERS = {"User-Agent": "NewsAgent/1.0 (+public news link discovery; source attribution)"}
TIMEOUT = (4, 9)
MAX_LISTINGS_PER_SITE = 8
MAX_HTML_BYTES = 1_200_000
MAX_ARTICLE_CHARS = 15_000
MIN_ARTICLE_CHARS = 120


def _host_matches(url, hostname):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and parsed.port in (None, 443) and (
        host == hostname or host == "www." + hostname)


@lru_cache(maxsize=64)
def _robots_allowed(hostname, path):
    try:
        r = requests.get("https://" + hostname + "/robots.txt", headers=HEADERS,
                         timeout=TIMEOUT, allow_redirects=False)
        if r.status_code == 404:
            return True
        if r.status_code != 200 or len(r.content) > 400_000:
            return False
        robots = RobotFileParser()
        robots.parse(r.text.splitlines())
        return robots.can_fetch(HEADERS["User-Agent"], "https://" + hostname + path)
    except requests.RequestException:
        return False


def _read_html(url, hostname):
    if not _host_matches(url, hostname):
        return None
    parsed = urlparse(url)
    suffix = parsed.path + ("?" + parsed.query if parsed.query else "")
    if not _robots_allowed(parsed.hostname, suffix or "/"):
        return None
    try:
        with requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                          allow_redirects=False, stream=True) as response:
            if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", "").lower():
                return None
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_HTML_BYTES:
                    return None
            return BeautifulSoup(data.decode(response.encoding or "utf-8", "replace"), "html.parser")
    except requests.RequestException:
        return None


def _article_url(href, spec):
    url = urljoin(spec["url"], href or "")
    if not _host_matches(url, spec["host"]):
        return None
    parsed = urlparse(url)
    if not re.fullmatch(spec["pattern"], parsed.path, re.I):
        return None
    if spec["query"]:
        value = parse_qs(parsed.query).get(spec["query"], [])
        if len(value) != 1 or not value[0].isdigit():
            return None
        # Westga needs its Jor category parameter and legacy '=hewal' parameter.
        # Keep only the known structural parameters and the stable article identifier.
        if spec["host"] == "westganews.net":
            jor = parse_qs(parsed.query).get("Jor", ["2"])[0]
            if not jor.isdigit():
                return None
            return "https://" + spec["host"] + parsed.path + "?=hewal&Jor=" + jor + "&jmara=" + value[0]
        return "https://" + spec["host"] + parsed.path + "?" + spec["query"] + "=" + value[0]
    return "https://" + spec["host"] + parsed.path


def _article_text(soup):
    for node in soup.select("script, style, nav, header, footer, aside, form, .related, .share, .advertisement"):
        node.decompose()
    title_node = soup.select_one("h1")
    if not title_node:
        return None
    title = title_node.get_text(" ", strip=True)
    if len(title) < 12:
        return None
    candidates = []
    for selector in ("article", ".news-detail", ".news-content", ".detail-content",
                     ".content-detail", ".post-content", ".body-content",
                     "main", "#content", ".content"):
        root = soup.select_one(selector)
        if root:
            parts = [p.get_text(" ", strip=True) for p in root.select("p")
                     if len(p.get_text(" ", strip=True)) >= 35]
            if parts:
                candidates.append("\n\n".join(dict.fromkeys(parts)))
    # Fallback: pages such as classic ASP.NET news portals may lack semantic article wrappers.
    if not candidates:
        parts = [p.get_text(" ", strip=True) for p in soup.select("p")
                 if len(p.get_text(" ", strip=True)) >= 45]
        if parts:
            candidates.append("\n\n".join(dict.fromkeys(parts)))
    if not candidates:
        return None
    body = max(candidates, key=len).strip()
    if len(body) < MIN_ARTICLE_CHARS or len(body) > MAX_ARTICLE_CHARS:
        return None
    return title, body


def _fetch_site(spec, seen_ids):
    homepage = _read_html(spec["url"], spec["host"])
    if homepage is None:
        print("Kurdish source " + spec["name"] + ": homepage unavailable or disallowed.")
        return []
    candidates = []
    for anchor in homepage.select("a[href]"):
        url = _article_url(anchor.get("href"), spec)
        if url and url not in candidates:
            candidates.append(url)
        if len(candidates) >= MAX_LISTINGS_PER_SITE:
            break
    items = []
    for url in candidates:
        news_id = "kurdish:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        if news_id in seen_ids:
            continue
        page = _read_html(url, spec["host"])
        if page is None:
            continue
        article = _article_text(page)
        if not article:
            continue
        title, body = article
        items.append({
            "id": news_id, "source": "وێبسایتی کوردی",
            "source_type": "website", "source_name": spec["name"],
            "source_published_at": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "title": title, "content": body, "link": url, "image": "",
        })
    print("Kurdish source " + spec["name"] + ": " + str(len(items)) + " complete new reports.")
    return items


def fetch_kurdish_news(seen_ids):
    found = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_site, spec, seen_ids): spec for spec in SITES}
        for future in as_completed(futures):
            try:
                found.extend(future.result())
            except Exception as exc:
                print("Kurdish source " + futures[future]["name"] +
                      " failed: " + type(exc).__name__)
    return found
