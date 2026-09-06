# We need to peek at the internal MT_TIME cache from database to refresh search cache
import database
from database import load_db, normalize_text
import re
from rapidfuzz import fuzz

def clean_story(name):
    """Local copy of clean_story function to avoid circular imports."""
    if not name:
        return ""
    name = re.sub(r"\(.*?\)", "", str(name))
    name = re.sub(r"\s+", " ", name)
    return name.strip()


# Cached lookup indices for O(1) exact, normalized, token-set, and alias matching
_exact_cache = {}    # exact clean name -> data
_norm_cache = {}     # normalized_name -> data
_alias_cache = {}    # clean_alias -> data
_token_cache = {}    # frozenset(normalized_tokens) -> data
_list_cache = []     # for partial/did_you_mean containing original titles
_search_cache_mtime = None

def _get_cache():
    global _exact_cache, _norm_cache, _alias_cache, _token_cache, _list_cache, _search_cache_mtime
    db = load_db()
    
    if not db:
        return {}
    
    # Invalidate when DB file is updated OR when size changes (catches in-process updates)
    if (_search_cache_mtime is not None
            and _search_cache_mtime == database._DB_MTIME
            and len(_exact_cache) == len(db)):
        return _exact_cache

    new_exact = {}
    new_norm = {}
    new_alias = {}
    new_tokens = {}
    new_list = []
    
    for name, data in db.items():
        # name is already clean and lowered in DB key
        story_name_clean = name 
        new_exact[story_name_clean] = data
        
        # normalized name index
        raw_title = data.get("text", name)
        norm_name = normalize_text(raw_title) or normalize_text(story_name_clean)
        if norm_name:
            new_norm[norm_name] = data
            tokens = frozenset(norm_name.split())
            if len(tokens) >= 1:
                new_tokens[tokens] = data
            
        new_list.append(raw_title)
        
        # aliases
        aliases = data.get("aliases", [])
        for al in aliases:
            al_clean = clean_story(al).lower()
            if al_clean:
                new_alias[al_clean] = data
            al_norm = normalize_text(al)
            if al_norm:
                new_alias[al_norm] = data
                al_tokens = frozenset(al_norm.split())
                if len(al_tokens) >= 1:
                    new_tokens[al_tokens] = data

    # Integrate learned aliases from persistent storage
    try:
        learned = database.load_learned_aliases()
        for al, canonical in (learned or {}).items():
            al_norm = normalize_text(al)
            if al_norm:
                can_norm = normalize_text(canonical)
                target_data = new_exact.get(clean_story(canonical).lower()) or new_norm.get(can_norm)
                if target_data:
                    new_alias[al_norm] = target_data
                    toks = frozenset(al_norm.split())
                    if len(toks) >= 1:
                        new_tokens[toks] = target_data
    except Exception:
        pass
                
    _exact_cache = new_exact
    _norm_cache = new_norm
    _alias_cache = new_alias
    _token_cache = new_tokens
    _list_cache = new_list
    _search_cache_mtime = database._DB_MTIME
    return _exact_cache


# Stopwords list: common words that should not trigger false story matches on their own
SEARCH_STOP_WORDS = {
    "hai", "hain", "kya", "kar", "karo", "raha", "rahi", "ho", "bhai", "yaar",
    "suno", "bolo", "batao", "mujhe", "tum", "aap", "kaise", "kaisi", "the",
    "tha", "thi", "aur", "and", "the", "for", "with", "this", "that", "how",
    "what", "are", "you", "tell", "give", "send", "bhejo", "chahiye",
    "dhoondho", "dhundho", "please", "plz", "link", "story", "audiobook",
    "kahani", "sunna", "padhna", "naam", "koi", "wala", "wali", "wale",
    "de", "do", "ki", "ka", "ke", "ko", "se", "me", "mein", "par", "ek",
    "yeh", "woh", "wo", "hi", "hello", "hey", "ok", "acha", "achha", "main",
    "sunao", "bataiye", "dijiye", "bhej do", "de do", "bhej", "dena", "sunni",
    "padhni", "chahata", "chahati", "hoon", "hun"
}

