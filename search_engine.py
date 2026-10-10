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
_token_cache = {}    # frozenset(normalized_tokens) -> data  (ONLY for 2+ token titles)
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
    
    # Load link_flags to filter out broken / deleted stories
    try:
        from database import load_link_flags
        link_flags = load_link_flags() or {}
    except Exception:
        link_flags = {}

    for name, data in db.items():
        # Skip broken / deleted stories
        if link_flags.get(name, {}).get("broken"):
            continue
        # name is already clean and lowered in DB key
        story_name_clean = name 
        new_exact[story_name_clean] = data
        
        # normalized name index
        raw_title = data.get("text", name)
        norm_name = normalize_text(raw_title) or normalize_text(story_name_clean)
        if norm_name:
            new_norm[norm_name] = data
            tokens = frozenset(norm_name.split())
            # ONLY index token-sets with 2+ tokens to avoid single-word collisions
            # where "malang" matches "malang returns", "malang ki yaadein", etc.
            if len(tokens) >= 2:
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
                # Same guard: only index 2+ token alias sets
                if len(al_tokens) >= 2:
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
                    if len(toks) >= 2:
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
    4. Token-set exact match (ONLY for 2+ word queries: e.g. "Mr and Mrs agent" == "Mr. & Mrs. Agent")
    5. Cleaned platform / season suffix match
    6. Smart fuzzy match with strict thresholds and auto-learn
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

    # 4. Token-set match — ONLY for queries with 2+ meaningful words
    #    Prevents single-word like "malang" matching "malang returns" via token-set
    q_tokens = frozenset(qn.split()) if qn else frozenset()
    meaningful_tokens = q_tokens - SEARCH_STOP_WORDS
    if len(q_tokens) >= 2 and len(meaningful_tokens) >= 1:
        if q_tokens in _token_cache:
            return _token_cache[q_tokens]

    # 5. Clean platform tags and noise from query
    from smart_matcher import clean_noise, normalize_hinglish, score_match, GENERIC_CHATTER
    if q in GENERIC_CHATTER:
        return None

    clean_q = clean_noise(query).lower().strip()
    norm_q = normalize_hinglish(clean_q)
    
    if clean_q and clean_q != q:
        if clean_q in _exact_cache:
            return _exact_cache[clean_q]
        if clean_q in _norm_cache:
            return _norm_cache[clean_q]
        if clean_q in _alias_cache:
            return _alias_cache[clean_q]
        clean_tokens = frozenset(clean_q.split())
        if len(clean_tokens) >= 2 and clean_tokens in _token_cache:
            return _token_cache[clean_tokens]

    if norm_q and norm_q != q and norm_q != clean_q:
        if norm_q in _exact_cache:
            return _exact_cache[norm_q]
        if norm_q in _norm_cache:
            return _norm_cache[norm_q]
        if norm_q in _alias_cache:
            return _alias_cache[norm_q]
        norm_tokens = frozenset(norm_q.split())
        if len(norm_tokens) >= 2 and norm_tokens in _token_cache:
            return _token_cache[norm_tokens]

    return None


def search_story_fuzzy(query: str):
    """
    Intelligent fuzzy & phonetic typo matching for Local DB.
    Handles typos, colloquial Hinglish, and minor word variations.
    """
    _get_cache()
    if not query:
        return None
        
    from smart_matcher import clean_noise, normalize_hinglish, score_match, GENERIC_CHATTER
    
    clean_q = clean_noise(query).lower().strip()
    if not clean_q or len(clean_q) < 3 or clean_q in GENERIC_CHATTER:
        return None
        
    norm_q = normalize_hinglish(clean_q)
    
    # 1. First check if Hinglish-normalized query directly hits _norm_cache
    if norm_q in _norm_cache:
        return _norm_cache[norm_q]
    if norm_q in _alias_cache:
        return _alias_cache[norm_q]
        
    # 2. Token-level smart similarity match across all cached titles
    best_match = None
    best_score = 0.0
    
    for norm_title, data in _norm_cache.items():
        score = score_match(clean_q, norm_title)
        if score > best_score:
            best_score = score
            best_match = data
            
    # Threshold: 78.0 is high enough to block chatter/unrelated titles
    # while matching legitimate typos like 'Commandar vikram chouhan', 'Parimodial god'
    if best_score >= 78.0:
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

    CRITICAL: This function must return PRECISE results only.
    - Exact match: always returned
    - Word-boundary match: returns if query word appears as a complete word in the title
    - Substring match: ONLY for queries 5+ chars long to prevent garbage matches
      (e.g. "man" should NOT match "romance", "ek" should NOT match "ek villain")
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

    # 2. Word boundary match on normalized names: e.g. "mahabali" in "Mahabali Mayank", "Mahabali Veer"
    #    This is SAFE because it requires the query to match as a complete word (not substring).
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

    return matched



def extract_story_from_text(text: str) -> tuple:
    """
    Scans a conversational sentence for any known story titles in the database.
    E.g. In "kisi ke pas Mr and Mrs agent story h kya free wali",
    detects "mr and mrs agent" and returns (story_data, matched_title).
    Returns (None, None) if no story found.

    CRITICAL PRECISION RULES to prevent wrong results:
    - Only match story titles that are 2+ words OR 5+ chars long when single-word.
    - Single-word story names MUST appear as a standalone word (not embedded in other words).
    - The matched portion must be at least 60% of the query length to avoid tiny fragments matching.
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

        key_words = norm_key.split()

        # PRECISION GUARD 1: Single-word story names shorter than 5 chars are too ambiguous.
        # "ek" (2 chars), "war" (3 chars), "love" (4 chars) — too common to reliably extract.
        if len(key_words) == 1 and len(norm_key) < 5:
            continue

        # PRECISION GUARD 2: If query itself is short (≤ 4 words), and the matched story name
        # length is close to the full query length, it's likely a direct story name search —
        # let the exact/keyword search handle it, not this text-extraction function.
        # This prevents: user types "malang" → extract_story_from_text returns "Malang Returns"
        # because "malang" is a word-boundary match inside the text "malang".
        query_words = tn.split()
        if len(query_words) <= 4 and len(key_words) == 1:
            # For short queries, only match if the story name exactly equals the query
            if norm_key.strip() == tn.strip():
                return data, data.get("text", data.get("name", norm_key))
            continue

        # Check whole word boundary match in normalized sentence
        pattern = rf"\b{re.escape(norm_key)}\b"
        if re.search(pattern, tn, re.IGNORECASE):
            return data, data.get("text", data.get("name", norm_key))
            
        # Also check normalized aliases
        for al in data.get("aliases", []):
            al_norm = normalize_text(al)
            if len(al_norm) >= 3 and al_norm not in SEARCH_STOP_WORDS:
                al_words = al_norm.split()
                if len(al_words) == 1 and len(al_norm) < 5:
                    continue
                if re.search(rf"\b{re.escape(al_norm)}\b", tn, re.IGNORECASE):
                    return data, data.get("text", data.get("name", al))
                    
    return None, None
