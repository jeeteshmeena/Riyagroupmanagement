# We need to peek at the internal MT_TIME cache from database to refresh search cache
import database
from database import load_db, normalize_text
import re



from rapidfuzz import fuzz

def clean_story(name):
    """Local copy of clean_story function to avoid circular imports."""
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


# We caching everything inside dicts for O(1) alias/exact lookups
_exact_cache = {}    # exact clean name -> data
_norm_cache = {}     # normalized_name -> data
_alias_cache = {}    # clean_alias -> data
_list_cache = []     # for partial/did_you_mean containing original titles
_search_cache_mtime = None

def _get_cache():
    global _exact_cache, _norm_cache, _alias_cache, _list_cache, _search_cache_mtime
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
    new_list = []
    
    for name, data in db.items():
        # name is already clean and lowered in DB key
        story_name_clean = name 
        new_exact[story_name_clean] = data
        
        # normalized name index
        norm_name = data.get("normalized_name", normalize_text(story_name_clean))
        if norm_name:
            new_norm[norm_name] = data
            
        new_list.append(data.get("text", name))
        
        # aliases
        aliases = data.get("aliases", [])
        for al in aliases:
            al_clean = clean_story(al).lower()
            if al_clean:
                new_alias[al_clean] = data
            al_norm = normalize_text(al)
            if al_norm:
                new_alias[al_norm] = data
                
    _exact_cache = new_exact
    _norm_cache = new_norm
    _alias_cache = new_alias
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
    Strict High-Precision Story Match:
    1. Exact story title match
    2. Normalized story title match
    3. Alias match
    4. Exact title with common platform / season suffix stripped
    5. High-confidence direct string similarity (fuzz.ratio >= 88 with length guard)
    
    Prevents false positive matching of completely different stories!
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

    # 4. Clean platform tags from query: e.g. "Vashikaran Pocket FM" -> "Vashikaran"
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

    # 5. Strict high-precision fuzzy match (Typo correction for VERY similar names only)
    # E.g. "Vashikarn" -> "Vashikaran", "Asli Warish" -> "Asli Waris"
    # Never matches different stories or loose tokens!
    best_match = None
    best_score = 0
    q_len = len(q)
    
    for key, data in _exact_cache.items():
        k_len = len(key)
        # Length guard: title length must be close (within 3 chars or 20%)
        if abs(k_len - q_len) > max(3, int(0.20 * max(k_len, q_len))):
            continue
            
        ratio = fuzz.ratio(q, key)
        if ratio >= 88 and ratio > best_score:
            best_score = ratio
            best_match = data
            
    if best_match:
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

    q = clean_story(query).lower().strip()
    q_words = set(w for w in re.findall(r"\w+", q) if w not in SEARCH_STOP_WORDS and len(w) >= 3)
    
    if not q_words and len(q) < 5:
        return []

    results = []
    q_len = len(q)
    for title in _list_cache:
        title_clean = clean_story(title).lower()
        t_len = len(title_clean)
        
        # Length guard to prevent tiny words matching long unrelated stories
        if abs(t_len - q_len) > max(5, int(0.35 * max(t_len, q_len))):
            continue

        ratio = fuzz.ratio(q, title_clean)
        sort_ratio = fuzz.token_sort_ratio(q, title_clean)
        best = max(ratio, sort_ratio)

        if best >= 78:
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
