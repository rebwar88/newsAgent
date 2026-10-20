# ڕێبەری دامەزراندنی وۆرکفلۆی هەواڵ بە گیت‌هەب (تێچووی سفر - $0)

ئەم پڕۆژەیە هەموو ٥ خولەک جارێک لە نێوان کاتژمێر ٩ی بەیانی تا ٣ی نیوەڕۆ هەواڵ لە سەرچاوەکان وەردەگرێت، بە مۆدێلی Gemini دایاندەڕێژێتەوە و بۆ کەناڵی تێلیگرامی دەنێرێت.

---

### هەنگاوی ١: دروستکردنی کۆگای گیت‌هەب (Repository)
1. بچۆ ناو هەژماری [GitHub](https://github.com/) و کۆگایەکی نوێ دروست بکە (Create a new repository).
2. دەتوانیت بیکەیت بە **Private** یان **Public** (ڕەچاوی بکە: لە کۆگای تایبەتدا مانگانە ٢,٠٠٠ خولەکی Actions بە بێبەرامبەرە کە تەواو بەسە، لە کۆگای گشتیدا (Public) خولەکەکان تەواو بێ‌سنوورە).
3. ئەم ٤ فایلە لەناو کۆگاکەت دابنێ:
   - `main.py` (کۆدی سەرەکی پڕۆگرامەکە)
   - `requirements.txt` (کتێبخانە پێویستەکان)
   - `seen_urls.json` (تەنها جووت کەوانەی بەتاڵ `[]` تێدا بێت)
   - `.github/workflows/news_bot.yml` (فایلی کاتدانەری خۆکاری گیت‌هەب)

---

### هەنگاوی ٢: وەرگرتنی کلیلە بێبەرامبەرەکان (API Keys)
1. **Google Gemini API Key:**
   - بچۆ ناو [Google AI Studio](https://aistudio.google.com/).
   - کلیلی **Get API Key** دابگرە و کۆپی بکە (تەواو خۆڕاییە).
2. **Telegram Bot Token:**
   - لە تێلیگرام بچۆ لای [BotFather](https://t.me/BotFather) و بنووسە `/newbot` تا بۆتێک دروست بکەیت و تۆکنەکەی وەربگریت.
3. **Telegram Chat ID:**
   - بۆتەکە بکە بە ئەدمین (Admin) لە چەناڵەکەتدا.
   - ئایدی چەناڵەکەت (بۆ نموونە `@channel_name` یان ئایدی ژمارەیی وەک `-1001234567890`) وەک چات ئایدی دابنێ.

---

### هەنگاوی ٣: پاشەکەوتکردنی کلیلەکان لە گیت‌هەب (Secrets)
بۆ ئەوەی کلیلەکانت بە پارێزراوی بمێننەوە:
1. لە کۆگاکەت لە گیت‌هەب بچۆ بەشی **Settings** > **Secrets and variables** > **Actions**.
2. دوگمەی **New repository secret** دابگرە و ئەم ٣ دانەیە زیاد بکە:
   - ناو: `GEMINI_API_KEY` | بەها: کلیلی گووگڵ جێمینای
   - ناو: `TELEGRAM_BOT_TOKEN` | بەها: تۆکنی بۆتەکەی تێلیگرام
   - ناو: `TELEGRAM_CHAT_ID` | بەها: ئایدی یان ناوی چەناڵەکەت

---

### هەنگاوی ٤: بەخشینی مۆڵەتی پاشەکەوتکردن بە گیت‌هەب
بۆ ئەوەی فایلی `seen_urls.json` لە دوای هەر کارپێکردنێک نوێ بکرێتەوە و ڕێگری لە هەواڵی دووبارە بکرێت:
1. لە کۆگاکەت بچۆ **Settings** > **Actions** > **General**.
2. لە خوارەوە لە ژێر بەشی **Workflow permissions**، بژاردەی **Read and write permissions** هەڵبژێرە و Save بکە.

---

### تاقیکردنەوەی سەرکەوتووانە (Manual Test)
دەتوانیت هەر کاتێک بتەوێت بە دەست تاقی بکەیتەوە:
- بچۆ پەڕەی **Actions** لە گیت‌هەب.
- کلیک لەسەر وۆرکفلۆی **Automated News Workflow** بکە.
- دوگمەی **Run workflow** دابگرە.

---

### کارپێکردنی هەر ١٥ خولەک جارێک بە cron-job.org

گیت‌هەب کاتی `schedule` بە دەگمەن دوا دەخات یان تێدەپەڕێنێت، بۆیە بۆتەکە بە cron-job.org دەست پێدەکرێت:

1. لە گیت‌هەب تۆکنێکی Fine-grained دروست بکە، تەنها بۆ ئەم کۆگایە، بە مۆڵەتی **Actions: Read and write**.
2. لە [cron-job.org](https://cron-job.org) هەژمار دروست بکە و Cronjobێکی نوێ زیاد بکە:
   - URL: `https://api.github.com/repos/rebwar88/newsAgent/actions/workflows/news_bot.yml/dispatches`
   - Schedule: هەر ١٥ خولەک
   - Request method: `POST`
   - Request body: `{"ref":"main"}`
   - Headers:
     - `Authorization: Bearer <تۆکنەکەت>`
     - `Accept: application/vnd.github+json`
     - `Content-Type: application/json`
3. پاش تاقیکردنەوە، وەڵامی سەرکەوتوو `204` ـە.

تۆکنەکە تەنها لە cron-job.org دابنێ و هەرگیز لە کۆددا مەینووسە.
