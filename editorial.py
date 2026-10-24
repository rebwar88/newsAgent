"""Editorial policy: topic matching (priority) and duplicate detection across sources and languages."""
import hashlib
import json
import os
import re
import unicodedata
import zlib
from datetime import datetime, timedelta, timezone

POLICY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "editorial_policy.json")
DEFAULT_POLICY = {"unmatched_max_age_minutes": 30,
                  "duplicate": {"window_hours": 48, "jaccard": 0.45, "overlap": 0.6, "overlap_without_numbers": 0.75},
                  "topics": {}}
MAX_FINGERPRINTS = 120
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_policy_cache = None


def load_policy():
    global _policy_cache
    if _policy_cache is None:
        try:
            with open(POLICY_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            policy = dict(DEFAULT_POLICY)
            policy.update({k: v for k, v in loaded.items() if k in DEFAULT_POLICY})
            policy["duplicate"] = {**DEFAULT_POLICY["duplicate"], **loaded.get("duplicate", {})}
            _policy_cache = policy
        except (OSError, ValueError, AttributeError):
            print("Editorial policy unreadable; using defaults.")
            _policy_cache = DEFAULT_POLICY
    return _policy_cache


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text or "")).lower().translate(_DIGITS)
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک").replace("ۀ", "ه").replace("ة", "ە")
    text = re.sub(r"[\u064B-\u065F\u0670\u200c\u200d]", "", text)
    text = re.sub(r"https?://\S+|www\.\S+|@\w+|#\w+", " ", text)
    return re.sub(r"[^\w\s$]", " ", text)


def match_topics(text):
    """Names of the policy topics the text belongs to (empty list = not a wanted topic)."""
    norm = normalize(text)
    def has(words):
        return any(normalize(w).strip() in norm for w in words)
    matched = []
    for name, spec in load_policy().get("topics", {}).items():
        if ("any" in spec and has(spec["any"])) or ("all" in spec and all(has(group) for group in spec["all"])):
            matched.append(name)
    return matched


def fingerprint(text, kind="final"):
    """Compact bottom-k sketch of a text. kind "raw" (source language) keeps less data than "final" (Sorani)."""
    tokens = normalize(text).split()
    grams = sorted({zlib.crc32(" ".join(tokens[i:i + 3]).encode("utf-8")) for i in range(max(0, len(tokens) - 2))})
    fp = {"k": kind, "h": hashlib.sha1(" ".join(tokens).encode("utf-8")).hexdigest()[:16], "g": grams[:30 if kind == "raw" else 40]}
    if kind == "final":
        fp["w"] = sorted({zlib.crc32(t.encode("utf-8")) for t in tokens if len(t) >= 4})[:40]
        fp["n"] = sorted({t for t in tokens if t.isdigit() and len(t) >= 2})[:12]
    return fp


def similar(a, b, cfg=None):
    cfg = cfg or load_policy()["duplicate"]
    if a["h"] == b["h"]:
        return True
    ga, gb = set(a["g"]), set(b["g"])
    if ga and gb and len(ga & gb) / len(ga | gb) >= cfg["jaccard"]:
        return True
    if "w" not in a or "w" not in b:
        return False
    wa, wb = set(a["w"]), set(b["w"])
    if len(wa) < 6 or len(wb) < 6:
        return False
    overlap = len(wa & wb) / min(len(wa), len(wb))
    na, nb = set(a["n"]), set(b["n"])
    if na and nb:
        return overlap >= cfg["overlap"] and bool(na & nb)
    return overlap >= cfg["overlap_without_numbers"]


def find_duplicate(fp, state, kind="final", now=None):
    """Return the stored entry of the same kind that fp duplicates, or None."""
    now = now or datetime.now(timezone.utc)
    cfg = load_policy()["duplicate"]
    limit = timedelta(hours=cfg["window_hours"])
    for entry in reversed(state.get("fingerprints", [])):
        try:
            if entry.get("k") != kind or now - datetime.fromisoformat(entry["t"]) > limit:
                continue
            if similar(fp, entry, cfg):
                return entry
        except (KeyError, ValueError, TypeError):
            continue
    return None


def remember(state, fp, kind, title="", now=None):
    now = now or datetime.now(timezone.utc)
    entries = state.setdefault("fingerprints", [])
    entries.append({**fp, "k": kind, "t": now.isoformat(), "title": str(title)[:80]})
    limit = timedelta(hours=load_policy()["duplicate"]["window_hours"])
    entries[:] = [e for e in entries if _fresh(e, now, limit)][-MAX_FINGERPRINTS:]


def _fresh(entry, now, limit):
    try:
        return now - datetime.fromisoformat(entry["t"]) <= limit
    except (KeyError, ValueError, TypeError):
        return False