def search_story_exact_or_alias(query):
    """
    Robust High-Precision Story Match:
    1. Exact story title match (case-insensitive)
    2. Normalized story title match (bypassing punctuation, symbols like &, +, @)
    3. Alias exact and normalized match (including persistent learned aliases)
    4. Token-set exact match (e.g. "Mr and Mrs agent" == "Mr. & Mrs. Agent")
    5. Cleaned platform / season suffix match
    6. Smart fuzzy match with 1-2 character typo tolerance and auto-learn
    """
    _get_cache()  # ensure cache is warm
    if not query:
        return None
    q = clean_story(query).lower().strip()
    qn = normalize_text(query)
    if not q or len(q) < 2:
        return None
        
    # 1. Exact match
    if q in _exact_cache:
        return _exact_cache[q]
        
    # 2. Normalized match
    if qn and qn in _norm_cache:
        return _norm_cache[qn]
        
    # 3. Alias match
    if q in _alias_cache:
        return _alias_cache[q]
        
    if qn and qn in _alias_cache:
        return _alias_cache[qn]

    # Check learned aliases directly in case cache was refreshed
    try:
        learned = database.load_learned_aliases()
        if qn in learned:
            can = learned[qn]
            match = _exact_cache.get(clean_story(can).lower()) or _norm_cache.get(normalize_text(can))
            if match:
                return match
    except Exception:
        pass

    # 4. Token-set match (handles word reordering & punctuation/symbol differences)
    q_tokens = frozenset(qn.split())
    if q_tokens and q_tokens in _token_cache:
        matched_data = _token_cache[q_tokens]
        # Auto-learn variant
        try:
            database.save_learned_alias(query, matched_data.get("text", matched_data.get("name", "")))
        except Exception:
            pass
        return matched_data

    # 5. Clean platform tags from query: e.g. "Vashikaran Pocket FM" -> "Vashikaran"
    clean_q = q
    for tag in ["pocket fm", "kuku fm", "kuku tv", "pratilipi", "headfone", "eight fm", "audiobook", "full story", "all episodes", "season 1", "season 2", "part 1", "part 2"]:
        clean_q = clean_q.replace(tag, "").strip()
    clean_qn = normalize_text(clean_q)
    
    if clean_q and clean_q != q:
        if clean_q in _exact_cache:
            return _exact_cache[clean_q]
        if clean_qn in _norm_cache:
            return _norm_cache[clean_qn]
        if clean_q in _alias_cache:
            return _alias_cache[clean_q]
        clean_tokens = frozenset(clean_qn.split())
        if clean_tokens and clean_tokens in _token_cache:
            return _token_cache[clean_tokens]

    # 6. Strict high-precision fuzzy match (Typo correction for VERY similar names)
    # E.g. "Vashikarn" -> "Vashikaran", "Asli Warish" -> "Asli Waris", "Mr and Mrs agnet" -> "Mr. & Mrs. Agent"
    best_match = None
    best_score = 0
    qn_len = len(qn)
    
    for norm_title, data in _norm_cache.items():
        t_len = len(norm_title)
        # Length guard: title length must be close (within 5 chars or 35%)
        if abs(t_len - qn_len) > max(5, int(0.35 * max(t_len, qn_len))):
            continue
            
        ratio = fuzz.ratio(qn, norm_title)
        sort_ratio = fuzz.token_sort_ratio(qn, norm_title)
        score = max(ratio, sort_ratio)
        
        # Token set ratio for multi-word titles with slight additions
        if len(q_tokens) >= 2 and len(norm_title.split()) >= 2:
            set_ratio = fuzz.token_set_ratio(qn, norm_title)
            if set_ratio >= 88:
                score = max(score, set_ratio)
                
        # Typo threshold: 78 for multi-word/longer titles (allows 1-2 char typos), 84 for short 1-word
        threshold = 78 if (len(q_tokens) >= 2 or qn_len >= 6) else 84
        if score >= threshold and score > best_score:
            best_score = score
            best_match = data
            
    if best_match:
        # Auto-learn successful typo correction permanently
        try:
            database.save_learned_alias(query, best_match.get("text", best_match.get("name", "")))
        except Exception:
            pass
        return best_match

    return None

