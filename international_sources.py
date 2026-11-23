"""Low-cost, source-attributed international news discovery for Iraq and Iran conflict.

Public licensed/accessible RSS and Google News publisher-index feeds only. No paywall scraping,
full-text extraction or reproduction of copyrighted articles.
"""
import calendar
import hashlib
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from international_article_text import enrich_international_item

# Prefer publishers' own active feeds. Google News indexes Reuters/AP/CNN:
# their historical public RSS endpoints are unreliable/unmaintained.
def google_publisher_feed(domain):
    query = f"site:{domain} (Iraq OR Kurdistan OR Iran) when:2d"
    return "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )


SOURCES = (
    # These publishers restrict RSS redistribution/translation. Discover only
    # public, publisher-attributed search headlines; never fetch their RSS text.
    {"name": "The New York Times", "feed": google_publisher_feed("nytimes.com"),
     "domains": ("nytimes.com",), "kind": "google"},
    {"name": "The Washington Post", "feed": google_publisher_feed("washingtonpost.com"),
     "domains": ("washingtonpost.com",), "kind": "google"},
    {"name": "Middle East Eye", "feed": "https://www.middleeasteye.net/rss",
     "domains": ("middleeasteye.net",), "kind": "official"},
    {"name": "Reuters", "feed": google_publisher_feed("reuters.com"),
     "domains": ("reuters.com",), "kind": "google"},
    {"name": "Associated Press", "feed": google_publisher_feed("apnews.com"),
     "domains": ("apnews.com",), "kind": "google"},
    {"name": "CNN", "feed": google_publisher_feed("cnn.com"),
     "domains": ("cnn.com",), "kind": "google"},
    {"name": "BBC Persian", "feed": "https://feeds.bbci.co.uk/persian/rss.xml",
     "domains": ("bbc.com", "bbc.co.uk"), "kind": "official"},
    {"name": "BBC Arabic", "feed": "https://feeds.bbci.co.uk/arabic/rss.xml",
     "domains": ("bbc.com", "bbc.co.uk"), "kind": "official"},
    {"name": "BBC English", "feed": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
     "domains": ("bbc.com", "bbc.co.uk"), "kind": "official",
     "fallback_feed": google_publisher_feed("bbc.com/news")},
)

# Expressions match the headline + the short public feed description, across
# English, Arabic, Persian and Sorani. An Iran mention ALONE is insufficient:
# only Iran war, conflict, security or its negotiations/sanctions qualify.
IRAQ = re.compile(
    r"\b(?:iraq|iraqi|baghdad|erbil|erbil's|kurdistan|kirkuk|mosul|basra|sulaymaniyah|duhok|peshmerga)\b"
    r"|عێراق|عراق|العراق|العراقی|بەغدا|بغداد|هەولێر|ھەولێر|أربيل|اربيل|کوردستان|كردستان"
    r"|کەرکووک|كركوك|کرکوک|موصل|موسڵ|بصرە|البصرة|سلێمانی|السليمانية|دهۆک|دهوك|پێشمەرگە|البيشمركة",
    re.IGNORECASE
)
IRAN = re.compile(
    r"\b(?:iran|iranian|tehran)\b|ئێران|ایران|ايران|إيران|تاران|طهران|تهران",
    re.IGNORECASE
)
CONFLICT = re.compile(
    r"\b(?:war|warfare|attack|attacked|strike|strikes|airstrike|military|missile|drone|bomb"
    r"|bombing|explosion|nuclear|sanction|ceasefire|truce|conflict|fighting|troops"
    r"|israel|israeli|pentagon|us|u\.s\.|united states|hormuz|navy|diplomat|diplomatic"
    r"|negotiat\w*|talks|retaliat\w*|threat|security|hostilit\w*|irgc)\b"
    r"|شەڕ|جەنگ|هێرش|بۆردومان|تەقینەوە|موشەک|درۆن|ئەمریکا|ئەمەریکا|ئیسرائیل"
    r"|ناوکی|ئاسایش|ئاگر‌بەست|دانوستان|هەڕەشە|سزا|سەربازی"
    r"|جنگ|حمله|موشک|پهپاد|انفجار|بمباران|آمریکا|اسرائیل|هسته‌ای|تحریم"
    r"|مذاکره|آتش‌بس|نظامی|امنیت|تنگه هرمز"
    r"|حرب|هجوم|هجمات|قصف|صاروخ|مسيرة|انفجار|النووي|عقوبات|هدنة|وقف إطلاق النار"
    r"|إسرائيل|اسرائيل|أمريكا|امريكا|واشنطن|مفاوضات|عسكري|أمني|هرمز",
    re.IGNORECASE
)

RSS_USER_AGENT = "NewsAgent/1.0 (public RSS; limited topic headlines)"
MAX_ITEMS_PER_SOURCE = 4
MAX_FEED_ENTRIES = 45
MAX_AGE = timedelta(hours=36)


def is_relevant(title, summary=""):
    """Iraq is a topic on its own; Iran needs a conflict/security hook."""
    text = unicodedata.normalize("NFKC", (str(title or "") + " " + str(summary or "")).lower())
    return bool(IRAQ.search(text) or (IRAN.search(text) and CONFLICT.search(text)))


