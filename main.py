import os
import json
import hashlib
import html
import re
from datetime import datetime, timezone, timedelta
import requests
import time
import feedparser
from bs4 import BeautifulSoup
from facebook_public import fetch_facebook_public_posts, discover_public_page_posts, FACEBOOK_PAGE_SHARE_URLS

# ================= Configuration =================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
PUBLISH_ENABLED = os.getenv("PUBLISH_ENABLED", "false").lower() == "true"

SEEN_FILE = "seen_urls.json"
MAX_ITEMS_PER_RUN = 1
GEMINI_INTERVAL = timedelta(minutes=10)  # shorter than the 15-min trigger so Gemini is not skipped by timing jitter
QUOTA_BACKOFF = timedelta(minutes=30)
PUBLISH_STATE_FILE = "publication_state.json"
# Comma-separated public Facebook POST permalinks; no login or Meta API required.
FACEBOOK_PUBLIC_POST_URLS = [u.strip() for u in os.getenv("FACEBOOK_PUBLIC_POST_URLS", "").split(",") if u.strip()]
NORMAL_INTERVAL = timedelta(minutes=12)  # shorter than the 15-min trigger so one article is published per trigger

# لیستی فیدی ماڵپەڕەکان (RSS)
RSS_FEEDS = [
    # نموونە: لینکەکانی خۆت لێرە دابنێ
    # "https://www.rudaw.net/sorani/rss",
    # "https://nrttv.com/feed/",
]

# لیستی چەناڵە گشتییەکانی تێلیگرام (تەنها ناوی بەکارهێنەر/username بێ @)
TELEGRAM_CHANNELS = [
    "brakingnews24",
    "ClashReport"
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
                        "title": title,
                        "content": clean_text or title,
                        "link": link
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
                        "title": "",
                        "content": content,
                        "link": f"https://t.me/{data_post}" if data_post else ""
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
        "تۆ سەرنووسەری پیشەگەری هەواڵ بە کوردیی سۆرانی. تەنها JSON بە دوو خانەی title و body بگەڕێنەوە. "
        "title سەردێڕێکی ڕوون و کورت بێت. body هەواڵەکە بە دوو تا سێ پەرەگرافی تەواو بێت. "
        "هەموو زانیارییە گرنگەکانی سەرچاوە بپارێزە، بەڵام هیچ ڕاستییەک یان وتەیەک زیاد مەکە. "
        "بانگەشە پشتڕاستنەکراوەکان بخە پاڵ سەرچاوەکە. هیچ URL، لینک، هاشتاگ، HTML، Markdown یان @username مەنووسە."
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
        return {"title": title, "body": body}
    except RuntimeError as e:
        if str(e) == "GEMINI_QUOTA_EXCEEDED":
            raise
        print(f"Error calling Gemini: {e}")
        return None
    except Exception as e:
        print(f"Error calling Gemini: {e}")
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
    sorani_chars = re.findall(r"[ئۆێەڕڵڤپچژگکق]", title + body)
    if len(sorani_chars) < 8 or len(body) < 60:
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


def send_to_telegram(message_text, source_link=None):
    """هەواڵە داڕێژراوەکە دەنێرێت بۆ چەناڵی مەبەست بە بۆتی تێلیگرام."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram tokens are not set. Message printed to console:")
        print(message_text)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    if isinstance(message_text, dict):
        title = message_text.get("title", "").strip()
        body = message_text.get("body", "").strip()
        if not title or not body:
            return False
        full_text = "<b>" + html.escape(title) + "</b>\n\n" + html.escape(body)
    else:
        full_text = html.escape(re.sub(r"</?b>", "", str(message_text), flags=re.IGNORECASE))
    if len(full_text) > 4000:
        print("Article too long for Telegram; skipping.")
        return False

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": full_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        res = requests.post(url, json=payload, timeout=15)
        if not res.ok:
            # Never print the request URL: it contains the bot token.
            try:
                detail = res.json().get("description", "Unknown Telegram error")
            except ValueError:
                detail = "Non-JSON Telegram response"
            print(f"Telegram rejected publication (HTTP {res.status_code}): {detail}")
            if res.status_code == 403:
                raise PermissionError("Telegram bot lacks access to the configured destination channel")
            return False
        return res.json().get("ok") is True
    except requests.RequestException as e:
        print(f"Telegram transport error ({type(e).__name__}); delivery status uncertain.")
        return False

def main():
    if not PUBLISH_ENABLED:
        print("Safe mode: publication disabled until explicitly enabled.")
        return
    print("Checking for new articles...")
    seen_ids = load_seen_ids()
    state = load_publication_state()
    new_items = []

    # ١. هێنانی هەواڵ لە RSS
    new_items.extend(fetch_rss_news(seen_ids))

    # ٢. هێنانی پەیام لە تێلیگرام
    for ch in TELEGRAM_CHANNELS:
        new_items.extend(fetch_telegram_public_channel(ch, seen_ids))

    # ٣. تەنها لینکە گشتییەکانی پۆستی Facebook کە بەبێ چوونەژوورەوە دەقەکەیان بەردەستە
    # Facebook collection temporarily paused while Telegram delivery is stabilized.

    print(f"Found {len(new_items)} new items.")

    # ڕێکخستنی سنووری پۆستکردن لە هەر سوڕانەوەیەکدا
    processed_count = 0
    new_items.sort(key=lambda item: not is_breaking(item))
    gemini_attempted = False
    for item in new_items:
        if processed_count >= MAX_ITEMS_PER_RUN:
            break
        breaking = is_breaking(item)
        if not breaking and not normal_slot_available(state, datetime.now(timezone.utc)):
            print("Waiting for next normal-news publishing slot.")
            continue
        print(f"Processing item from {item['source']}...")
        
        now = datetime.now(timezone.utc)
        rewritten_text = None
        if not gemini_attempted and gemini_slot_available(state, now):
            gemini_attempted = True
            state["last_gemini_at"] = now.isoformat()
            save_publication_state(state)
            try:
                rewritten_text = rewrite_with_gemini((item.get("title", "") + "\n" + item["content"]).strip())
            except RuntimeError as exc:
                if str(exc) == "GEMINI_QUOTA_EXCEEDED":
                    state["quota_blocked_until"] = (datetime.now(timezone.utc) + QUOTA_BACKOFF).isoformat()
                    save_publication_state(state)
                    print("Gemini quota exhausted; using safe non-AI fallback.")
                else:
                    raise
        else:
            print("Gemini cooldown active; trying safe non-AI fallback.")
        if not rewritten_text:
            rewritten_text = format_without_ai(item)
            if rewritten_text:
                print("Using non-AI Sorani formatting fallback.")
        if not rewritten_text:
            print("No safe publishable text; checking next item.")
            continue

        # بڵاوکردنەوە لە تێلیگرام
        success = send_to_telegram(rewritten_text)
        if success:
            seen_ids.add(item["id"])
            if not breaking:
                state["last_normal_at"] = datetime.now(timezone.utc).isoformat()
            save_seen_ids(seen_ids)
            save_publication_state(state)
            processed_count += 1
            print("Successfully published!")
        else:
            print("Telegram publication failed; keeping item for retry.")
        break  # Only one Gemini request per run, including failures.

    # پاشەکەوتکردنی هەواڵە بینراوەکان بۆ ڕێگری لە دووبارەبوونەوە
    save_seen_ids(seen_ids)
    save_publication_state(state)
    print(f"Completed run. Processed {processed_count} articles.")

if __name__ == "__main__":
    main()
