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
    1. Exact story title match
    2. Normalized story title match
    3. Alias match
    4. High-confidence token / whole-word fuzzy match (with stopword filtering)
    """
    _get_cache()  # ensure cache is warm
    if not query:
        return None
    q = clean_story(query).lower().strip()
    qn = normalize_text(query)
    if not q:
        return None
        
    if q in _exact_cache:
        return _exact_cache[q]
        
    if qn and qn in _norm_cache:
        return _norm_cache[qn]
        
    if q in _alias_cache:
        return _alias_cache[q]
        
    if qn and qn in _alias_cache:
        return _alias_cache[qn]

    # Extract meaningful non-stopword words
    q_words = [w for w in re.findall(r"\w+", q) if w not in SEARCH_STOP_WORDS and len(w) >= 2]

    # Whole-word substring search and high-confidence fuzzy matching
    if q_words and len(q) >= 3:
        # Check boundary match: e.g. "secret fauji" in "secret fauji episode 1"
        try:
            pattern = re.compile(rf"\b{re.escape(q)}\b", re.IGNORECASE)
            for key, data in _exact_cache.items():
                if pattern.search(key):
                    return data
        except Exception:
            pass

        # High-confidence token match across exact cache
        best_match = None
        best_score = 0
        for key, data in _exact_cache.items():
            # Calculate token set and sort ratios
            ts_score = fuzz.token_set_ratio(q, key)
            t_sort = fuzz.token_sort_ratio(q, key)
            score = max(ts_score, t_sort)

            # If all meaningful query words are in the title, boost score
            if q_words and all(qw in key for qw in q_words):
                score = max(score, 92)

            if score > best_score and score >= 85:
                best_score = score
                best_match = data

        if best_match:
            return best_match

    return None

def get_suggestions(query, limit=5):
    """
    Return similar titles from the database using fuzzy matching.
    Filters out common conversational stopwords so random queries don't match.
    """
    _get_cache()
    if not query or len(query.strip()) < 3:
        return []

    q = clean_story(query).lower().strip()
    qn = normalize_text(query)
    q_words = set(w for w in re.findall(r"\w+", q) if w not in SEARCH_STOP_WORDS and len(w) >= 3)
    
    # If query only contains stopwords or is too generic, do not suggest random stories
    if not q_words and len(q) < 5:
        return []

    results = []
    for title in _list_cache:
        title_clean = clean_story(title).lower()
        title_words = set(re.findall(r"\w+", title_clean))

        # Check whole meaningful word overlap
        meaningful_overlap = q_words & title_words
        if q_words and len(meaningful_overlap) == len(q_words):
            results.append((title, 95))
            continue

        ratio = fuzz.token_sort_ratio(q, title_clean)
        token_set = fuzz.token_set_ratio(q, title_clean)
        best = max(ratio, token_set)

        if best >= 75:
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
