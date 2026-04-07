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


def search_story_exact_or_alias(query):
    """
    1. Exact story title match
    2. Normalized story title match
    3. Alias match
    4. Partial normalized string match (substring within title)
    """
    _get_cache()  # ensure cache is warm
    q = clean_story(query).lower()
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

    # Substring search: check if clean query is fully contained in any title key
    if len(q) >= 3:
        for key, data in _exact_cache.items():
            if q in key:
                return data
        # Also try normalized substring
        if qn and len(qn) >= 3:
            for key, data in _norm_cache.items():
                if qn in key:
                    return data

    return None

def get_suggestions(query, limit=5):
    """
    Return similar titles from the database using fuzzy matching.
    
    Rules:
      - Query must be at least 3 characters long.
      - Match if any of:
          a) Fuzzy similarity (WRatio) >= 62
          b) The cleaned query is a substring of the title
          c) Any word in the query is found in the title words
    """
    _get_cache()
    if not query or len(query.strip()) < 3:
        return []

    q = query.lower().strip()
    qc = clean_story(q).lower()
    qn = normalize_text(query)
    query_words = set(qc.split())

    results = []
    for title in _list_cache:
        title_lower = title.lower()
        title_clean = clean_story(title).lower()
        title_norm = normalize_text(title)
        title_words = set(title_clean.split())

        # Substring check — query contained in title
        if qc in title_clean or (qn and qn in title_norm):
            results.append((title, 100))
            continue

        # Word-overlap check — any significant word from query matches title
        common_words = query_words & title_words
        # ignore very short words (prepositions etc.)
        significant = [w for w in common_words if len(w) >= 3]
        if significant:
            # score by how many words overlap
            overlap_score = int(60 + 40 * len(significant) / max(len(query_words), 1))
            results.append((title, overlap_score))
            continue

        # Fuzzy similarity — lowered threshold for better recall
        ratio = fuzz.ratio(qc, title_clean)
        partial = fuzz.partial_ratio(qc, title_clean)
        token_set = fuzz.token_set_ratio(qc, title_clean)
        best = max(ratio, partial, token_set)

        if best >= 62:
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
