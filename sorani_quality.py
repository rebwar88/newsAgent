"""Conservative, deterministic language checks for Kurdish Sorani news publication.

This module does not translate or guess facts. When a result is uncertain, it
returns a reason to retry translation instead of publishing mixed-language text.
"""
import re
import unicodedata
from difflib import SequenceMatcher

SORANI_CHARS = re.compile(r"[ئۆێەڕڵڤ]")
ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF]")
SCRIPT_WORDS = re.compile(r"[\u0600-\u06FF]+")
LATIN_WORDS = re.compile(r"[A-Za-z]{3,}")

# These are grammatical clues, not a dictionary of foreign proper names.
# Words common in Sorani as well (e.g. دولت, ایران, بازار) are deliberately omitted.
PERSIAN_STRONG = {
    "است", "هست", "نیست", "بود", "بودند", "شد", "شده",
    "خواهد", "دارند", "دارد", "گفت", "گفته", "میشود", "میکند",
    "میتواند", "نمی", "می", "برای", "درباره", "دربارۀ",
    "بهای", "قیمت", "امروز", "دیروز", "فردا", "بیایند",
    "مقامات", "روحانیون", "خبرهای", "ضرورت", "نیازمند",
}
PERSIAN_WEAK = {"به", "از", "در", "که", "را", "این", "آن", "های", "همه", "با"}
PERSIAN_TITLE_MARKERS = {"بهای", "قیمت", "برای", "درباره", "دربارۀ", "خبرهای", "روحانیون", "مقامات", "امروز"}
ARABIC_MARKERS = {"أعلنت", "اعلنت", "وقال", "قال", "الذي", "التي", "هذا", "هذه", "على", "إلى", "الى", "في", "أن", "من"}


def _normalize(value):
    return unicodedata.normalize("NFKC", str(value or "")).replace("ي", "ی").replace("ك", "ک")


def _words(value):
    return SCRIPT_WORDS.findall(_normalize(value))


def _persian_residue(value, headline=False):
    words = _words(value)
    if not words:
        return False
    strong = sum(word in PERSIAN_STRONG for word in words)
    weak = sum(word in PERSIAN_WEAK for word in words)
    arabic = sum(word in ARABIC_MARKERS for word in words)
    if arabic >= 3 or (headline and arabic >= 2):
        return True
    if headline:
        return (
            any(word in PERSIAN_TITLE_MARKERS for word in words)
            or strong >= 2
            or (strong >= 1 and weak >= 2)
        )
    return (
        strong >= 3
        or (strong >= 2 and weak >= 2)
        or (strong >= 1 and weak >= 4 and (strong + weak) * 5 >= len(words))
    )


def article_issue(title, body, source_text=""):
    """Return a reason to block unsafe publication, otherwise None.

    This is intentionally conservative: a rejected article stays in the queue
    for a better translation instead of being sent with Persian fragments.
    """
    title = str(title or "").strip()
    body = str(body or "").strip()
    if len(title) < 8 or len(body) < 60:
        return "incomplete headline or article"
    if len(_words(title)) < 3:
        return "headline has too few meaningful words"
    if len(_words(body)) < 9:
        return "article content is too fragmentary"
    if re.search(r"[:：]\s*[0-9٠-٩۰-۹]+\s*$", title):
        return "headline ends in an isolated number"
    if len(SORANI_CHARS.findall(title + body)) < 5 or len(SORANI_CHARS.findall(body)) < 3:
        return "insufficient Sorani orthography"
    if _persian_residue(title, headline=True):
        return "Persian or Arabic grammatical residue in headline"
    if _persian_residue(body):
        return "Persian or Arabic grammatical residue in article"
    script_count = len(ARABIC_SCRIPT.findall(title + body))
    latin_count = len(re.findall(r"[A-Za-z]", title + body))
    if script_count and latin_count > script_count * 0.18 and len(LATIN_WORDS.findall(title + body)) >= 3:
        return "excessive untranslated Latin-script text"
    source = _normalize(source_text).strip()
    if len(source) >= 60 and (_persian_residue(source) or len(SORANI_CHARS.findall(source)) < 5):
        original = re.sub(r"\s+", " ", source)[:1600]
        finished = re.sub(r"\s+", " ", _normalize(title + "\n" + body))[:1600]
        if SequenceMatcher(None, original, finished, autojunk=True).ratio() > 0.84:
            return "translation too similar to foreign-language source"
    return None