def _site_allowed(url, domains):
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _entry_from_publisher(entry, spec):
    """Reject search hits attributed to a different publisher or invalid links."""
    url = str(entry.get("link") or "").strip()
    if not url.startswith("https://"):
        return False
    if spec["kind"] == "official":
        return _site_allowed(url, spec["domains"])
    # Google News search results are redirects; source.href is the publisher
    # attribution embedded in the Google News RSS item. Fail closed if absent.
    if not _site_allowed(url, ("news.google.com",)):
        return False
    source = entry.get("source") or {}
    origin = str(source.get("href") or source.get("url") or "")
    return bool(origin) and _site_allowed(origin, spec["domains"])


def _clean_excerpt(value):
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for bad in soup(["script", "style", "img"]):
        bad.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:750]


def _published_at(entry, now):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        stamp = datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
    if stamp - now > timedelta(minutes=10) or now - stamp > MAX_AGE:
        return False
    return stamp.isoformat()


def _item(entry, spec, now):
    if not _entry_from_publisher(entry, spec):
        return None
    title = _clean_excerpt(entry.get("title"))
    if spec["kind"] == "google":
        # Remove Google News' appended source label (not part of the headline).
        title = re.sub(r"\s+[-–]\s+" + re.escape(spec["name"]) + r"$", "", title).strip()
        # Google News "description" is a cluster of other headlines, not the
        # Reuters/AP/CNN article text: do not conflate multiple reports.
        description = ""
    else:
        description = _clean_excerpt(entry.get("summary") or entry.get("description"))
    if len(title) < 16 or not is_relevant(title, description):
        return None
    published = _published_at(entry, now)
    if published is False:
        return None
    # No fabricated details. For Google News, the only known fact may be
    # its headline; require a substantial headline, or defer publication.
    if spec["kind"] == "google" and len(title) < 75:
        return None
    body = description or title
    if len(body) < 60:
        return None
    link = str(entry.get("link"))
    # Avoid repeating a short headline verbatim as a fake long "summary".
    if description and description.strip(" .") == title.strip(" ."):
        return None
    author = str(entry.get("author") or "").strip()[:140]
    identity = hashlib.sha256((spec["name"] + "\n" + link).encode("utf-8")).hexdigest()[:24]
    return {
        "id": "intl:" + identity,
        "source": "وێبسایتی نێودەوڵەتی",
        "source_type": "website",
        "source_name": spec["name"],
        "source_byline": author,
        "source_published_at": published,
        "fetched_at": now.isoformat(),
        "title": title,
        "content": body[:750],
        "link": link,
        "image": "",  # no reproduction of third-party news photos
        "international_source": True,
    }


def _fetch_feed_url(spec, feed_url, now, seen_ids, kind=None):
    """Return matching public RSS entries; failures are isolated per feed."""
    label = spec["name"] + (" (index fallback)" if kind else "")
    try:
        response = requests.get(
            feed_url, headers={"User-Agent": RSS_USER_AGENT},
            timeout=(4, 9), allow_redirects=True,
        )
        if response.status_code != 200:
            print(f"International feed {label}: HTTP {response.status_code}.")
            return []
        if len(response.content) > 2_000_000:
            print(f"International feed {label}: oversized feed skipped.")
            return []
        feed = feedparser.parse(response.content)
        if not feed.entries:
            print(f"International feed {label}: zero entries.")
            return []
        active_spec = dict(spec, kind=kind or spec["kind"])
        found = []
        for entry in feed.entries[:MAX_FEED_ENTRIES]:
            item = _item(entry, active_spec, now)
            if item and item["id"] not in seen_ids:
                found.append(item)
                if len(found) >= MAX_ITEMS_PER_SOURCE:
                    break
        print(f"International feed {label}: {len(found)} matching new headlines.")
        return found
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"International feed {label} unavailable ({type(exc).__name__}).")
        return []


def _fetch_source(spec, now, seen_ids):
    matched = _fetch_feed_url(spec, spec["feed"], now, seen_ids)
    if not matched and spec.get("fallback_feed"):
        # Search-index backup if the official feed stalls or contains no
        # current Iraq/Iran-conflict stories. Only publisher-attributed hits.
        return _fetch_feed_url(spec, spec["fallback_feed"], now, seen_ids, kind="google")
    return matched


def fetch_international_news(seen_ids, now=None):
    """Collect only relevant headlines; partial source failures never block news."""
    now = now or datetime.now(timezone.utc)
    results = []
    # Bounded parallel public RSS requests; never fetch paywalled articles.
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {pool.submit(_fetch_source, spec, now, seen_ids): spec for spec in SOURCES}
        for job in as_completed(jobs):
            try:
                results.extend(job.result())
            except Exception as exc:
                print(f"International feed {jobs[job]['name']} unexpected error ({type(exc).__name__}).")
    results.sort(key=lambda e: e.get("source_published_at") or "", reverse=True)
    # Discovery metadata is NOT a full article. Read only approved, public
    # publisher pages; other publishers require an authorized article feed/API.
    # Never publish a Google News headline as a fabricated full report.
    complete = []
    for item in results:
        enriched = enrich_international_item(item)
        if enriched is not None:
            complete.append(enriched)
    print(f"International feeds: {len(results)} discovered; {len(complete)} full-source "
          f"articles ready for independent Sorani reporting.")
    return complete
