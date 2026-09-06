import json
import os
import re
import unicodedata

def normalize_text(text):
    if not text:
        return ""
    
    # Manual un-fancy for small-caps which bypass standard Unicode decomposition
    small_caps = {
        'ꜱ':'s', 'ɪ':'i', 'ʟ':'l', 'ᴇ':'e', 'ɴ':'n', 'ᴛ':'t', 'ᴏ':'o', 'ᴠ':'v',
        'ᴀ':'a', 'ʙ':'b', 'ᴄ':'c', 'ᴅ':'d', 'ꜰ':'f', 'ɢ':'g', 'ʜ':'h', 'ᴊ':'j',
        'ᴋ':'k', 'ᴍ':'m', 'ᴘ':'p', 'ǫ':'q', 'ʀ':'r', 'ᴜ':'u', 'ᴡ':'w', 'ʏ':'y', 'ᴢ':'z'
    }
    
    t = str(text)
    for k, v in small_caps.items():
        t = t.replace(k, v)
        
    # Convert unicode fancy fonts to normal (NFKD handles most Math Bold, script, fraktur)
    t = unicodedata.normalize('NFKD', t).lower()
    
    # Pre-process symbols before stripping punctuation
    t = re.sub(r'[\&\+]', ' and ', t)
    t = re.sub(r'[@]', ' at ', t)
    
    # Replace all punctuation and special characters with spaces
    # Bypasses . - , : ; ' " _ ! ? / \ | ~ ( ) [ ] { } * # % ^ = < >
    t = re.sub(r'[^\w\s]', ' ', t)
    
    # Collapse multiple spaces and return clean string
    return ' '.join(t.split())