def get_suggestions(query, limit=5):
    """
    Return genuinely similar titles from the database for typos.
    Filters out loose matches so non-existent stories don't get random suggestions.
    """
    _get_cache()
    if not query or len(query.strip()) < 3:
        return []

    qn = normalize_text(query)
    q_words = set(w for w in qn.split() if w not in SEARCH_STOP_WORDS and len(w) >= 3)
    
    if not q_words and len(qn) < 4:
        return []

    results = []
    qn_len = len(qn)
    for title in _list_cache:
        title_norm = normalize_text(title)
        t_len = len(title_norm)
        
        # Length guard to prevent tiny words matching long unrelated stories
        if abs(t_len - qn_len) > max(6, int(0.40 * max(t_len, qn_len))):
            continue

        ratio = fuzz.ratio(qn, title_norm)
        sort_ratio = fuzz.token_sort_ratio(qn, title_norm)
        best = max(ratio, sort_ratio)

        if best >= 74:
            results.append((title, best))

    # Sort by best score descending
    results.sort(key=lambda x: x[1], reverse=True)

    # Remove duplicates, keeping order
    seen = set()
    final = []
    for r, _ in results:
        if r not in seen:
            seen.add(r)
            final.append(r)

    return final[:limit]


def search_stories_by_keyword(query: str, limit: int = 10) -> list:
    """
    Search stories by keyword (e.g. 1-word or partial word like "mahabali", "waris", "fauji", "vampire").
    Returns a list of matching story data dicts (up to limit).
    """
    _get_cache()
    if not query:
        return []
        
    q = clean_story(query).lower().strip()
    qn = normalize_text(query)
    if not qn or len(qn) < 2:
        return []

    # If query is purely a stopword (e.g. "hai", "kya", "bhai", "story"), don't search
    if qn in SEARCH_STOP_WORDS and len(qn) <= 4:
        return []

    matched = []
    seen_keys = set()

    # 1. Exact match / exact normalized match first
    if q in _exact_cache:
        matched.append(_exact_cache[q])
        seen_keys.add(clean_story(_exact_cache[q].get("text", _exact_cache[q].get("name", ""))).lower())
    elif qn in _norm_cache:
        matched.append(_norm_cache[qn])
        seen_keys.add(clean_story(_norm_cache[qn].get("text", _norm_cache[qn].get("name", ""))).lower())

    # 2. Word boundary substring match on normalized names: e.g. "mahabali" in "Mahabali Mayank", "Mahabali Veer"
    try:
        pattern = re.compile(rf"\b{re.escape(qn)}\b", re.IGNORECASE)
        for norm_name, data in _norm_cache.items():
            t_name = clean_story(data.get("text", data.get("name", norm_name)))
            t_key = t_name.lower()
            if t_key not in seen_keys and pattern.search(norm_name):
                matched.append(data)
                seen_keys.add(t_key)
                if len(matched) >= limit:
                    return matched
    except Exception:
        pass

    # 3. Substring match if qn is 3+ chars: e.g. "waris" in "asli waris"
    if len(qn) >= 3:
        for norm_name, data in _norm_cache.items():
            t_name = clean_story(data.get("text", data.get("name", norm_name)))
            t_key = t_name.lower()
            if t_key not in seen_keys and (qn in norm_name or any(qn in normalize_text(al) for al in data.get("aliases", []))):
                matched.append(data)
                seen_keys.add(t_key)
                if len(matched) >= limit:
                    return matched

    return matched


def extract_story_from_text(text: str) -> tuple:
    """
    Scans a conversational sentence for any known story titles in the database.
    E.g. In "kisi ke pas Mr and Mrs agent story h kya free wali",
    detects "mr and mrs agent" and returns (story_data, matched_title).
    Returns (None, None) if no story found.
    """
    _get_cache()
    if not text or len(text.strip()) < 3:
        return None, None
        
    tn = normalize_text(text)
    if not tn or len(tn) < 3:
        return None, None
    
    # Sort normalized titles by length descending so longer titles match first
    # (e.g. "Mr and Mrs Agent" matches before "Agent")
    sorted_titles = sorted(_norm_cache.items(), key=lambda x: len(x[0]), reverse=True)
    
    for norm_key, data in sorted_titles:
        if len(norm_key) < 3 or norm_key in SEARCH_STOP_WORDS:
            continue
        # Check whole word boundary match in normalized sentence
        pattern = rf"\b{re.escape(norm_key)}\b"
        if re.search(pattern, tn, re.IGNORECASE):
            return data, data.get("text", data.get("name", norm_key))
            
        # Also check normalized aliases
        for al in data.get("aliases", []):
            al_norm = normalize_text(al)
            if len(al_norm) >= 3 and al_norm not in SEARCH_STOP_WORDS:
                if re.search(rf"\b{re.escape(al_norm)}\b", tn, re.IGNORECASE):
                    return data, data.get("text", data.get("name", al))
                    
    return None, None
