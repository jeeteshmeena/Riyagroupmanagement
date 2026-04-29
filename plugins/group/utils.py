"""
plugins/group/utils.py
─────────────────────
JSON-based database layer for the Group Control plugin.
Uses the same DATA_DIR pattern as the main database.py.
Includes in-memory TTL cache (60 s) for group settings.
"""
import json
import os
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
def _data_path(name: str) -> str:
    data_dir = os.getenv("DATA_DIR", ".")
    path = os.path.join(data_dir, name)
    dir_path = os.path.dirname(path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    return path

GRP_SETTINGS_FILE = _data_path("grp_settings.json")
GRP_WELCOME_FILE  = _data_path("grp_welcome.json")
GRP_WARNS_FILE    = _data_path("grp_warns.json")
GRP_FILTERS_FILE  = _data_path("grp_filters.json")
GRP_LOGS_FILE     = _data_path("grp_logs.json")

# ── In-memory cache ───────────────────────────────────────────────────────────
_settings_cache: Dict[str, Any] = {}
_cache_ts: Dict[str, float] = {}
_CACHE_TTL = 60.0

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled":     False,
    "admins_only": False,
    "warn_limit":  3,
    "nsfw_strict": True,
    "locks": {
        "links":    False,
        "media":    False,
        "stickers": False,
        "forwards": False,
    },
}


def _load_json(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[GCP-DB] load failed {path}: {e}")
    return {}


def _save_json(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[GCP-DB] save failed {path}: {e}")


# ── Group Settings ────────────────────────────────────────────────────────────
def get_settings(chat_id: int) -> dict:
    key = str(chat_id)
    now = time.time()
    if key in _settings_cache and now - _cache_ts.get(key, 0) < _CACHE_TTL:
        return _settings_cache[key]
    db = _load_json(GRP_SETTINGS_FILE)
    merged = {**_DEFAULT_SETTINGS, **db.get(key, {})}
    merged["locks"] = {**_DEFAULT_SETTINGS["locks"], **merged.get("locks", {})}
    _settings_cache[key] = merged
    _cache_ts[key] = now
    return merged


def save_settings(chat_id: int, settings: dict) -> None:
    key = str(chat_id)
    db = _load_json(GRP_SETTINGS_FILE)
    db[key] = settings
    _save_json(GRP_SETTINGS_FILE, db)
    _settings_cache[key] = settings
    _cache_ts[key] = time.time()


def is_enabled(chat_id: int) -> bool:
    return get_settings(chat_id).get("enabled", False)


# ── Welcome Messages ──────────────────────────────────────────────────────────
def get_welcome(chat_id: int) -> dict:
    db = _load_json(GRP_WELCOME_FILE)
    return db.get(str(chat_id), {"messages": [], "random": False})


def save_welcome(chat_id: int, data: dict) -> None:
    db = _load_json(GRP_WELCOME_FILE)
    db[str(chat_id)] = data
    _save_json(GRP_WELCOME_FILE, db)


# ── Warnings ──────────────────────────────────────────────────────────────────
def get_warns(chat_id: int, user_id: int) -> int:
    db = _load_json(GRP_WARNS_FILE)
    return db.get(str(chat_id), {}).get(str(user_id), 0)


def add_warn(chat_id: int, user_id: int) -> int:
    db = _load_json(GRP_WARNS_FILE)
    ck, uk = str(chat_id), str(user_id)
    db.setdefault(ck, {})[uk] = db.get(ck, {}).get(uk, 0) + 1
    _save_json(GRP_WARNS_FILE, db)
    return db[ck][uk]


def reset_warns(chat_id: int, user_id: int) -> None:
    db = _load_json(GRP_WARNS_FILE)
    ck, uk = str(chat_id), str(user_id)
    if ck in db and uk in db[ck]:
        del db[ck][uk]
        _save_json(GRP_WARNS_FILE, db)


# ── Keyword Filters ───────────────────────────────────────────────────────────
def get_filters(chat_id: int) -> list:
    db = _load_json(GRP_FILTERS_FILE)
    return db.get(str(chat_id), [])


def add_filter(chat_id: int, keyword: str, reply: str) -> None:
    db = _load_json(GRP_FILTERS_FILE)
    key = str(chat_id)
    db.setdefault(key, [])
    db[key] = [f for f in db[key] if f.get("keyword", "").lower() != keyword.lower()]
    db[key].append({"keyword": keyword.lower(), "reply": reply})
    _save_json(GRP_FILTERS_FILE, db)


def remove_filter(chat_id: int, keyword: str) -> bool:
    db = _load_json(GRP_FILTERS_FILE)
    key = str(chat_id)
    if key not in db:
        return False
    before = len(db[key])
    db[key] = [f for f in db[key] if f.get("keyword", "").lower() != keyword.lower()]
    if len(db[key]) < before:
        _save_json(GRP_FILTERS_FILE, db)
        return True
    return False


# ── Log Channel ───────────────────────────────────────────────────────────────
def get_log_channel(chat_id: int) -> Optional[int]:
    db = _load_json(GRP_LOGS_FILE)
    val = db.get(str(chat_id), {}).get("log_channel")
    return int(val) if val else None


def set_log_channel(chat_id: int, log_channel_id: int) -> None:
    db = _load_json(GRP_LOGS_FILE)
    db[str(chat_id)] = {"log_channel": log_channel_id}
    _save_json(GRP_LOGS_FILE, db)
