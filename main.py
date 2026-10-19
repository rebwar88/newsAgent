import os
import json
import hashlib
import html
import requests
import feedparser
from bs4 import BeautifulSoup

# ================= Configuration =================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
PUBLISH_ENABLED = os.getenv("PUBLISH_ENABLED", "false").lower() == "true"

SEEN_FILE = "seen_urls.json"
MAX_ITEMS_PER_RUN = 3  # To avoid spamming and respect API rate limits

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
        "تۆ ڕۆژنامەنووسێکی پیشەگەریت. دەقی ئەم هەواڵە بە شێوازێکی ڕۆژنامەوانیی پاراو و بە زمانی کوردیی سۆرانی دابڕێژەرەوە.\n"
        "یاساکانی داڕشتنەوە:\n"
        "١. سەردێڕێکی سەرنجڕاکێش و بەهێز لە دێڕی یەکەم بنووسە بە پیتی تۆخ (<b>سەردێڕ</b>).\n"
        "٢. دەقی هەواڵەکە لە یەک تا دوو پەرەگرافی کورت و پوخت ڕێکبخە.\n"
        "٣. بە تەواوی بێلایەن بە و هیچ بیروڕایەک لە خۆتەوە زیاد مەکە.\n"
        "٤. لە کۆتایی دوو تا سێ هاشتاگی پەیوەندیدار دابنێ.\n"
        "٥. هیچ پێشەکی یان لێدوانێک مەنووسە، تەنها دەقی داڕێژراوی هەواڵەکە بنێرە."
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
            "maxOutputTokens": 600
        }
    }

    try:
        endpoint = f"{base_url}/models/{model}:generateContent"
        response = requests.post(endpoint, headers=headers, json=payload, timeout=25)
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
            response = requests.post(f"{base_url}/models/{model}:generateContent", headers=headers, json=payload, timeout=25)
        if not response.ok:
            print(f"Gemini API returned HTTP {response.status_code}: {response.text[:300]}")
            return None
        res_json = response.json()
        rewritten = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
        return rewritten or None
    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return None

def send_to_telegram(message_text, source_link):
    """هەواڵە داڕێژراوەکە دەنێرێت بۆ چەناڵی مەبەست بە بۆتی تێلیگرام."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram tokens are not set. Message printed to console:")
        print(message_text)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Escape external text and source URL for Telegram HTML mode.
    safe_text = html.escape(message_text).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    if source_link:
        full_text = f"{safe_text}\n\n🔗 <a href='{html.escape(source_link, quote=True)}'>سەرچاوە</a>"
    else:
        full_text = safe_text
    if len(message_text) > 3900:
        print("Message too long; skipping.")
        return False

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": full_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
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
    new_items = []

    # ١. هێنانی هەواڵ لە RSS
    new_items.extend(fetch_rss_news(seen_ids))

    # ٢. هێنانی پەیام لە تێلیگرام
    for ch in TELEGRAM_CHANNELS:
        new_items.extend(fetch_telegram_public_channel(ch, seen_ids))

    print(f"Found {len(new_items)} new items.")

    # ڕێکخستنی سنووری پۆستکردن لە هەر سوڕانەوەیەکدا
    processed_count = 0
    for item in new_items[:MAX_ITEMS_PER_RUN]:
        print(f"Processing item from {item['source']}...")
        
        # داڕشتنەوە بە ژیری دەستکرد
        rewritten_text = rewrite_with_gemini(item["content"])
        if not rewritten_text:
            print("AI rewrite failed; keeping item for retry.")
            continue
        
        # بڵاوکردنەوە لە تێلیگرام
        success = send_to_telegram(rewritten_text, item["link"])
        if success:
            seen_ids.add(item["id"])
            processed_count += 1
            print("Successfully published!")
        else:
            print("Telegram publication failed; keeping item for retry.")

    # پاشەکەوتکردنی هەواڵە بینراوەکان بۆ ڕێگری لە دووبارەبوونەوە
    save_seen_ids(seen_ids)
    print(f"Completed run. Processed {processed_count} articles.")

if __name__ == "__main__":
    main()
