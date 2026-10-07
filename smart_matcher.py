"""
smart_matcher.py — Intelligent Phonetic, Colloquial, and Fuzzy Story Matching
Handles Hinglish phonetic variations, noise removal, typos, and token alignment.
"""

import re
from rapidfuzz import fuzz

# Common story search noise phrases (suffixes/prefixes/qualifiers)
NOISE_PATTERNS = [
    r"\ball\s+episodes?\b",
    r"\bfull\s+episodes?\b",
    r"\ball\s+eps?\b",
    r"\bepisodes?\s*\d*\b",
    r"\beps?\s*\d*\b",
    r"\bseason\s*\d*\b",
    r"\bpart\s*\d*\b",
    r"\bfull\s+story\b",
    r"\bcomplete\s+story\b",
    r"\bfull\s+audiobook\b",
    r"\baudiobook\b",
    r"\bcomplete\s+series\b",
    r"\bseries\b",
    r"\bin\s+hindi\b",
    r"\bhindi\b",
    r"\bpocket\s*fm\b",
    r"\bkuku\s*fm\b",
    r"\bkuku\s*tv\b",
    r"\bheadfone\b",
    r"\beight\s*fm\b",
    r"\bpratilipi\b",
    r"\bkahani\b",
    r"\bstory\b",
]

# Common Hinglish colloquial spelling variants mapped to standard canonical forms
HINGLISH_VARIANTS = {
    "me": "main",
    "mein": "main",
    "mai": "main",
    "vha": "wahan",
    "wha": "wahan",
    "waha": "wahan",
    "vahan": "wahan",
    "yha": "yahan",
    "yaha": "yahan",
    "jha": "jahan",
    "jaha": "jahan",
    "chouhan": "chauhan",
    "chowhan": "chauhan",
    "commandar": "commander",
    "parimodial": "primordial",
    "primidal": "primordial",
    "parimordiyal": "primordial",
    "dr": "doctor",
    "dr.": "doctor",
    "imortal": "immortal",
    "warior": "warrior",
}

# Stop words that shouldn't match stories on their own
STOP_WORDS = {
    "hai", "hain", "kya", "kar", "karo", "raha", "rahi", "ho", "bhai", "yaar",
    "suno", "bolo", "batao", "mujhe", "tum", "aap", "kaise", "kaisi", "the",
    "tha", "thi", "aur", "and", "the", "for", "with", "this", "that", "how",
    "what", "are", "you", "tell", "give", "send", "bhejo", "chahiye",
    "please", "plz", "link", "de", "do", "ki", "ka", "ke", "ko", "se",
    "par", "ek", "yeh", "woh", "wo", "hi", "hello", "hey", "ok", "acha",
    "achha", "dijiye", "bataiye", "bhej do", "dena", "sunna", "sunni",
    "padhna", "padhni"
}

# Generic chatter phrases that must never trigger story matches
GENERIC_CHATTER = {
    "this is paid", "is this paid", "this is paid story", "paid story",
    "free story", "ok", "bro", "nhi", "thanks", "thank you", "hello",
    "hi", "hey", "good morning", "good night", "kya haal hai", "kaise ho",
    "paid available", "bhai", "story", "kya", "hai", "haan", "nahi"
}


def clean_noise(text: str) -> str:
    """Removes platform tags, episode counts, audio noise words, and punctuation."""
    if not text:
        return ""
    cleaned = str(text).strip()
    for pattern in NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_hinglish(text: str) -> str:
    """Normalizes colloquial Hinglish words to standard spelling."""
    if not text:
        return ""
    words = str(text).lower().split()
    norm_words = [HINGLISH_VARIANTS.get(w, w) for w in words]
    return " ".join(norm_words)


def score_match(query: str, target: str) -> float:
    """
    Computes a smart token-aligned similarity score (0.0 - 100.0) between query and target.
    Handles typos, word transposition, extra noise words, and phonetic variants.
    """
    if not query or not target:
        return 0.0

    clean_q = clean_noise(query).lower()
    norm_q = normalize_hinglish(clean_q)

    clean_t = clean_noise(target).lower()
    norm_t = normalize_hinglish(clean_t)

    # Filter out stopwords for token analysis
    q_words = [w for w in norm_q.split() if w not in STOP_WORDS]
    if not q_words:
        q_words = norm_q.split()

    t_words = [w for w in norm_t.split() if w not in STOP_WORDS]
    if not t_words:
        t_words = norm_t.split()

    if not q_words or not t_words:
        return 0.0

    # Direct exact normalized match check
    if norm_q == norm_t:
        return 100.0

    # Token alignment scores
    q_matches = []
    for qw in q_words:
        best_w = max((fuzz.ratio(qw, tw) for tw in t_words), default=0)
        q_matches.append(best_w)

    t_matches = []
    for tw in t_words:
        best_q = max((fuzz.ratio(tw, qw) for qw in q_words), default=0)
        t_matches.append(best_q)

    avg_q = sum(q_matches) / len(q_matches) if q_matches else 0
    avg_t = sum(t_matches) / len(t_matches) if t_matches else 0
    token_sort = fuzz.token_sort_ratio(norm_q, norm_t)
    full_ratio = fuzz.ratio(norm_q, norm_t)

    # Composite score
    composite = (avg_q * 0.4) + (avg_t * 0.3) + (token_sort * 0.2) + (full_ratio * 0.1)

    # Boost if all query words matched very strongly (e.g. 2 words of a 3-word title)
    if avg_q >= 88.0 and min(q_matches) >= 75.0 and len(q_words) >= 2:
        composite = max(composite, 85.0 + (avg_q - 88.0) * 0.5)

    return composite
