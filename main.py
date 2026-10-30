import os
import calendar
import json
import hashlib
import html
import re
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
import requests
import time
import feedparser
from bs4 import BeautifulSoup
import editorial
from apify_facebook import fetch_facebook_via_apify
from facebook_public import fetch_facebook_public_posts, discover_public_page_posts, FACEBOOK_PAGE_SHARE_URLS

# ================= Configuration =================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODELS = [m.strip() for m in os.getenv("OPENROUTER_MODELS", "google/gemini-2.5-flash,openrouter/free").split(",") if m.strip()]
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DASHBOARD_NEWS_API_URL = os.getenv("DASHBOARD_NEWS_API_URL", "").strip()
DASHBOARD_NEWS_API_KEY = os.getenv("DASHBOARD_NEWS_API_KEY", "").strip()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
PUBLISH_ENABLED = os.getenv("PUBLISH_ENABLED", "false").lower() == "true"

SEEN_FILE = "seen_urls.json"
MAX_ITEMS_PER_RUN = 1
GEMINI_INTERVAL = timedelta(minutes=8)  # limit Gemini calls independently of the 5-minute publishing interval
QUOTA_BACKOFF = timedelta(minutes=30)
PUBLISH_STATE_FILE = "publication_state.json"
# Comma-separated public Facebook POST permalinks; no login or Meta API required.
FACEBOOK_PUBLIC_POST_URLS = [u.strip() for u in os.getenv("FACEBOOK_PUBLIC_POST_URLS", "").split(",") if u.strip()]
NORMAL_INTERVAL = timedelta(minutes=5)  # minimum interval between normal-news publications

# لیستی فیدی ماڵپەڕەکان (RSS)
RSS_FEEDS = [
    # نموونە: لینکەکانی خۆت لێرە دابنێ
    # "https://www.rudaw.net/sorani/rss",
    # "https://nrttv.com/feed/",
]

# لیستی چەناڵە گشتییەکانی تێلیگرام (تەنها ناوی بەکارهێنەر/username بێ @)
TELEGRAM_CHANNELS = [
    "brakingnews24",
    "ClashReport",
    "Hengaw_kurdi",
    "entekhab_ir",
    "Khabar_Fouri",
    "ajMubasher"
]