# Paths - use DATA_DIR from config for VPS (load_dotenv runs when config is imported first)
def _data_path(name):
    data_dir = os.getenv("DATA_DIR", ".")
    path = os.path.join(data_dir, name)
    dir_path = os.path.dirname(path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    return path

DB_FILE = _data_path("stories_db.json")
CLAIMS_FILE = _data_path("claims_db.json")
REQUESTS_FILE = _data_path("requests_db.json")
VOTING_FILE = _data_path("voting_db.json")
SEARCH_INDEX_FILE = _data_path("search_index.json")
STORY_INDEX_FILE = _data_path("story_index.json")
GENRE_INDEX_FILE = _data_path("genre_index.json")
LANG_FILE = _data_path("languages_db.json")
COOLDOWN_FILE = _data_path("cooldowns_db.json")
LINK_FLAGS_FILE = _data_path("link_flags.json")
CONFIG_FILE = _data_path("config_db.json")
FAVORITES_FILE = _data_path("favorites_db.json")
STATS_FILE = _data_path("stats_db.json")
SUBS_FILE = _data_path("subs_db.json")
FORMATS_FILE = _data_path("learned_formats.json")
STORY_CHANNELS_FILE = _data_path("story_channels.json")
LEARNED_ALIASES_FILE = _data_path("learned_aliases.json")

_DB_CACHE = None
_LEARNED_ALIASES_CACHE = None

def load_learned_aliases() -> dict:
    global _LEARNED_ALIASES_CACHE
    if _LEARNED_ALIASES_CACHE is not None:
        return _LEARNED_ALIASES_CACHE
    if not os.path.exists(LEARNED_ALIASES_FILE):
        _LEARNED_ALIASES_CACHE = {}
        return _LEARNED_ALIASES_CACHE
    try:
        with open(LEARNED_ALIASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            _LEARNED_ALIASES_CACHE = data if isinstance(data, dict) else {}
            return _LEARNED_ALIASES_CACHE
    except Exception:
        _LEARNED_ALIASES_CACHE = {}
        return _LEARNED_ALIASES_CACHE

def save_learned_alias(alias: str, canonical_name: str, story_data: dict = None) -> None:
    """
    Save learned alias mapping so future queries hit immediately.
    Also propagates to local stories_db.json or MongoDB if applicable.
    """
    if not alias or not canonical_name:
        return
    a_norm = normalize_text(alias)
    if not a_norm or len(a_norm) < 2 or a_norm == normalize_text(canonical_name):
        return

    aliases = load_learned_aliases()
    if aliases.get(a_norm) == canonical_name:
        return

    aliases[a_norm] = canonical_name
    global _LEARNED_ALIASES_CACHE
    _LEARNED_ALIASES_CACHE = aliases

    try:
        with open(LEARNED_ALIASES_FILE, "w", encoding="utf-8") as f:
            json.dump(aliases, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    # If story is in local DB, also append to story["aliases"]
    try:
        db = load_db()
        key = canonical_name.lower().strip()
        matched_key = None
        for k, v in db.items():
            if k == key or normalize_text(v.get("text", k)) == normalize_text(canonical_name):
                matched_key = k
                break
        if matched_key:
            st = db[matched_key]
            st_aliases = st.setdefault("aliases", [])
            if alias not in st_aliases:
                st_aliases.append(alias)
            if a_norm not in st_aliases:
                st_aliases.append(a_norm)
            save_db(db)
    except Exception:
        pass

    # If MongoDB story, propagate to MongoDB document
    try:
        from mongo_search import save_mongo_alias_sync
        save_mongo_alias_sync(canonical_name, alias)
    except Exception:
        pass
_DB_MTIME = None


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_db():
    """Load main stories database with simple mtime-based caching."""
    global _DB_CACHE, _DB_MTIME

    if not os.path.exists(DB_FILE):
        _DB_CACHE = {}
        _DB_MTIME = None
        return _DB_CACHE

    try:
        mtime = os.path.getmtime(DB_FILE)
    except OSError:
        mtime = None

    if _DB_CACHE is not None and _DB_MTIME == mtime:
        return _DB_CACHE

    _DB_CACHE = _load_json(DB_FILE, {})
    _DB_MTIME = mtime

    # Ensure normalized_name is fully up-to-date with our fancy fonts logic
    changed = False
    for k, v in _DB_CACHE.items():
        correct_norm = normalize_text(v.get("name", k))
        if v.get("normalized_name") != correct_norm:
            v["normalized_name"] = correct_norm
            changed = True
    
    if changed:
        _save_json(DB_FILE, _DB_CACHE)
        try:
            _DB_MTIME = os.path.getmtime(DB_FILE)
        except OSError:
            pass

    return _DB_CACHE


def save_db(data):
    global _DB_CACHE, _DB_MTIME

    _save_json(DB_FILE, data)
    _DB_CACHE = data
    try:
        _DB_MTIME = os.path.getmtime(DB_FILE)
    except OSError:
        _DB_MTIME = None


# Link quality scores — used in add_story to decide which entry wins
_LINK_BTN_PRIMARY = ("play", "ᴘʟᴀʏ", "buy", "ʙᴜʏ", "read", "open", "start", "watch")
_LINK_BTN_BACKUP  = ("backup", "mirror", "alt", "alternative", "link 2", "link2")


def _story_link_quality(story: dict) -> int:
    """
    Score the link quality stored in a story dict.
      3 = has explicit primary button (Play/Buy) → best
      2 = link present, no button info            → good
      1 = link present but btn_priority == backup → weak
      0 = no link
    """
    link = story.get("link", "")
    if not link:
        return 0
    btn = (story.get("link_btn_text") or "").lower()
    if btn:
        if any(k in btn for k in _LINK_BTN_BACKUP):
            return 1
        if any(k in btn for k in _LINK_BTN_PRIMARY):
            return 3
    return 2


def add_story(story):
    db = load_db()

    name = story["name"]
    story["normalized_name"] = normalize_text(name)

    if name not in db:
        db[name] = story
    else:
        existing = db[name]
        new_q = _story_link_quality(story)
        old_q = _story_link_quality(existing)

        new_link = story.get("link", "")
        old_link = existing.get("link", "")

        # Decision tree:
        # • New link is BETTER quality → always replace (even if older msg)
        # • Same quality → replace only if newer message_id (fresher post)
        # • New link is WORSE quality → keep existing, only update non-link fields
        if new_q > old_q:
            # Better link wins regardless of message age
            existing_aliases = existing.get("aliases", [])
            if existing_aliases:
                story["aliases"] = existing_aliases
            db[name] = story
        elif new_q == old_q and story.get("message_id", 0) > existing.get("message_id", 0):
            # Same quality, newer message → update everything
            existing_aliases = existing.get("aliases", [])
            if existing_aliases:
                story["aliases"] = existing_aliases
            if not new_link:
                story["link"] = old_link
            db[name] = story
        else:
            # New entry is worse or same+older — keep existing link but maybe store
            # other metadata if existing has no link at all
            if not old_link and new_link:
                existing["link"] = new_link
                db[name] = existing

    save_db(db)


def get_story(name):
    db = load_db()
    res = db.get(name)
    if res:
        return res
    try:
        from mongo_search import search_story_from_mongo_sync
        return search_story_from_mongo_sync(name)
    except Exception:
        return None


def remove_stories_not_in(keys_to_keep):
    """Remove from DB any story whose key is not in keys_to_keep. Used after scan to drop deleted posts."""
    global _DB_CACHE
    db = load_db()
    keys_set = set(keys_to_keep)
    removed = [k for k in list(db.keys()) if k not in keys_set]
    for k in removed:
        del db[k]
    if removed:
        save_db(db)
        _DB_CACHE = db


# -----------------------
# Persistent claims / requests
# -----------------------

def load_claims():
    return _load_json(CLAIMS_FILE, {})


def save_claims(data):
    _save_json(CLAIMS_FILE, data)


def load_requests():
    """
    Load requests.
    Legacy format:
      {"requests": {"story": [user_ids]}, "chats": {"story": chat_id}}
    New format:
      {"requests": {"story": {"chat_id": [user_ids], ...}}}
    """
    raw = _load_json(REQUESTS_FILE, {"requests": {}, "chats": {}})
    requests_raw = raw.get("requests", {})
    chats_raw = raw.get("chats", {})
    requests = {}

    for story, value in requests_raw.items():
        if isinstance(value, list):
            # legacy: single chat per story
            chat_id = chats_raw.get(story)
            if chat_id is None:
                continue
            requests[story] = {str(chat_id): set(value)}
        elif isinstance(value, dict):
            inner = {}
            for chat_id, users in value.items():
                inner[str(chat_id)] = set(users) if isinstance(users, list) else set()
            if inner:
                requests[story] = inner

    return {"requests": requests}


def save_requests(data):
    """
    Save requests in new format:
      {"requests": {"story": {"chat_id": [user_ids], ...}}}
    """
    requests = data.get("requests", {})
    serializable = {}
    for story, chats in requests.items():
        ser_chats = {}
        for chat_id, users in chats.items():
            ser_chats[str(chat_id)] = list(users) if isinstance(users, set) else list(users or [])
        serializable[story] = ser_chats
    _save_json(REQUESTS_FILE, {"requests": serializable})


# -----------------------
# Persistent search index (survives restarts)
# -----------------------

def load_search_index():
    """Load search index: {lowercase_key -> original_name}."""
    raw = _load_json(SEARCH_INDEX_FILE, {})
    if isinstance(raw, dict):
        return raw
    return {}


def save_search_index(data):
    _save_json(SEARCH_INDEX_FILE, data)


def load_story_index():
    """Load ordered list of story names for /stories pagination."""
    raw = _load_json(STORY_INDEX_FILE, [])
    if isinstance(raw, list):
        return raw
    return []


def save_story_index(data):
    _save_json(STORY_INDEX_FILE, data)


def load_genre_index():
    """Load genre index mapping: {genre_name -> [story_name1, ...]}."""
    raw = _load_json(GENRE_INDEX_FILE, {})
    if isinstance(raw, dict):
        return raw
    return {}


def save_genre_index(data):
    _save_json(GENRE_INDEX_FILE, data)


def build_genre_index():
    """Build or rebuild genre index mapping { genre_name: [story_name1, ...] } from main db."""
    import re
    db = load_db()
    genre_map = {}
    
    for key, story in db.items():
        story_name = story.get("text") or story.get("name") or key
        if not story_name:
            continue
            
        genre = story.get("genre") or story.get("story_type")
        if not genre:
            caption = story.get("caption", "")
            match = re.search(r"(?:🧩|🗓|Story Type|Type|Genre)\s*:*\s*-*\s*(.+)", caption, re.IGNORECASE)
            if match:
                genre = match.group(1).strip()
        
        if not genre:
            genre = "General"
            
        genre = genre.strip()
        if genre.lower() in ("unknown", "none", "not specified", ""):
            genre = "General"
            
        # Clean html tags, dashes, dots, underscores, commas from genre name
        genre = re.sub(r"<[^>]+>", "", genre).strip()
        genre = re.sub(r"^[\s\-_.,:+|/]+|[\s\-_.,:+|/]+$", "", genre).strip()
        genre = re.sub(r"[\-_.,:+|/]{2,}", " ", genre).strip()
        
        if not genre or genre.lower() in ("unknown", "none", "not specified", ""):
            genre = "General"
            
        genre_clean = genre.title() if genre.islower() or genre.isupper() else genre
        
        if genre_clean not in genre_map:
            genre_map[genre_clean] = []
        if story_name not in genre_map[genre_clean]:
            genre_map[genre_clean].append(story_name)
            
    save_genre_index(genre_map)
    return genre_map


# -----------------------
# Per-chat language settings
# -----------------------

def load_languages():
    """Return mapping chat_id(str) -> language code ('en' or 'hi')."""
    raw = _load_json(LANG_FILE, {})
    return raw if isinstance(raw, dict) else {}


def save_languages(data):
    _save_json(LANG_FILE, data)


# -----------------------
# Persistent cooldowns (copyright, etc.)
# -----------------------

def load_cooldowns():
    """Return mapping user_id(str) -> {'until': float, 'reason': str}."""
    raw = _load_json(COOLDOWN_FILE, {})
    return raw if isinstance(raw, dict) else {}


def save_cooldowns(data):
    _save_json(COOLDOWN_FILE, data)


# -----------------------
# Link flags (broken reports, etc.)
# -----------------------

def load_link_flags():
    """Return mapping story_key -> {'broken': bool, 'link': str, 'voters': [int], 'chats': [int]}."""
    raw = _load_json(LINK_FLAGS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def save_link_flags(data):
    _save_json(LINK_FLAGS_FILE, data)


# -----------------------
# Bot config (sources, panel settings, etc.)
# -----------------------

def load_config():
    """
    Load high-level bot configuration.
    Keys:
      start_text: str
      force_sub_channels: list
      moderators: list
      auto_delete: dict
      sources: list of extra channel ids
      formats: dict channel_id -> list of format dicts
    """
    default = {
        "start_text": "",
        "force_sub_channels": [],
        "moderators": [],
        "auto_delete": {},
        "sources": [],
        "formats": {},
    }
    raw = _load_json(CONFIG_FILE, default)
    if not isinstance(raw, dict):
        return default
    for k, v in default.items():
        raw.setdefault(k, v)
    return raw


def save_config(data):
    _save_json(CONFIG_FILE, data)

# -----------------------
# New Features Databases
# -----------------------

def load_favorites():
    raw = _load_json(FAVORITES_FILE, {})
    return raw if isinstance(raw, dict) else {}

def save_favorites(data):
    _save_json(FAVORITES_FILE, data)

def load_stats():
    raw = _load_json(STATS_FILE, {"searches": {}, "users": {}, "trending": {}})
    return raw if isinstance(raw, dict) else {"searches": {}, "users": {}, "trending": {}}

def save_stats(data):
    _save_json(STATS_FILE, data)

def load_subs():
    raw = _load_json(SUBS_FILE, [])
    return raw if isinstance(raw, list) else []

def save_subs(data):
    _save_json(SUBS_FILE, data)


# -----------------------
# Learned formats (per-channel auto-detected templates)
# -----------------------

def load_learned_formats() -> dict:
    """
    Returns: { str(channel_id): [template_dict, ...], ... }
    """
    raw = _load_json(FORMATS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def save_learned_formats(data: dict):
    _save_json(FORMATS_FILE, data)


# -----------------------
# Voting system persistence
# -----------------------

def load_voting_db() -> dict:
    """
    Return voting db: {
        "queue": [{"name": str, "requesters": {str(chat_id): [user_ids]}}, ...],
        "polls": {
            str(poll_id): {
                "message_id": int,
                "chat_id": int,
                "options": [str], # story names in order
                "votes": {str(option_index): [user_ids]},
                "created_at": float
            }
        }
    }
    """
    return _load_json(VOTING_FILE, {"queue": [], "polls": {}})

def save_voting_db(data: dict):
    _save_json(VOTING_FILE, data)

# --- Saved Posts DB for Re-Publishing ---
SAVED_POSTS_FILE = _data_path("saved_posts_db.json")

def load_saved_posts():
    return _load_json(SAVED_POSTS_FILE, {})

def save_saved_posts(data):
    _save_json(SAVED_POSTS_FILE, data)

# --- Story Broadcast Channels (Ongoing / Completed) ---
def load_story_channels() -> dict:
    """Returns {"ongoing": [...], "completed": [...]} with channel IDs/usernames."""
    return _load_json(STORY_CHANNELS_FILE, {"ongoing": [], "completed": []})

def save_story_channels(data: dict):
    _save_json(STORY_CHANNELS_FILE, data)


# --- Automated Ads DB (SL Auto-Ads System) ---
ADS_FILE = _data_path("ads_db.json")

def load_ads_db() -> dict:
    """Returns all scheduled/automated ads configurations."""
    return _load_json(ADS_FILE, {})

def save_ads_db(data: dict):
    """Saves all scheduled/automated ads configurations."""
    _save_json(ADS_FILE, data)


# --- Broadcast History DB for Mass Deletion ---
BROADCASTS_LOG_FILE = _data_path("broadcast_history.json")

def load_broadcast_history() -> list:
    """Returns a list of all broadcasted message records."""
    return _load_json(BROADCASTS_LOG_FILE, [])

def save_broadcast_history(data: list):
    """Saves the list of broadcasted message records."""
    _save_json(BROADCASTS_LOG_FILE, data)

def add_broadcast_record(chat_id, message_id, timestamp: float):
    """Logs a sent broadcast message and purges logs older than 3 days."""
    import time
    try:
        history = load_broadcast_history()
        history.append({
            "chat_id": chat_id,
            "message_id": message_id,
            "timestamp": timestamp
        })
        # Keep only the last 3 days of broadcast history to prevent file bloat
        cutoff = time.time() - (3 * 24 * 3600)
        history = [h for h in history if h.get("timestamp", 0) > cutoff]
        save_broadcast_history(history)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"[Database] add_broadcast_record error: {e}")


# --- Global Ban (GBan) Persistent Database ---
GBAN_FILE = _data_path("gban_db.json")

def load_gban_db() -> dict:
    """
    Returns persistent GBan dictionary:
    {
      "user_id_str": {
         "username": "...",
         "reason": "...",
         "by": admin_id,
         "time": timestamp
      }
    }
    """
    return _load_json(GBAN_FILE, {})

def save_gban_db(data: dict):
    _save_json(GBAN_FILE, data)

def is_gban_user(user_id: int) -> bool:
    if not user_id:
        return False
    gb = load_gban_db()
    return str(user_id) in gb

def add_gban_user(user_id: int, username: str = "", reason: str = "", by_admin: int = 0) -> bool:
    if not user_id:
        return False
    gb = load_gban_db()
    import time
    gb[str(user_id)] = {
        "username": username or "",
        "reason": reason or "No reason provided",
        "by": by_admin,
        "time": time.time()
    }
    save_gban_db(gb)
    return True

def remove_gban_user(user_id: int) -> bool:
    if not user_id:
        return False
    gb = load_gban_db()
    uid_str = str(user_id)
    if uid_str in gb:
        del gb[uid_str]
        save_gban_db(gb)
        return True
    return False