def load_seen_ids():
    """هەواڵە بینراوەکان لە فایلی JSON دەخوێنێتەوە."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            raise RuntimeError(f"Unsafe seen file; publication stopped: {e}")
    return set()

def save_seen_ids(seen_ids):
    """لیستی هەواڵە بینراوەکان نوێ دەکاتەوە و تەنها دوایین ١٠٠٠ هەواڵ دەهێڵێتەوە."""
    trimmed = sorted(seen_ids)  # Keep all published IDs; a set has no chronological order
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)

def load_publication_state():
    if not os.path.exists(PUBLISH_STATE_FILE):
        return {"last_normal_at": None, "last_gemini_at": None, "quota_blocked_until": None}
    try:
        with open(PUBLISH_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("Expected object")
        return state
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Invalid publication state: {exc}")

def save_publication_state(state):
    with open(PUBLISH_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_breaking(item):
    text = (item.get("title", "") + " " + item.get("content", ""))[:350]
    return bool(re.search(r"(?i)(?:^|[\s:!🚨🔴])(?:breaking(?: news)?|urgent|عاجل|بەپەلە|فوری)(?:[\s:!🚨🔴]|$)", text))

def normal_slot_available(state, now):
    last = state.get("last_normal_at")
    if not last:
        return True
    try:
        previous = datetime.fromisoformat(last)
        if previous.tzinfo is None:
            raise ValueError("Missing timezone")
        return now - previous >= NORMAL_INTERVAL
    except (ValueError, TypeError):
        raise RuntimeError("Invalid last publication timestamp")

def gemini_slot_available(state, now):
    blocked = state.get("quota_blocked_until")
    if blocked and now < datetime.fromisoformat(blocked):
        return False
    previous = state.get("last_gemini_at")
    if previous and now - datetime.fromisoformat(previous) < GEMINI_INTERVAL:
        return False
    return True

IMAGE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAX_IMAGE_BYTES = 9_000_000

def extract_telegram_image(msg):
    """Return the first photo (or link-preview image) URL of a public Telegram web-preview message."""
    for css in ("tgme_widget_message_photo_wrap", "link_preview_image", "link_preview_right_image"):
        node = msg.find(["a", "i"], class_=css)
        if node:
            m = re.search(r"url\(['\"]?([^'\")]+)", node.get("style", ""))
            if m and m.group(1).startswith("https://"):
                return html.unescape(m.group(1))
    return ""

MAX_ALBUM_IMAGES = 10  # Telegram albums hold at most 10 items

def extract_telegram_images(msg):
    """Return every photo URL (in order, no duplicates, max 10) of a public Telegram web-preview message."""
    urls = []
    for node in msg.find_all(["a", "i"], class_="tgme_widget_message_photo_wrap"):
        m = re.search(r"url\(['\"]?([^'\")]+)", node.get("style", ""))
        if m and m.group(1).startswith("https://"):
            url = html.unescape(m.group(1))
            if url not in urls:
                urls.append(url)
    if not urls:
        single = extract_telegram_image(msg)  # link-preview image as a fallback
        if single:
            urls.append(single)
    return urls[:MAX_ALBUM_IMAGES]

def extract_rss_image(entry, summary_html):
    for media in entry.get("media_content", []) or []:
        url = media.get("url", "")
        if url.startswith("https://") and (media.get("type", "image").startswith("image") or media.get("medium") == "image"):
            return url
    for link in entry.get("links", []) or []:
        if str(link.get("type", "")).startswith("image/") and str(link.get("href", "")).startswith("https://"):
            return link["href"]
    img = BeautifulSoup(summary_html or "", "html.parser").find("img")
    src = img.get("src", "") if img else ""
    return src if src.startswith("https://") else ""

def download_image(url):
    """Download an image so it can be uploaded to Telegram. Returns (bytes, content_type) or None."""
    if not url or not url.startswith("https://"):
        return None
    try:
        res = requests.get(url, headers=IMAGE_UA, timeout=15)
        content_type = res.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not res.ok or not content_type.startswith("image/"):
            print(f"Image skipped (HTTP {res.status_code}, type {content_type or 'unknown'}).")
            return None
        if not 1000 <= len(res.content) <= MAX_IMAGE_BYTES:
            print("Image skipped (unsuitable size).")
            return None
        return res.content, content_type
    except requests.RequestException as exc:
        print(f"Image download failed ({type(exc).__name__}).")
        return None

MAX_VIDEO_BYTES = 45_000_000  # Telegram bots can upload up to 50 MB

def read_mp4_info(data):
    """Read (width, height, duration_seconds) of the first video track from an MP4/MOV file. Returns None if unreadable."""
    def boxes(start, end):
        pos = start
        while pos + 8 <= end:
            size = int.from_bytes(data[pos:pos + 4], "big")
            kind = data[pos + 4:pos + 8]
            header = 8
            if size == 1:
                size = int.from_bytes(data[pos + 8:pos + 16], "big")
                header = 16
            elif size == 0:
                size = end - pos
            if size < header:
                return
            yield kind, pos + header, min(pos + size, end)
            pos += size
    try:
        moov = next(((a, b) for kind, a, b in boxes(0, len(data)) if kind == b"moov"), None)
        if not moov:
            return None
        duration = 0.0
        found = None
        for kind, a, b in boxes(*moov):
            if kind == b"mvhd":
                version = data[a]
                offset = a + (20 if version == 1 else 12)
                timescale = int.from_bytes(data[offset:offset + 4], "big")
                dur = int.from_bytes(data[offset + 4:offset + (12 if version == 1 else 8)], "big")
                duration = dur / timescale if timescale else 0.0
            elif kind == b"trak" and not found:
                tkhd = None
                is_video = False
                for k2, a2, b2 in boxes(a, b):
                    if k2 == b"tkhd":
                        tkhd = a2
                    elif k2 == b"mdia":
                        for k3, a3, b3 in boxes(a2, b2):
                            if k3 == b"hdlr" and data[a3 + 8:a3 + 12] == b"vide":
                                is_video = True
                if tkhd is not None and is_video:
                    version = data[tkhd]
                    base = tkhd + (52 if version == 1 else 40)
                    matrix = [int.from_bytes(data[base + i * 4:base + i * 4 + 4], "big", signed=True) for i in range(9)]
                    wpos = base + 36
                    width = int.from_bytes(data[wpos:wpos + 4], "big") >> 16
                    height = int.from_bytes(data[wpos + 4:wpos + 8], "big") >> 16
                    if matrix[0] == 0 and matrix[4] == 0 and matrix[1] != 0 and matrix[3] != 0:
                        width, height = height, width  # rotated 90/270 degrees: the displayed size is swapped
                    if width > 0 and height > 0:
                        found = (width, height)
        if not found:
            return None
        return found[0], found[1], int(round(duration))
    except (IndexError, ValueError, ZeroDivisionError):
        return None

def extract_telegram_video(msg):
    """Return the direct video URL of a public Telegram web-preview message (only small videos expose one)."""
    node = msg.find("video")
    src = node.get("src", "") if node else ""
    return html.unescape(src) if src.startswith("https://") else ""

def download_video(url):
    """Download a video for upload to Telegram. Returns (bytes, content_type) or None. Never touches Gemini."""
    if not url or not url.startswith("https://"):
        return None
    try:
        res = requests.get(url, headers=IMAGE_UA, timeout=(10, 60), stream=True)
        content_type = res.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not res.ok or not content_type.startswith("video/"):
            print(f"Video skipped (HTTP {res.status_code}, type {content_type or 'unknown'}).")
            return None
        chunks, size = [], 0
        for chunk in res.iter_content(chunk_size=1024 * 256):
            size += len(chunk)
            if size > MAX_VIDEO_BYTES:
                print("Video skipped (larger than the 45 MB upload limit).")
                return None
            chunks.append(chunk)
        data = b"".join(chunks)
        return (data, content_type) if len(data) >= 1000 else None
    except requests.RequestException as exc:
        print(f"Video download failed ({type(exc).__name__}).")
        return None

MAX_VIDEO_BYTES = 45_000_000  # Telegram bots can upload up to 50 MB

def extract_telegram_video(msg):
    """Return the direct video URL of a public Telegram web-preview message, if the page exposes one."""
    node = msg.find("video")
    src = node.get("src", "") if node else ""
    return html.unescape(src) if src.startswith("https://") else ""

def download_video(url):
    """Download a video (max 45 MB) so it can be uploaded to Telegram. Returns (bytes, content_type) or None."""
    if not url or not url.startswith("https://"):
        return None
    res = None
    try:
        res = requests.get(url, headers=IMAGE_UA, timeout=(10, 60), stream=True)
        content_type = res.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not res.ok or not (content_type.startswith("video/") or content_type == "application/octet-stream"):
            print(f"Video skipped (HTTP {res.status_code}, type {content_type or 'unknown'}).")
            return None
        try:
            declared = int(res.headers.get("Content-Length") or 0)
        except ValueError:
            declared = 0
        if declared > MAX_VIDEO_BYTES:
            print("Video skipped (larger than the 45 MB limit).")
            return None
        chunks, total = [], 0
        for chunk in res.iter_content(262144):
            total += len(chunk)
            if total > MAX_VIDEO_BYTES:
                print("Video skipped (larger than the 45 MB limit).")
                return None
            chunks.append(chunk)
        data = b"".join(chunks)
        if len(data) < 5000:
            print("Video skipped (unsuitable size).")
            return None
        return data, (content_type if content_type.startswith("video/") else "video/mp4")
    except requests.RequestException as exc:
        print(f"Video download failed ({type(exc).__name__}).")
        return None
    finally:
        if res is not None:
            res.close()

def fetch_rss_news(seen_ids):
    """هەواڵە نوێیەکان لە فیدی RSSی ماڵپەڕەکان کۆدەکاتەوە."""
    news_items = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                link = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                
                # پاککردنەوەی تاگە HTMLەکان لە ناو تێکست
                clean_text = BeautifulSoup(summary, "html.parser").get_text(separator=" ").strip()
                news_id = hashlib.md5(link.encode('utf-8')).hexdigest()

                if news_id not in seen_ids and link:
                    news_items.append({
                        "id": news_id,
                        "source": "وێبسایت",
                        "source_type": "website",
                        "source_published_at": datetime.fromtimestamp(calendar.timegm(entry["published_parsed"]), timezone.utc).isoformat() if entry.get("published_parsed") else None,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "source_name": feed.feed.get("title") or urlparse(link or feed_url).hostname,
                        "title": title,
                        "content": clean_text or title,
                        "link": link,
                        "image": extract_rss_image(entry, summary)
                    })
        except Exception as e:
            print(f"Error fetching RSS {feed_url}: {e}")
    return news_items

def fetch_telegram_public_channel(channel_username, seen_ids):
    """پەیامە نوێیەکان لە ڕووکاری وێبی چەناڵی تێلیگرام وەردەگرێت بێ پێویستی بە ئەکاونت."""
    news_items = []
    url = f"https://t.me/s/{channel_username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            channel_title = soup.select_one(".tgme_channel_info_header_title")
            channel_name = channel_title.get_text(" ", strip=True) if channel_title else channel_username
            messages = soup.find_all("div", class_="tgme_widget_message")
            
            for msg in messages[-5:]:  # دوایین ٥ پەیام
                text_div = msg.find("div", class_="tgme_widget_message_text")
                if not text_div:
                    continue
                content = text_div.get_text(separator="\n").strip()
                
                # وەرگرتنی لینکی پەیامەکە
                data_post = msg.get("data-post", "")
                post_id = hashlib.md5((data_post or (channel_username + content)).encode("utf-8")).hexdigest()
                
                if post_id not in seen_ids and len(content) > 30:
                    news_items.append({
                        "id": post_id,
                        "source": f"تێلیگرام (@{channel_username})",
                        "source_type": "telegram",
                        "source_published_at": (msg.select_one("time[datetime]").get("datetime") if msg.select_one("time[datetime]") else None),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "source_name": channel_name,
                        "title": "",
                        "content": content,
                        "link": f"https://t.me/{data_post}" if data_post else "",
                        "image": extract_telegram_image(msg),
                        "images": extract_telegram_images(msg),
                        "video": extract_telegram_video(msg)
                    })
    except Exception as e:
        print(f"Error fetching Telegram @{channel_username}: {e}")
    return news_items

def rewrite_with_gemini(raw_text):
    """دەقی هەواڵەکە بە Gemini دادەڕێژێتەوە بەپێی ڕێنماییە کوردییەکان."""
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set; skipping publication.")
        return None

    base_url = "https://generativelanguage.googleapis.com/v1beta"
    headers = {"x-goog-api-key": GEMINI_API_KEY}
    model = GEMINI_MODEL

    system_prompt = (
        "تۆ وەرگێڕ و سەرنووسەری شارەزای هەواڵی کوردیی سۆرانی (شێوازی ڕۆژنامەوانیی کوردستانی عێراق)یت. "
        "سەرچاوە دەتوانێت فارسی، عەرەبی، ئینگلیزی یان کوردی بێت؛ سەرەتا واتای هەواڵەکە تێبگە، "
        "پاشان بە سۆرانییەکی سروشتی، ڕەوان و ڕۆژنامەوانی دایبڕێژەوە، نەک وەرگێڕانی وشە بە وشە. "
        "ڕستەسازی فارسی و عەرەبی مەگوازەوە بۆ کوردی؛ وشە و دەستەواژەی باوی سۆرانی بەکاربهێنە. "
        "ناوە تایبەتەکان، ژمارەکان، بەروارەکان، شوێنەکان و وتە ڕاستەوخۆکان بە وردی بپارێزە. "
        "هیچ زانیاری، هۆکار، ژمارە یان وتەیەکی نەبوو لە سەرچاوە زیاد مەکە. "
        "ئەگەر بانگەشەیەک پشتڕاست نەکرابێتەوە، بە ڕوونی بیخە پاڵ سەرچاوەکە. "
        "تەنها JSON ـێکی دروست بە دوو خانەی title و body بگەڕێنەوە. "
        "title سەردێڕێکی کورت و ڕوونی سۆرانی بێت؛ body دوو تا سێ پەرەگرافی ڕەوان بێت، "
        "بەڵام ئەگەر دەقی سەرچاوە کورتە، هەواڵەکە بە زۆر درێژ مەکەوە. "
        "دەقی فارسی یان عەرەبی بە خاوی مەهێڵەوە و بە تێکەڵاوی زمانەکان مەینووسە. "
        "هیچ URL، لینک، هاشتاگ، HTML، Markdown یان @username مەنووسە."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system_prompt}\n\nدەقی خاوی هەواڵەکە:\n{raw_text}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 2400,
            "responseMimeType": "application/json"
        }
    }

    def request_with_retry(selected_model):
        endpoint = f"{base_url}/models/{selected_model}:generateContent"
        for attempt in range(3):
            try:
                response = requests.post(endpoint, headers=headers, json=payload, timeout=45)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == 2:
                    print(f"Gemini transport unavailable after retries ({type(exc).__name__}).")
                    return None
                delay = (5, 15)[attempt]
                print(f"Gemini transport issue; retry {attempt + 1}/2 in {delay}s.")
                time.sleep(delay)
                continue
            if response.status_code not in (500, 502, 503, 504):
                return response
            if attempt == 2:
                print(f"Gemini HTTP {response.status_code} after 3 attempts; keeping article for retry.")
                return response
            delay = (5, 15)[attempt]
            print(f"Gemini HTTP {response.status_code}; retry {attempt + 1}/2 in {delay}s.")
            time.sleep(delay)
        return None

    try:
        response = request_with_retry(model)
        if response is None:
            return None
        if response.status_code == 404:
            # Some keys/projects have a different set of supported models.
            models_response = requests.get(f"{base_url}/models", headers=headers, timeout=15)
            models_response.raise_for_status()
            supported = [
                m["name"].removeprefix("models/")
                for m in models_response.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
                and "flash" in m.get("name", "").lower()
            ]
            if not supported:
                print("No accessible Gemini Flash models found for this API key.")
                return None
            model = next((m for m in supported if m.startswith("gemini-3.8-flash")), next((m for m in supported if m != GEMINI_MODEL), supported[0]))
            print(f"Requested model unavailable; retrying with available model: {model}")
            response = request_with_retry(model)
            if response is None:
                return None
        if response.status_code == 429:
            raise RuntimeError("GEMINI_QUOTA_EXCEEDED")
        if not response.ok:
            print(f"Gemini API returned HTTP {response.status_code}: {response.text[:300]}")
            return None
        res_json = response.json()
        candidate = res_json["candidates"][0]
        rewritten = "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", [])).strip()
        if candidate.get("finishReason") == "MAX_TOKENS":
            print("Gemini output truncated at token limit; keeping item for retry.")
            return None
        try:
            data = json.loads(rewritten)
        except json.JSONDecodeError as exc:
            print(f"Gemini returned invalid JSON ({exc.msg}); keeping item for retry.")
            return None
        if not isinstance(data, dict):
            return None
        title = re.sub(r"https?://\S+|www\.\S+", "", str(data.get("title", ""))).strip()
        body = re.sub(r"https?://\S+|www\.\S+", "", str(data.get("body", ""))).strip()
        if len(title) < 8 or len(body) < 60:
            print("Gemini returned an incomplete article; skipping.")
            return None
        return validate_ai_news({"title": title, "body": body})
    except RuntimeError as e:
        if str(e) == "GEMINI_QUOTA_EXCEEDED":
            raise
        print(f"Error calling Gemini: {e}")
        return None
    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return None


def validate_ai_news(data):
    """Reject empty or obviously untranslated AI responses before publication."""
    if not isinstance(data, dict):
        return None
    title = re.sub(r"https?://\\S+|www\\.\\S+", "", str(data.get("title") or "")).strip()
    body = re.sub(r"https?://\\S+|www\\.\\S+", "", str(data.get("body") or "")).strip()
    if len(title) < 8 or len(body) < 60:
        return None
    # Distinctive Sorani characters; not sufficient for literary quality but
    # prevents unmodified Persian/Arabic text from being published as Sorani.
    if len(re.findall(r"[ئۆێەڕڵڤ]", title + body)) < 5:
        print("AI text rejected: insufficient Sorani orthography.")
        return None
    return {"title": title, "body": body}


def rewrite_with_openrouter(raw_text):
    """Use OpenRouter only after Gemini cannot prepare the article."""
    if not OPENROUTER_API_KEY or not OPENROUTER_MODELS:
        print("OpenRouter not configured.")
        return None
    instructions = (
        "You are an experienced Iraqi Kurdish Sorani news editor. Translate Persian, Arabic, "
        "English or Kurdish source news into natural, fluent Kurdish Sorani (Arabic script), "
        "not literal Persian/Arabic calques. Preserve every name, number, date, attribution "
        "and uncertainty. Never invent facts. Return ONLY valid JSON with string fields "
        "title and body, a concise headline and a factual news article. No URLs, hashtags "
        "or markdown. Both fields MUST be Kurdish Sorani."
    )
    for model in OPENROUTER_MODELS:
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": model, "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": raw_text[:12000]}],
                    "temperature": 0.3, "max_tokens": 1800},
                timeout=35)
            if not response.ok:
                print(f"OpenRouter {model} HTTP {response.status_code}.")
                if response.status_code == 401:
                    break  # Invalid key: no other model can work.
                # 402 (no credit for a paid model) or 429 (rate limit): try the next model, e.g. the free router.
                continue
            body = response.json()
            if not isinstance(body, dict) or not body.get("choices"):
                err = body.get("error") if isinstance(body, dict) else None
                detail = err.get("message") if isinstance(err, dict) else (err or "unexpected response")
                print(f"OpenRouter {model} returned no result: {str(detail)[:120]}")
                continue
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            content = str(content).strip()
            if content.startswith("```"):
                content = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", content).strip()
            prepared = validate_ai_news(json.loads(content))
            if prepared:
                return prepared
            print(f"OpenRouter {model}: incomplete or untranslated output.")
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            snippet = ""
            if isinstance(exc, ValueError) and "response" in locals():
                snippet = f" HTTP {response.status_code}, body starts: {response.text[:80]!r}"
            print(f"OpenRouter {model} failed ({type(exc).__name__}).{snippet}")
    return None


def format_without_ai(item):
    """Safely format existing Sorani text without inventing or translating facts."""
    raw_title = str(item.get("title") or "")
    raw_body = str(item.get("content") or "")
    def clean(value):
        value = re.sub(r"https?://\S+|www\.\S+|@[A-Za-z0-9_]+|#[\w]+", "", value)
        value = re.sub(r"<[^>]*>", "", value)
        value = re.sub(r"[ \t]+", " ", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()
    title, body = clean(raw_title), clean(raw_body)
    # Do not publish untranslated foreign-language or nearly empty news.
    sorani_chars = re.findall(r"[ئۆێەڕڵڤ]", title + body)
    if len(sorani_chars) < 5 or len(body) < 60:
        print("Non-AI fallback rejected: insufficient Sorani text or article content.")
        return None
    if not title:
        meaningful_lines = [line.strip(" •-–—:،.؛!؟") for line in body.splitlines() if len(line.strip(" •-–—:،.؛!؟")) >= 8]
        title = (meaningful_lines[0] if meaningful_lines else body.strip(" •-–—:،.؛!؟"))[:110].strip()
    if len(title) < 8:
        print("Non-AI fallback rejected: missing meaningful title.")
        return None
    if len(title) + len(body) > 3600:
        body = body[: max(0, 3550 - len(title))].rsplit(" ", 1)[0] + "…"
    return {"title": title, "body": body}


TRANSLATE_FALLBACK_ENABLED = os.getenv("TRANSLATE_FALLBACK_ENABLED", "true").lower() == "true"
TRANSLATE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
SORANI_LETTERS = r"[ئۆێەڕڵڤ]"

TRANSLATE_SERVICE_FAILED = False  # set when the free translation service itself is unreachable

def translate_to_sorani(text):
    """Free machine translation to Sorani (no Gemini, no API key). Returns None on any failure."""
    global TRANSLATE_SERVICE_FAILED
    params = {"client": "gtx", "sl": "auto", "tl": "ckb", "dt": "t"}
    for attempt in range(2):
        try:
            res = requests.post(TRANSLATE_ENDPOINT, params=params, data={"q": text}, timeout=15,
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            if not res.ok:
                print(f"Translation service HTTP {res.status_code}.")
            else:
                segments = res.json()[0]
                translated = "".join(seg[0] for seg in segments if seg and seg[0]).strip()
                if translated:
                    return translated
        except (requests.RequestException, ValueError, IndexError, TypeError) as exc:
            print(f"Translation attempt {attempt + 1} failed ({type(exc).__name__}).")
        if attempt == 0:
            time.sleep(3)
    TRANSLATE_SERVICE_FAILED = True
    return None

def translate_without_gemini(item):
    """Fallback for non-Sorani news when Gemini is unavailable: free translation, then the same safety checks."""
    if not TRANSLATE_FALLBACK_ENABLED:
        return None
    def clean(value):
        value = re.sub(r"https?://\S+|www\.\S+|@[A-Za-z0-9_]+|#[\w]+", "", str(value or ""))
        value = re.sub(r"<[^>]*>", "", value)
        value = re.sub(r"[ \t]+", " ", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()
    title, body = clean(item.get("title")), clean(item.get("content"))
    source_text = (title + "\n\n" + body).strip() if title else body
    if len(source_text) < 60:
        return None
    if len(source_text) > 2000:
        source_text = source_text[:2000].rsplit(" ", 1)[0]
    translated = translate_to_sorani(source_text)
    if not translated:
        print("Translation fallback unavailable; skipping item.")
        return None
    letters = re.findall(r"[A-Za-z\u0600-\u06FF]", translated)
    sorani = re.findall(SORANI_LETTERS, translated)
    latin = re.findall(r"[A-Za-z]", translated)
    # Reject output that is still mostly foreign text (would mix languages in the channel).
    if not letters or len(sorani) < 5 or len(latin) > 0.25 * len(letters):
        print("Translation fallback rejected: output is not clean Sorani.")
        return None
    t_title, t_body = "", translated
    if title and "\n" in translated:
        t_title, t_body = [part.strip() for part in translated.split("\n", 1)]
    # Translation must be readable Sorani, never a raw Persian/Arabic repost.
    # A conservative heuristic: reject outputs with too few distinctive Sorani letters.
    cleaned = format_without_ai({"title": t_title, "content": t_body})
    if not cleaned:
        return None
    original = source_text.strip()
    output = (cleaned["title"] + "\n" + cleaned["body"]).strip()
    if output == original:
        print("Translation fallback rejected: unchanged source text.")
        return None
    return cleaned


def post_telegram(method, data, files=None, timeout=40):
    """Call a Telegram Bot API method. Returns "ok", "rejected" (HTTP error) or "uncertain" (transport error)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        res = requests.post(url, json=data, timeout=15) if files is None else requests.post(url, data=data, files=files, timeout=timeout)
        if not res.ok:
            # Never print the request URL: it contains the bot token.
            try:
                detail = res.json().get("description", "Unknown Telegram error")
            except ValueError:
                detail = "Non-JSON Telegram response"
            print(f"Telegram rejected {method} (HTTP {res.status_code}): {detail}")
            if res.status_code == 403:
                raise PermissionError("Telegram bot lacks access to the configured destination channel")
            if res.status_code == 429 or res.status_code >= 500:
                return "uncertain"  # flood control or server trouble: retry later, never downgrade the message
            return "rejected"
        return "ok" if res.json().get("ok") is True else "rejected"
    except requests.RequestException as e:
        print(f"Telegram transport error ({type(e).__name__}); delivery status uncertain.")
        return "uncertain"

def publication_source(item):
    """Build attribution from collector metadata, independently of AI output."""
    kind = item.get("source_type")
    name = item.get("source_name") or ""
    source = str(item.get("source") or "")
    if not kind:
        if source.startswith("تێلیگرام"):
            kind = "telegram"
            name = source.removeprefix("تێلیگرام").strip(" ()@")
        elif source.startswith("facebook:"):
            kind = "facebook"
            name = source.split(":", 1)[1]
        else:
            kind = "website"
            name = urlparse(item.get("link") or "").hostname or source
    name = " ".join(str(name).split())[:150] or "نەناسراو"
    prefix = {"telegram": "تلگرامی", "facebook": "پەیجی", "website": "سایتی"}.get(kind, "سایتی")
    return f"{prefix} - {name}"


def publication_credit(item):
    """Show source publication time, or explicitly label the collection time."""
    label = publication_source(item)
    method = (item.get("progress") or {}).get("method")
    method_labels = {
        "gemini": "داڕشتنەوەی جێمێنای",
        "translation": "وەرگێڕاوی ئەیجێنت",
        "source": "خاو لە سەرچاوەکەوە",
    }
    if method in method_labels:
        label += "\n- " + method_labels[method]
    candidates = [
        (item.get("source_published_at"), "کاتی بڵاوکردنەوە لە سەرچاوە"),
        (item.get("fetched_at") or (item.get("progress") or {}).get("received_at"), "کاتی وەرگرتن"),
    ]
    for value, caption in candidates:
        if not value:
            continue
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                continue  # Do not guess the source timezone.
            local = stamp.astimezone(timezone(timedelta(hours=3)))
            return label + "\n" + caption + ": " + local.strftime("%d/%m/%Y - %H:%M") + " (کاتی عێراق)"
        except (ValueError, TypeError, OverflowError):
            continue
    return label + "\nکاتی سەرچاوە بەردەست نییە"


def send_to_telegram(message_text, source_link=None, image_url=None, video_url=None, image_urls=None, source_label=None):
    """هەواڵە داڕێژراوەکە (و ڤیدیۆ یان وێنەکەی ئەگەر هەبێت) دەنێرێت بۆ چەناڵی مەبەست بە بۆتی تێلیگرام."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram tokens are not set. Message printed to console:")
        print(message_text)
        return False

    title = body = ""
    if isinstance(message_text, dict):
        title = message_text.get("title", "").strip()
        body = message_text.get("body", "").strip()
        if not title or not body:
            return False
        full_text = "<b>" + html.escape(title) + "</b>\n\n" + html.escape(body)
    else:
        full_text = html.escape(re.sub(r"</?b>", "", str(message_text), flags=re.IGNORECASE))
    attribution = "\n\n" + html.escape(source_label) if source_label else ""
    full_text += attribution
    if len(full_text) > 4000:
        print("Article too long for Telegram; skipping.")
        return False

    text_payload = {"chat_id": TELEGRAM_CHAT_ID, "text": full_text, "parse_mode": "HTML", "disable_web_page_preview": True}

    def build_caption():
        """Returns (caption, is_short) or None when the article cannot be captioned."""
        if len(full_text) <= 1000:
            return full_text, True
        if not title:
            return None
        return "<b>" + html.escape(title[:900]) + "</b>", False

    def finish(status, method, short):
        """Returns True (delivered), False (uncertain; retry later) or None (rejected; try the next option)."""
        if status == "ok":
            if short:
                return True
            # Telegram captions are limited to 1024 characters: media with the headline, article in a second message.
            return post_telegram("sendMessage", dict(text_payload, text=html.escape(body) + attribution)) == "ok"
        if status == "uncertain":
            return False
        print(f"{method} rejected; trying the next option.")
        return None

    def send_media(method, field, filename, data, content_type, upload_timeout, extra=None):
        built = build_caption()
        if not built:
            return None
        caption, short = built
        payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        payload.update(extra or {})
        return finish(post_telegram(method, payload, {field: (filename, data, content_type)}, upload_timeout), method, short)

    def send_album(photos):
        built = build_caption()
        if not built:
            return None
        caption, short = built
        media, files = [], {}
        for i, (data, content_type) in enumerate(photos):
            entry = {"type": "photo", "media": f"attach://photo{i}"}
            if i == 0:
                entry.update({"caption": caption, "parse_mode": "HTML"})
            media.append(entry)
            files[f"photo{i}"] = (f"news{i}.jpg", data, content_type)
        status = post_telegram("sendMediaGroup", {"chat_id": TELEGRAM_CHAT_ID, "media": json.dumps(media)}, files, 90)
        return finish(status, "sendMediaGroup", short)

    video = download_video(video_url) if video_url else None
    if video:
        data, content_type = video
        extra = {"supports_streaming": "true"}
        info = read_mp4_info(data)
        if info:
            # Without explicit size Telegram may guess the aspect ratio wrong (stretched or cropped video).
            extra.update({"width": str(info[0]), "height": str(info[1])})
            if info[2] > 0:
                extra["duration"] = str(info[2])
        outcome = send_media("sendVideo", "video", "news." + content_type.split("/")[-1], data, content_type, 180, extra)
        if outcome is not None:
            return outcome

    urls = list(image_urls or ([image_url] if image_url else []))[:MAX_ALBUM_IMAGES]
    photos = [p for p in (download_image(u) for u in urls) if p]
    if len(photos) >= 2:
        outcome = send_album(photos)
        if outcome is not None:
            return outcome
    if photos:
        data, content_type = photos[0]
        outcome = send_media("sendPhoto", "photo", "news." + content_type.split("/")[-1].replace("jpeg", "jpg"), data, content_type, 40)
        if outcome is not None:
            return outcome

    return post_telegram("sendMessage", text_payload) == "ok"


DRAIN_TARGET = timedelta(minutes=20)  # the queue should be empty within about this long

def adaptive_interval(pending_count):
    """Spacing between articles shrinks as the queue grows: 5 min for a short queue down to 1 min for a long one."""
    if pending_count <= 1:
        return timedelta(minutes=5)
    seconds = DRAIN_TARGET.total_seconds() / pending_count
    return timedelta(seconds=min(300, max(60, seconds)))


PENDING_MAX_AGE = timedelta(hours=6)   # older news is stale: drop it instead of publishing it late
BLOCKED_MAX_AGE = timedelta(hours=1)   # items that cannot be turned into safe text are retried for an hour at most
RECENT_LIMIT = 30

def prune_pending(state, seen_ids, now=None):
    """Remove stale or permanently blocked items from the queue and mark them as seen. Returns how many were dropped."""
    now = now or datetime.now(timezone.utc)
    kept, dropped = [], 0
    for item in state.get("pending_items", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        progress = item.get("progress") or {}
        try:
            received = datetime.fromisoformat(str(progress.get("received_at") or item.get("fetched_at") or ""))
            age = now - received
        except (ValueError, TypeError):
            age = timedelta(0)
        limit = BLOCKED_MAX_AGE if progress.get("stage") == "blocked_content" else PENDING_MAX_AGE
        if item.get("topics") == []:
            limit = min(limit, timedelta(minutes=editorial.load_policy()["unmatched_max_age_minutes"]))
        if age > limit:
            seen_ids.add(item["id"])
            dropped += 1
        else:
            kept.append(item)
    state["pending_items"] = kept
    return dropped

def merge_pending(state, items, seen_ids):
    """Persist unposted news across runs, including Facebook polling cooldowns."""
    pending = state.get("pending_items", [])
    if not isinstance(pending, list):
        raise RuntimeError("Invalid pending news queue")
    merged = {}
    for item in pending + items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if item["id"] not in seen_ids:
            merged.setdefault(item["id"], item)
    state["pending_items"] = list(merged.values())
    return state["pending_items"]


def source_key(item):
    if item.get("source_type") and item.get("source_name"):
        return str(item["source_type"]) + ":" + str(item["source_name"])
    return str(item.get("source") or publication_source(item))


def choose_pending(items, state, attempted):
    """Least recently served source first; breaking news first within that source."""
    available = [item for item in items if item["id"] not in attempted]
    # Already-prepared Gemini stories always have publication priority.
    gemini_ready = [item for item in available if item.get("prepared_method") == "gemini"]
    if gemini_ready:
        available = gemini_ready
    # Wanted topics first; items outside the policy only run when nothing wanted is waiting.
    wanted = [item for item in available if item.get("topics", ["legacy"])]
    if wanted:
        available = wanted
    if not available:
        return None
    turns = state.get("source_turns", {})
    sources = list(dict.fromkeys(source_key(item) for item in available))
    selected = min(sources, key=lambda key: turns.get(key, 0))
    candidates = [item for item in available if source_key(item) == selected]
    return next((item for item in candidates if is_breaking(item)), candidates[0])


def track_article(item, state, stage, reason="", method=None):
    """Persist per-article progress; titles are taken from the source or prepared text."""
    now = datetime.now(timezone.utc).isoformat()
    progress = item.setdefault("progress", {})
    progress.setdefault("received_at", now)
    progress.update({"stage": stage, "updated_at": now, "reason": reason})
    if method is not None:
        progress["method"] = method
    history = progress.setdefault("history", [])
    if not history or history[-1].get("stage") != stage or history[-1].get("reason") != reason:
        history.append({"stage": stage, "at": now, "reason": reason,
                        "method": progress.get("method", "undecided")})
        del history[:-30]
    save_publication_state(state)


def story_text(prepared):
    """Title and body of a prepared story (dict) or the plain text itself."""
    if isinstance(prepared, dict):
        return str(prepared.get("title", "")) + "\n" + str(prepared.get("body", ""))
    return str(prepared)



def dashboard_media_items(item, title):
    """Build a compact, deduplicated WordPress media manifest from the original source item."""
    media = []
    seen_media_urls = set()

    image_urls = item.get("images") if isinstance(item.get("images"), list) else []
    if item.get("image"):
        image_urls = [item.get("image")] + image_urls

    for image_url in image_urls[:10]:
        media_url = str(image_url or "").strip()
        if media_url.startswith("https://") and media_url not in seen_media_urls:
            media.append({"type": "image", "url": media_url, "alt": title})
            seen_media_urls.add(media_url)

    video_url = str(item.get("video") or item.get("video_url") or "").strip()
    if video_url.startswith("https://") and video_url not in seen_media_urls:
        media.append({"type": "video", "url": video_url, "alt": title})
        seen_media_urls.add(video_url)

    return media


def send_news_to_dashboard(item, prepared_text):
    """Send a Telegram-published article to the configured WordPress archive."""
    if not DASHBOARD_NEWS_API_URL or not DASHBOARD_NEWS_API_KEY:
        return None
    if not isinstance(prepared_text, dict):
        print("WordPress news sync skipped: prepared article is not structured.")
        return False

    title = str(prepared_text.get("title", "")).strip()
    body = str(prepared_text.get("body", "")).strip()
    if not title or not body:
        print("WordPress news sync skipped: title/body missing.")
        return False

    # Keep the Telegram album and video with the WordPress news item.
    media = dashboard_media_items(item, title)

    payload = {
        "id": str(item.get("id", "")).strip(),
        "title": title,
        "body": body,
        "source": str(item.get("source_name") or item.get("source") or "Telegram"),
        "source_url": str(item.get("link") or ""),
        "image_url": str(item.get("image") or (image_urls[0] if image_urls else "")),
        "media": media,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    if not payload["id"]:
        print("WordPress news sync skipped: article id missing.")
        return False

    headers = {
        "X-WM-News-Key": DASHBOARD_NEWS_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    for attempt in range(3):
        try:
            response = requests.post(
                DASHBOARD_NEWS_API_URL,
                headers=headers,
                json=payload,
                timeout=(5, 90),
                allow_redirects=False,
            )
            if response.status_code in (200, 201):
                print("WordPress news sync succeeded.")
                return True
            print(f"WordPress news sync returned HTTP {response.status_code}.")
            if response.status_code < 500 and response.status_code != 429:
                return False
        except requests.RequestException as exc:
            print(f"WordPress news sync attempt {attempt + 1}/3 failed ({type(exc).__name__}).")
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    return False


def retry_pending_dashboard_news(state):
    """Retry durable WordPress sync items independently of Telegram publication."""
    if not DASHBOARD_NEWS_API_URL or not DASHBOARD_NEWS_API_KEY:
        return
    pending = state.setdefault("dashboard_sync_pending", [])
    if not isinstance(pending, list) or not pending:
        return
    remaining = []
    for entry in pending[-100:]:
        item = entry.get("item", {})
        prepared = entry.get("prepared_text", {})
        if not send_news_to_dashboard(item, prepared):
            remaining.append(entry)
    state["dashboard_sync_pending"] = remaining
    save_publication_state(state)

def main():
    if not PUBLISH_ENABLED:
        print("Safe mode: publication disabled until explicitly enabled.")
        return
    started = time.monotonic()
    print("Checking for new articles...")
    seen_ids = load_seen_ids()
    state = load_publication_state()
    retry_pending_dashboard_news(state)
    new_items = fetch_rss_news(seen_ids)
    for ch in TELEGRAM_CHANNELS:
        new_items.extend(fetch_telegram_public_channel(ch, seen_ids))
    new_items.extend(fetch_facebook_via_apify(seen_ids, state))
    dropped = prune_pending(state, seen_ids)
    if dropped:
        print(f"Dropped {dropped} stale or blocked articles from the queue.")
        save_seen_ids(seen_ids)
    pending = merge_pending(state, new_items, seen_ids)
    for queued in pending:
        if "topics" not in queued:
            queued["topics"] = editorial.match_topics(queued.get("title", "") + "\n" + queued.get("content", ""))
    for queued in pending:
        if not queued.get("progress"):
            track_article(queued, state, "received")
            track_article(queued, state, "waiting_turn", "چاوەڕوانی نۆرەی سەرچاوە")
    save_publication_state(state)
    print(f"Found {len(new_items)} new items.")
    print(f"Pending queue: {len(pending)} articles.")

    # Spread publications within a five-minute trigger window. Leave time for
    # telemetry and the state commit; never increase Gemini calls with backlog.
    processed_count = 0
    gemini_attempted = False
    attempted = set()
    while processed_count < 5 and time.monotonic() - started < 240:
        item = choose_pending(pending, state, attempted)
        if item is None:
            break
        track_article(item, state, "selected", "نۆرەی ئەم سەرچاوەیە هاتووە")
        raw_fp = editorial.fingerprint(item.get("title", "") + "\n" + item["content"], "raw")
        dup = editorial.find_duplicate(raw_fp, state, "raw")
        if dup:
            print(f"Duplicate of an already published story ({dup.get('title', '')[:60]}); skipping.")
            track_article(item, state, "duplicate", "duplicate of " + dup.get("title", "")[:60])
            seen_ids.add(item["id"])
            pending.remove(item)
            save_seen_ids(seen_ids)
            save_publication_state(state)
            continue
        interval = adaptive_interval(len(pending))
        now = datetime.now(timezone.utc)
        last = state.get("last_published_at") or state.get("last_normal_at")
        if last:
            previous = datetime.fromisoformat(last)
            if previous.tzinfo is None:
                raise RuntimeError("Invalid last publication timestamp")
            wait_seconds = max(0, (previous + interval - now).total_seconds())
            if wait_seconds:
                item["progress"]["eligible_at"] = (previous + interval).isoformat()
                track_article(item, state, "waiting_slot", "چاوەڕوانی کاتی بڵاوکردنەوە؛ ئامادەکردنی دەق دوای نۆرە")
            # A five-minute interval is handled by the next scheduled run.
            if wait_seconds and (interval >= timedelta(minutes=5) or
                                 time.monotonic() - started + wait_seconds >= 240):
                print("Waiting for next publishing slot; queue saved.")
                break
            while wait_seconds > 0:
                time.sleep(min(wait_seconds, 30))
                wait_seconds = max(0, (previous + interval - datetime.now(timezone.utc)).total_seconds())

        attempted.add(item["id"])
        print(f"Processing item from {item['source']}...")
        now = datetime.now(timezone.utc)
        rewritten_text = item.get("prepared_text")
        method = item.get("prepared_method")
        if not rewritten_text and not gemini_attempted and gemini_slot_available(state, now):
            track_article(item, state, "waiting_gemini", "هەڵبژێردراوە بۆ داڕشتنەوە بە Gemini", "gemini")
            gemini_attempted = True
            state["last_gemini_at"] = now.isoformat()
            save_publication_state(state)
            try:
                track_article(item, state, "processing_gemini", "داواکاری بۆ Gemini نێردراوە", "gemini")
                rewritten_text = rewrite_with_gemini((item.get("title", "") + "\n" + item["content"]).strip())
                if rewritten_text:
                    method = "gemini"
            except RuntimeError as exc:
                if str(exc) != "GEMINI_QUOTA_EXCEEDED":
                    raise
                state["quota_blocked_until"] = (datetime.now(timezone.utc) + QUOTA_BACKOFF).isoformat()
                save_publication_state(state)
                print("Gemini quota exhausted; using safe non-AI fallback.")
        if not rewritten_text:
            track_article(item, state, "processing_openrouter", "Gemini نەیتوانی دابڕێژێتەوە؛ هەوڵی OpenRouter", "openrouter")
            rewritten_text = rewrite_with_openrouter((item.get("title", "") + "\n" + item["content"]).strip())
            if rewritten_text:
                method = "openrouter"
        if not rewritten_text:
            # Never post foreign-language source text unchanged.
            track_article(item, state, "formatting_source", "هەردوو AI شکستیان هێنا؛ پشکنینی کوردی", "source")
            rewritten_text = format_without_ai(item)
            if rewritten_text:
                method = "source"
            else:
                track_article(item, state, "translating", "وەرگێڕانی جێگرەوە بەبێ AI", "translation")
                blocked = state.get("translate_blocked_until")
                if blocked and datetime.now(timezone.utc) < datetime.fromisoformat(blocked):
                    rewritten_text = None
                else:
                    rewritten_text = translate_without_gemini(item)
                    if TRANSLATE_SERVICE_FAILED:
                        globals()["TRANSLATE_SERVICE_FAILED"] = False
                        state["translate_blocked_until"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
                        print("Translation service unreachable; pausing it for 30 minutes.")
                if rewritten_text:
                    method = "translation"
        if not rewritten_text:
            track_article(item, state, "blocked_content", "دەقێکی گونجاو ئامادە نەبوو؛ لە ڕەنی داهاتوودا هەوڵ دەدرێتەوە")
            print("No safe publishable text; keeping item pending.")
            continue
        final_fp = editorial.fingerprint(story_text(rewritten_text), "final")
        dup = editorial.find_duplicate(final_fp, state, "final")
        if dup:
            print(f"Duplicate of an already published story ({dup.get('title', '')[:60]}); skipping.")
            track_article(item, state, "duplicate", "duplicate of " + dup.get("title", "")[:60])
            seen_ids.add(item["id"])
            pending.remove(item)
            save_seen_ids(seen_ids)
            save_publication_state(state)
            continue
        item["prepared_text"] = rewritten_text
        item["prepared_method"] = method or "unknown"
        track_article(item, state, "ready", "دەق ئامادەی ناردنە")
        track_article(item, state, "sending", "ناردن بۆ تێلیگرام")
        success = send_to_telegram(rewritten_text, image_url=item.get("image"),
                                   video_url=item.get("video"), image_urls=item.get("images"),
                                   source_label=publication_credit(item))
        if not success:
            track_article(item, state, "delivery_failed", "ناردن پشتڕاست نەکراوە؛ هەواڵەکە پارێزراوە")
            print("Telegram publication failed; keeping item for retry.")
            break
        track_article(item, state, "published", "تێلیگرام بڵاوکردنەوەکەی پشتڕاست کردەوە")
        dashboard_sync_result = send_news_to_dashboard(item, rewritten_text)
        if dashboard_sync_result is False:
            # Durable outbox: WordPress may be offline while Telegram succeeds.
            sync_pending = state.setdefault("dashboard_sync_pending", [])
            sync_pending = [entry for entry in sync_pending if entry.get("id") != item["id"]]
            sync_pending.append({
                "id": item["id"],
                "item": item,
                "prepared_text": rewritten_text,
                "queued_at": datetime.now(timezone.utc).isoformat(),
            })
            state["dashboard_sync_pending"] = sync_pending[-100:]
            save_publication_state(state)
        recent = state.setdefault("recent_articles", [])
        recent.append({"id": item["id"], "title": rewritten_text.get("title", ""),
                       "source": publication_source(item), "progress": item["progress"]})
        del recent[:-RECENT_LIMIT]
        editorial.remember(state, raw_fp, "raw", rewritten_text.get("title", "") if isinstance(rewritten_text, dict) else "")
        editorial.remember(state, final_fp, "final", rewritten_text.get("title", "") if isinstance(rewritten_text, dict) else "")
        seen_ids.add(item["id"])
        pending.remove(item)
        stamp = datetime.now(timezone.utc).isoformat()
        state["last_published_at"] = stamp
        state["last_normal_at"] = stamp
        turns = state.setdefault("source_turns", {})
        turns[source_key(item)] = max(turns.values(), default=0) + 1
        save_seen_ids(seen_ids)
        save_publication_state(state)
        processed_count += 1
        print("Successfully published!")

    save_seen_ids(seen_ids)
    save_publication_state(state)
    print(f"Completed run. Processed {processed_count} articles.")


if __name__ == "__main__":
    main()
