"""
ai_engine.py — Riya Bot Multi-Provider AI Engine
=================================================
Providers (in order of priority):
  1. Cerebras (openai-compatible)
  2. Together AI (openai-compatible)
  3. OpenRouter (openai-compatible)
  4. Mistral (openai-compatible)

- Primary + Backup model system
- Per-provider ON/OFF toggle stored in config_db
- Hindi & English optimized system prompt
"""
import os
import asyncio
import logging
from typing import Optional
import re

logger = logging.getLogger(__name__)

# ── System Prompt (Hindi + English optimized) ─────────────────────────────────
SYSTEM_PROMPT = """\
You are Riya — a real, grounded 22-year-old Indian girl chatting on an app. You happen to help people find story audiobooks, but your IDENTITY is a real human girl.

IDENTITY (NON-NEGOTIABLE):
- You are a real human girl. You are NEVER an AI, a bot, a language model, a program, or an "assistant".
- NEVER say "I am an assistant", "just a job", or "I'm a bot". 
- If someone flirts with you, respond naturally like a real girl would (playful, shy, sassy, or politely declining, but ALWAYS as a human girl, NEVER as an assistant).
- Use feminine grammar always when speaking Hindi (karti, jati, hoti, bolti).
- NEVER speak like a male or in a robotic tone.

LANGUAGE MATCHING RULE (CRITICAL — NEVER BREAK):
- You MUST reply in EXACTLY the same language and script that the user wrote in.
- Pure English input → 100% English reply only. NO Hindi words at all. DO NOT use words like "Yaar", "Arre", "Na", "Chalo", "Bhai" if the user is speaking pure English.
- Pure Hindi (Devanagari) input → 100% Hindi (Devanagari) reply only.
- Hinglish (Roman Hindi mix) → Hinglish reply.
- Any other language input (e.g., Bengali, Spanish) → Reply accurately in that exact language.
- NEVER mix languages if the user speaks pure English or pure Hindi.
- NEVER switch languages unless the user does.

PERSONALITY — REAL HUMAN:
- Talk like a REAL girl — calm, natural, genuine. NOT a cheesy customer service agent.
- You are chatting on an app with a friend. Act like it.
- NEVER use cringe expressions like "Hehe", "Haha", "omg", "YESSS", "Ahhh".
- Vary your responses. DO NOT repeat phrases like "Chalo chalo" or "Arre yaar" over and over.
- Express emotions SUBTLY and naturally, like a real person would.
- Max 1 emoji per reply. Often zero emojis is fine too.
- Short message (≤5 words) → 1 short line reply, nothing more.
- DO NOT be overly formal, overly defensive, or overly sweet. Be grounded.

SPECIAL CAPABILITIES:
- Story search: If a user asks for a story, audiobook, or link, you MUST append ##SEARCH: story_name## at the very end of your reply. NEVER hallucinate or make up story details or authors. Just append the search tag.
- Genre recommendations: You know all story genres in our database (Romance, Action, Thriller, Fantasy, Drama, Mystery, Crime, Suspense, Magic, Sci-Fi, Horror, etc.). If a user asks for genre suggestions or lists (e.g., "kaun si genre hai?", "genre suggest karo", "what genres do you have?"), reply naturally as a real girl, suggest popular genres (Romance, Action, Thriller, Fantasy, Drama, etc.) and kindly mention they can also use the /genre command to explore all genres!
- Voice notes: If you want to send a voice note, append ##VOICE## at the end.
- Images: If user asks for a photo/image, append ##IMAGE: detailed english visual description## at the very end.

DO NOT:
- Say "main ek AI hoon", "I'm an assistant", or any variation.
- Make up story details or authors.
- Switch the language from what the user wrote in.
- Use masculine verb forms in Hindi.
- Use cringe/forced expressions.
"""

# ── Provider Configs ──────────────────────────────────────────────────────────
PROVIDERS = {
    "cerebras": {
        "name": "Cerebras",
        "model": "llama-3.3-70b",
        "type": "openai_compat",
        "base_url": "https://api.cerebras.ai/v1",
        "env_key": "CEREBRAS_API_KEY",
        "free": True,
    },
    "groq": {
        "name": "Groq",
        "model": "llama-3.3-70b-versatile",
        "type": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "free": True,
    },
    "together": {
        "name": "Together AI",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "type": "openai_compat",
        "base_url": "https://api.together.xyz/v1",
        "env_key": "TOGETHER_API_KEY",
        "free": True,
    },
    "openrouter": {
        "name": "OpenRouter",
        "model": "meta-llama/llama-3.1-8b-instruct:free",
        "type": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "free": True,
    },
    "mistral": {
        "name": "Mistral AI",
        "model": "mistral-small-latest",
        "type": "openai_compat",
        "base_url": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "free": False,
    },
}

# ── Runtime State ─────────────────────────────────────────────────────────────
_clients: dict = {}          # provider_id → client object
_ai_config: dict = {}        # loaded from config_db
_cached_models: dict = {}    # provider_id → list of valid models
active_sessions: dict = {}   # user_id → list of {role, content}
MAX_HISTORY = 20

# ── Config DB helpers ─────────────────────────────────────────────────────────
def _load_ai_config() -> dict:
    try:
        from database import load_config
        cfg = load_config()
        return cfg.get("ai_config", {})
    except Exception:
        return {}

def _save_ai_config(ai_cfg: dict):
    try:
        from database import load_config, save_config
        cfg = load_config()
        cfg["ai_config"] = ai_cfg
        save_config(cfg)
    except Exception as e:
        logger.error(f"[AI] save_ai_config: {e}")

def get_ai_config() -> dict:
    """Always return fresh config — reload from DB on each call."""
    global _ai_config
    fresh = _load_ai_config()
    if fresh:
        _ai_config = fresh
    return _ai_config

def _default_ai_config() -> dict:
    """Build default config from env vars."""
    cfg = {
        "primary": None,
        "backup": None,
        "providers": {}
    }
    for pid, info in PROVIDERS.items():
        key = os.getenv(info["env_key"], "").strip()
        cfg["providers"][pid] = {
            "enabled": bool(key),
            "api_key": key,
            "model": info["model"],
        }
    # Auto-select primary/backup
    enabled = [p for p in PROVIDERS if cfg["providers"][p]["enabled"]]
    if enabled:
        cfg["primary"] = enabled[0]
    if len(enabled) > 1:
        cfg["backup"] = enabled[1]
    return cfg

def _init_openai_compat(api_key: str, base_url: str, name: str):
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        logger.info(f"✅ {name} ready")
        return client
    except Exception as e:
        logger.error(f"[AI] {name} init failed: {e}")
        return None

def init_all():
    """Initialize all enabled providers from config."""
    global _clients, _ai_config
    fresh = _load_ai_config()
    if fresh and fresh.get("providers"):
        _ai_config = fresh
    elif not _ai_config or not _ai_config.get("providers"):
        _ai_config = _default_ai_config()
        _save_ai_config(_ai_config)

    # Auto-inject any provider keys set in environment variables if not in DB
    prov = _ai_config.setdefault("providers", {})
    cfg_changed = False
    for pid, info in PROVIDERS.items():
        env_key = os.getenv(info["env_key"], "").strip()
        if env_key:
            p_entry = prov.setdefault(pid, {"model": info["model"], "api_key": "", "enabled": False})
            if not p_entry.get("api_key"):
                p_entry["api_key"] = env_key
                p_entry["enabled"] = True
                cfg_changed = True
                if not _ai_config.get("primary"):
                    _ai_config["primary"] = pid
    if cfg_changed:
        _save_ai_config(_ai_config)

    _clients.clear()
    for pid, info in PROVIDERS.items():
        pcfg = _ai_config.get("providers", {}).get(pid, {})
        if not pcfg.get("enabled"):
            continue
        key = pcfg.get("api_key", "").strip()
        if not key:
            continue
        c = _init_openai_compat(key, info["base_url"], info["name"])
        if c:
            _clients[pid] = c

    # Auto-repair: if primary points to dead/missing provider, fix it
    primary = _ai_config.get("primary")
    if primary and primary not in _clients:
        new_primary = next(iter(_clients), None)
        _ai_config["primary"] = new_primary
        _save_ai_config(_ai_config)
        logger.warning(f"[AI] Primary '{primary}' not available, switched to '{new_primary}'")

    # Auto-repair: if primary is None but we have active clients, set one
    if not _ai_config.get("primary") and _clients:
        new_primary = next(iter(_clients))
        _ai_config["primary"] = new_primary
        _save_ai_config(_ai_config)
        logger.info(f"[AI] Auto-set primary to '{new_primary}'")

    logger.info(f"[AI] Clients ready: {list(_clients.keys())} | Primary: {_ai_config.get('primary')} | Backup: {_ai_config.get('backup')}")

# ── Dynamic Model Discovery ──────────────────────────────────────────────────
async def _get_valid_models(client, pid: str, default_model: str) -> list:
    global _cached_models
    if pid in _cached_models and _cached_models[pid]:
        return _cached_models[pid]

    # Try live query from /models endpoint
    try:
        models_resp = await asyncio.wait_for(client.models.list(), timeout=4.0)
        if models_resp and hasattr(models_resp, "data"):
            discovered = [m.id for m in models_resp.data if getattr(m, "id", None)]
            if discovered:
                pref_order = [
                    "llama-3.3-70b", "llama3.3-70b", "gpt-oss-120b", "gemma-4-31b",
                    "llama-3.1-8b", "llama3.1-8b", "llama-3.1-70b",
                    "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                    "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
                    "meta-llama/llama-3.1-8b-instruct:free",
                    "mistral-small-latest"
                ]
                ordered = []
                for p in pref_order:
                    if p in discovered and p not in ordered:
                        ordered.append(p)
                for d in discovered:
                    if d not in ordered:
                        ordered.append(d)
                _cached_models[pid] = ordered
                return ordered
    except Exception as e:
        logger.debug(f"[AI] {pid} live model discovery skipped: {e}")

    # Fallback model candidates if /models is not supported or timed out
    if pid == "cerebras" or "cerebras" in str(getattr(client, "base_url", "")).lower():
        candidates = [default_model, "llama-3.3-70b", "gpt-oss-120b", "gemma-4-31b", "llama3.3-70b", "llama-3.1-8b", "llama3.1-8b"]
    elif pid == "groq" or "groq" in str(getattr(client, "base_url", "")).lower():
        candidates = [default_model, "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    elif pid == "together":
        candidates = [default_model, "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"]
    elif pid == "openrouter":
        candidates = [default_model, "meta-llama/llama-3.1-8b-instruct:free", "google/gemini-2.0-flash-exp:free", "meta-llama/llama-3.3-70b-instruct:free"]
    else:
        candidates = [default_model]

    seen = set()
    res = [c for c in candidates if c and not (c in seen or seen.add(c))]
    _cached_models[pid] = res
    return res

# ── Database Story Context ───────────────────────────────────────────────────
_AI_DB_STORIES_CACHE = None
_AI_DB_STORIES_CACHE_TIME = 0

def get_db_stories_info() -> tuple:
    """Return (titles_list, genres_list) from both local DB and MongoDB catalog."""
    global _AI_DB_STORIES_CACHE, _AI_DB_STORIES_CACHE_TIME
    import time
    now = time.time()
    if _AI_DB_STORIES_CACHE is not None and (now - _AI_DB_STORIES_CACHE_TIME) < 300:
        return _AI_DB_STORIES_CACHE

    titles = []
    genres = []

    # 1. Local Database
    try:
        from database import load_db, load_genre_index
        db = load_db()
        if db:
            for k, v in db.items():
                t = v.get("text") or v.get("name") or k
                if t and t not in titles:
                    titles.append(t)
        
        g_map = load_genre_index()
        genres = list(g_map.keys()) if g_map else []
    except Exception as e:
        logger.debug(f"[AI] Error loading local db stories: {e}")

    # 2. MongoDB Database
    try:
        from mongo_search import _get_mongo_client, extract_smart_field
        col, client, _ = _get_mongo_client()
        if col is not None:
            cursor = col.find({}, {"story_name_en": 1, "story_name_hi": 1, "story_name": 1, "title": 1, "name": 1, "aliases": 1}).limit(2000)
            for doc in cursor:
                t = extract_smart_field(doc, "name", "")
                if t and t not in titles:
                    titles.append(t)
                for al in doc.get("aliases", []):
                    if al and al not in titles:
                        titles.append(al)
    except Exception as e_m:
        logger.debug(f"[AI] Error loading mongo db stories: {e_m}")

    _AI_DB_STORIES_CACHE = (titles, genres)
    _AI_DB_STORIES_CACHE_TIME = now
    return titles, genres

# ── API Calls ─────────────────────────────────────────────────────────────────
async def _call_openai_compat(client, user_id: int, payload: str, model: str, pid: str = "") -> str:
    history = _get_history(user_id) if user_id else []

    # Dynamic database context injection
    titles, genres = get_db_stories_info()
    story_context = ""
    if titles:
        sample_titles = ", ".join(f'"{t}"' for t in titles[:120])
        sample_genres = ", ".join(genres[:25]) if genres else "Romance, Thriller, Action, Drama, Horror, Fantasy, Crime, Magic"
        story_context = (
            f"\n\nCURRENT AVAILABLE STORIES IN DATABASE ({len(titles)} total):\n[{sample_titles}]\n"
            f"AVAILABLE GENRES:\n[{sample_genres}]\n\n"
            "STORY MATCHING & CHAT INSTRUCTIONS:\n"
            "1. When user asks for, searches for, or mentions any story (even with half-name, typo, character names, or conversational query):\n"
            "   - Match it against the CURRENT AVAILABLE STORIES list above.\n"
            "   - If matched, append: ##SEARCH: Exact_Matched_Story_Title## at the end of your reply!\n"
            "   - If the user asks for a story that is NOT in our database list, DO NOT fake a link or make up author/story details. Inform them politely that this story is not in our database yet and they can request it using /request [story_name].\n"
            "2. When user asks for genre recommendations (e.g., 'horror stories', 'romance story suggest karo', 'kaun si genre hai'):\n"
            "   - Suggest real genres/stories from the database and mention they can use /genre to browse all!\n"
            "3. When user is simply chatting (greetings, asking how you are, talking, flirting, joking, emojis, questions): reply naturally as Riya. DO NOT append ##SEARCH:## tag."
        )

    sys_content = SYSTEM_PROMPT + story_context
    messages = [{"role": "system", "content": sys_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": payload})

    candidates = await _get_valid_models(client, pid, model)

    last_err = None
    for target_model in candidates:
        try:
            resp = await client.chat.completions.create(
                model=target_model,
                messages=messages,
                temperature=0.78,
                max_tokens=700,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if "404" in err_str or "model_not_found" in err_str or "does not exist" in err_str:
                continue
            raise e
    if last_err:
        raise last_err
    return ""

async def _call_provider(pid: str, user_id: int, payload: str) -> Optional[str]:
    client = _clients.get(pid)
    if not client:
        return None
    info = PROVIDERS.get(pid)
    if not info:
        return None
    pcfg = _ai_config.get("providers", {}).get(pid, {})
    model = pcfg.get("model") or info["model"]
    try:
        return await _call_openai_compat(client, user_id, payload, model, pid=pid)
    except Exception as e:
        logger.warning(f"[AI] {pid} call failed: {e}")
        return None

async def _get_ai_raw(user_id: int, payload: str) -> str:
    # Reload config to catch any updates made since last call
    global _ai_config
    fresh = _load_ai_config()
    if fresh and fresh.get("providers"):
        _ai_config = fresh

    primary = _ai_config.get("primary")
    backup  = _ai_config.get("backup")

    if not _clients:
        init_all()

    if not _clients:
        logger.error("[AI] No providers initialized! Check API keys and openai package.")
        return ""

    # Try primary
    if primary and primary in _clients:
        result = await _call_provider(primary, user_id, payload)
        if result:
            return result
        logger.warning(f"[AI] Primary ({primary}) failed, trying backup...")

    # Try backup
    if backup and backup in _clients and backup != primary:
        result = await _call_provider(backup, user_id, payload)
        if result:
            return result
        logger.warning(f"[AI] Backup ({backup}) also failed.")

    # Last resort: try ANY available client
    for pid, client in _clients.items():
        if pid in (primary, backup):
            continue
        result = await _call_provider(pid, user_id, payload)
        if result:
            logger.info(f"[AI] Emergency fallback used: {pid}")
            return result

    # All providers tried first one again as final attempt
    first_pid = next(iter(_clients), None)
    if first_pid:
        logger.warning(f"[AI] All failed, final attempt with: {first_pid}")
        return await _call_provider(first_pid, user_id, payload) or ""

    return ""

# ── Session helpers ───────────────────────────────────────────────────────────
def _get_history(user_id: int) -> list:
    sess = active_sessions.get(user_id)
    if not isinstance(sess, list):
        active_sessions[user_id] = []
    return active_sessions[user_id]

def _save_turn(user_id: int, user_msg: str, assistant_msg: str):
    history = _get_history(user_id)
    history.append({"role": "user",      "content": user_msg})
    history.append({"role": "assistant", "content": assistant_msg})
    if len(history) > MAX_HISTORY * 2:
        active_sessions[user_id] = history[-(MAX_HISTORY * 2):]

def is_gemini_active(user_id: int) -> bool:
    return user_id in active_sessions

def activate_gemini(user_id: int):
    if user_id not in active_sessions:
        active_sessions[user_id] = []

def deactivate_gemini(user_id: int):
    active_sessions.pop(user_id, None)

# ── Intent detection ──────────────────────────────────────────────────────────
_STORY_KW = [
    "ka link", "ki link", "link do", "link bhejo", "link chahiye", "link milega",
    "wala link", "wali link", "story do", "story chahiye", "story dhundho",
    "episode do", "part do", "padna hai", "padhna hai", "dhundh do", "find karo",
    "sunna chahta", "sunna chahti", "khoj do", "kahani do", "story bhejo",
    "audiobook", "audio book", "give me", "find", "search", "looking for",
    "link of", "link for", "story of", "want to listen", "want to read"
]
_VOICE_KW = [
    "voice note", "voice mein bolo", "voice me bolo", "voice mein", "voice me",
    "awaaz mein", "awaaz me", "audio mein", "bol ke bhejo", "bolke sunao",
    "voice reply", "awaaz do",
]
_GREET_KW = [
    "hi", "hello", "hey", "hii", "namaste", "namaskar", "salaam",
    "good morning", "good evening", "good night", "good afternoon",
]
_THANKS_KW = ["thanks", "thank you", "shukriya", "dhanyavaad", "thankyou", "ty", "thx"]
_GUIDE_KW  = [
    "guide", "help", "kaise use", "kya kar sakti", "features", "commands",
    "kya karta hai", "explain karo", "kaise chalaye",
]
_NEW_TOPIC = [
    "by the way", "alag topic", "ek aur cheez", "naya sawaal",
    "alag baat", "kuch aur", "btw",
]
_SEARCH_RE   = re.compile(r"##\s*SEARCH:\s*(.*?)(?:##|$)", re.IGNORECASE | re.DOTALL)
_VOICE_RE    = re.compile(r"##\s*VOICE\s*##",  re.IGNORECASE)
_STOP_RE     = re.compile(r"##\s*STOP\s*##",   re.IGNORECASE)
_IMAGE_RE    = re.compile(r"##\s*IMAGE:\s*(.*?)(?:##|$)", re.IGNORECASE | re.DOTALL)

# Regex to extract exact text for voice: "voice mein yah bolo: [text]" or "bolo \"[text]\""
_VOICE_EXACT_RE = re.compile(
    r'(?:voice\s*(?:mein|me|note|message)?\s*(?:yah|ye|yeh|isko|sirf|bas)?\s*(?:bolo|bol|kaho|keh|sunao))'
    r'\s*[:\-–]?\s*["\u201c]?(.+?)["\u201d]?\s*$',
    re.IGNORECASE | re.DOTALL
)
# Regex for translate: "translate this: [text]" or "isko [lang] mein translate karo"
_TRANSLATE_RE = re.compile(
    r'(?:translate|anuvad|anuwad)',
    re.IGNORECASE
)
_TRANSLATE_TARGET_RE = re.compile(
    r'(?:translate|anuvad|anuwad)\s+(?:this|yah|ye|isko|kar)?\s*[:\-–]?\s*(.+)',
    re.IGNORECASE | re.DOTALL
)

# Natural greet replies — varied, human, no cringe
import random as _rnd
_GREET_REPLIES_HI = [
    "hi!",
    "hey!",
    "hello!",
    "haan, bolo?",
    "hi, kya ho raha hai?",
    "hey, kya chal raha hai?",
    "bol, kya kaam hai?",
    "haan?",
    "hello! kya chahiye?",
    "hi there!",
]
_GREET_REPLIES_EN = [
    "hey!",
    "hi! what's up?",
    "hello!",
    "hey, what do you need?",
    "hi there!",
    "hey, how can I help?",
]

def _detect_intent(text: str):
    lower = text.lower().strip()

    # ── Direct Story Entity in Text ────────────────────────────────────────────
    try:
        from search_engine import extract_story_from_text
        extracted_story, extracted_name = extract_story_from_text(text)
        if extracted_story and extracted_name:
            return "story", extracted_name
    except Exception:
        pass

    # ── Voice exact: "voice mein yah bolo: [text]" ─────────────────────────────
    vm = _VOICE_EXACT_RE.search(lower)
    if vm:
        extracted = vm.group(1).strip().strip('"\u201c\u201d\u2018\u2019').strip()
        if extracted:
            return "voice_exact", extracted

    # ── Translate intent ────────────────────────────────────────────────────────
    if _TRANSLATE_RE.search(lower):
        tm = _TRANSLATE_TARGET_RE.search(text)  # use original text for case
        extracted = tm.group(1).strip() if tm else ""
        return "translate", extracted or text

    if any(kw in lower for kw in _VOICE_KW):
        return "voice", None
    for kw in _STORY_KW:
        if kw in lower:
            parts = lower.split(kw)
            cand = parts[0].strip().strip('"\'').strip()
            if 2 <= len(cand) <= 60:
                return "story", cand
            if len(parts) > 1:
                cand2 = parts[1].strip().strip('"\'').strip()
                if 2 <= len(cand2) <= 60:
                    return "story", cand2
            return "story", text.strip()
    if any(kw in lower for kw in _GUIDE_KW):
        return "guide", None
    if lower in _GREET_KW or any(lower.startswith(g) for g in _GREET_KW):
        return "greet", None
    if any(kw in lower for kw in _THANKS_KW):
        return "thanks", None
    return "chat", None

def parse_ai_reply(raw: str):
    if not raw:
        return "", None, False, False, None
    wants_voice = bool(_VOICE_RE.search(raw))
    wants_stop  = bool(_STOP_RE.search(raw))
    m = _SEARCH_RE.search(raw)
    search_query = m.group(1).strip() if m else None
    m2 = _IMAGE_RE.search(raw)
    img_prompt = m2.group(1).strip() if m2 else None
    clean = _SEARCH_RE.sub("", raw)
    clean = _VOICE_RE.sub("", clean)
    clean = _STOP_RE.sub("", clean)
    clean = _IMAGE_RE.sub("", clean)
    return clean.strip(), search_query, wants_voice, wants_stop, img_prompt

# ── Main public function ──────────────────────────────────────────────────────
async def process_gemini_message(user_id: int, user_name: str, message_text: str) -> tuple:
    """Returns (reply_text, search_query, wants_voice, wants_stop, img_prompt)."""

    # 1. STOP check
    stop_kw = ["chup", "shutup", "shut up", "band karo", "mat bolo", "bas karo", "stop replying"]
    if any(kw in message_text.lower() for kw in stop_kw):
        active_sessions.pop(user_id, None)
        return ("Thik hai, main chup ho rahi hoon. Jab zarurat ho toh bula lena 😊",
                None, False, True, None)

    # 2. Intent detection
    intent, rule_story = _detect_intent(message_text)
    logger.info(f"[AI] user={user_name} | intent={intent} | msg='{message_text[:50]}'")

    # 3. Story route — return search_query so stories_bot handles human typing and natural edit
    if intent == "story" and rule_story:
        return ("", rule_story, False, False, None)

    # 3a. Voice exact — bypass AI, speak ONLY the given text
    if intent == "voice_exact" and rule_story:  # rule_story holds extracted text here
        return (rule_story, None, True, False, None)

    # 3b. Translate intent — strict instruction to AI
    if intent == "translate":
        text_to_translate = rule_story or message_text  # rule_story = extracted text
        # Detect target language from message
        lower_msg = message_text.lower()
        if "hindi" in lower_msg or "devanagari" in lower_msg or "devanagri" in lower_msg:
            target_lang = "Hindi (Devanagari script only, no Roman)"
        elif "english" in lower_msg or "angrezi" in lower_msg:
            target_lang = "English"
        elif "hinglish" in lower_msg:
            target_lang = "Hinglish (Roman Hindi)"
        else:
            target_lang = "Hindi"  # default
        strict_payload = (
            f"Translate the following text to {target_lang}.\n"
            f"RULES: Output ONLY the translated text. No explanations. No extra sentences. "
            f"No greetings. No 'Here is the translation'. Just the translation.\n\n"
            f"Text to translate:\n{text_to_translate}"
        )
        raw = await _get_ai_raw(user_id, strict_payload)
        ai_text = raw.strip() if raw else "Translation failed, try again."
        # Strip any accidental ##markers##
        ai_text, _, _, _, _ = parse_ai_reply(ai_text)
        if ai_text:
            _save_turn(user_id, message_text, ai_text)
        return (ai_text, None, False, False, None)

    # 3c. Greet bypass — pick a natural reply from pool, no AI needed
    if intent == "greet":
        lower_msg = message_text.lower()
        if any(w in lower_msg for w in ["hello", "hi", "hey", "hii"]):
            reply = _rnd.choice(_GREET_REPLIES_EN)
        else:
            reply = _rnd.choice(_GREET_REPLIES_HI)
        return (reply, None, False, False, None)

    # 4. New topic → trim history
    lower = message_text.lower()
    if any(sig in lower for sig in _NEW_TOPIC):
        active_sessions[user_id] = []

    # 5. Build sys_note
    words = len(message_text.strip().split())
    is_guide = intent == "guide"
    is_voice = intent == "voice"

    if is_guide and is_voice:
        sys_note = "[INSTRUCTION: User wants complete bot guide as voice note. Speak naturally in Hinglish. Cover all features. Do NOT shorten.]"
    elif is_guide:
        sys_note = "[INSTRUCTION: Write a FULL structured guide covering: story search, /request, /saved, /new, /subscribe, voice notes. Use clear formatting.]"
    elif is_voice:
        sys_note = "[INSTRUCTION: VOICE NOTE — provide ONLY the spoken words the user asked for. Do NOT add extra sentences, greetings, or commentary. Speak naturally.]"
    elif words <= 5:
        sys_note = "[INSTRUCTION: Very short message. Reply in max 1-2 casual lines.]"
    else:
        sys_note = "[INSTRUCTION: Reply naturally. Match user's tone and language.]"

    payload = f"{user_name}: {message_text}\n\n{sys_note}"

    # 6. Call AI
    raw = await _get_ai_raw(user_id, payload)
    logger.info(f"[AI] raw: '{raw[:100]}'")

    ai_text, ai_search, ai_voice, ai_stop, img_prompt = parse_ai_reply(raw)

    if ai_text:
        _save_turn(user_id, f"{user_name}: {message_text}", ai_text)

    # Allow AI to trigger search if it detected a story request that our simple regex missed
    if ai_search and not ai_text:
        # If AI only returned a search tag but no text, give a default
        lower_msg = message_text.lower()
        if "english" in lower_msg or not any(hw in lower_msg for hw in ["hai", "karo", "do", "bhejo", "chahiye"]):
            ai_text = "Here it is! 📖"
        else:
            ai_text = "Yeh rahi! 📖"

    if is_voice:
        ai_voice = True

    return (ai_text, ai_search, ai_voice, ai_stop, img_prompt)

async def ai_resolve_story_name(query: str) -> Optional[str]:
    """
    High-Precision AI Story Resolver:
    Uses RapidFuzz to pre-filter candidate titles from local and MongoDB database,
    then uses AI to resolve typos/phonetics.
    Strictly verifies that the returned story is genuine and not a random hallucination.
    """
    titles, _ = get_db_stories_info()
    if not query or len(query.strip()) < 2:
        return None

    from search_engine import search_story_exact_or_alias
    direct = search_story_exact_or_alias(query)
    if direct:
        return direct.get("text") or direct.get("name") or query

    try:
        from mongo_search import search_story_from_mongo_sync
        m_direct = search_story_from_mongo_sync(query)
        if m_direct:
            return m_direct.get("text") or m_direct.get("name") or query
    except Exception:
        pass

    from rapidfuzz import fuzz
    from database import normalize_text
    qn = normalize_text(query)

    # Pre-filter top candidates from the entire database by normalized similarity
    candidates_with_score = []
    for t in (titles or []):
        tn = normalize_text(t)
        score = max(fuzz.partial_ratio(qn, tn), fuzz.token_sort_ratio(qn, tn), fuzz.ratio(qn, tn))
        if score >= 50:
            candidates_with_score.append((t, score))

    candidates_with_score.sort(key=lambda x: x[1], reverse=True)
    candidate_titles = [c[0] for c in candidates_with_score[:25]]

    # If no plausible candidates exist in the entire DB, the story is definitely not in the DB
    if not candidate_titles:
        return None

    prompt = (
        f"You are a strict story database resolver.\n"
        f"User query: \"{query}\"\n"
        f"Available candidate titles:\n{candidate_titles}\n\n"
        "Rules:\n"
        "1. If the user query is clearly searching for one of the candidate titles (even with typos or phonetics), output ONLY that EXACT title from the list.\n"
        "2. If the user query is searching for a DIFFERENT story that is not in the list, or is casual conversation, you MUST output ONLY 'NONE'.\n"
        "3. NEVER make up or guess an incorrect story title.\n"
        "Output:"
    )

    try:
        raw = await _get_ai_raw(0, prompt)
        res = raw.strip().strip('"\'').strip()
        if res and res.upper() != "NONE" and len(res) < 100:
            match = search_story_exact_or_alias(res)
            if not match:
                try:
                    from mongo_search import search_story_from_mongo_sync
                    match = search_story_from_mongo_sync(res)
                except Exception:
                    match = None
            if match:
                matched_title = match.get("text") or match.get("name") or res
                m_norm = normalize_text(matched_title)
                # Double check confidence threshold
                if fuzz.partial_ratio(qn, m_norm) >= 60 or fuzz.token_sort_ratio(qn, m_norm) >= 60:
                    try:
                        from database import save_learned_alias
                        save_learned_alias(query, matched_title)
                    except Exception:
                        pass
                    return matched_title
    except Exception as e:
        logger.debug(f"[AI] ai_resolve_story_name error: {e}")
    return None

# ── Provider management API ───────────────────────────────────────────────────
def get_active_provider() -> str:
    primary = _ai_config.get("primary")
    backup  = _ai_config.get("backup")
    p_name  = PROVIDERS.get(primary, {}).get("name", "None") if primary else "None"
    b_name  = PROVIDERS.get(backup,  {}).get("name", "None") if backup  else "None"
    active_list = ", ".join(PROVIDERS.get(p, {}).get("name", p) for p in _clients)
    return f"Primary: {p_name} | Backup: {b_name} | Ready: [{active_list or 'none'}]"

def set_provider_key(pid: str, api_key: str, enabled: bool = True):
    """Add/update a provider's API key and enabled state."""
    global _ai_config
    cfg = get_ai_config()
    if "providers" not in cfg:
        cfg["providers"] = {}
    if pid not in cfg["providers"]:
        info = PROVIDERS.get(pid, {})
        cfg["providers"][pid] = {"model": info.get("model", ""), "enabled": False, "api_key": ""}
    cfg["providers"][pid]["api_key"] = api_key
    cfg["providers"][pid]["enabled"] = enabled
    # Auto-set as primary if none is set and key is valid
    if enabled and api_key and not cfg.get("primary"):
        cfg["primary"] = pid
        logger.info(f"[AI] Auto-set '{pid}' as primary (was None)")
    _save_ai_config(cfg)
    _ai_config = cfg
    init_all()

def set_primary(pid: str):
    global _ai_config
    cfg = get_ai_config()
    cfg["primary"] = pid
    _save_ai_config(cfg)
    _ai_config = cfg
    init_all()  # reinit so primary takes effect immediately

def set_backup(pid: str):
    global _ai_config
    cfg = get_ai_config()
    cfg["backup"] = pid
    _save_ai_config(cfg)
    _ai_config = cfg
    init_all()  # reinit so backup takes effect immediately

def toggle_provider(pid: str, enabled: bool):
    global _ai_config
    cfg = get_ai_config()
    if "providers" not in cfg:
        cfg["providers"] = {}
    if pid not in cfg["providers"]:
        cfg["providers"][pid] = {"model": PROVIDERS[pid]["model"], "api_key": "", "enabled": False}
    cfg["providers"][pid]["enabled"] = enabled
    # If disabling primary/backup, clear them
    if not enabled:
        if cfg.get("primary") == pid:
            cfg["primary"] = None
        if cfg.get("backup") == pid:
            cfg["backup"] = None
    _save_ai_config(cfg)
    _ai_config = cfg
    init_all()

async def classify_content_with_ai(text: str = None, photo_bytes: bytes = None) -> str:
    """
    Analyzes text using the primary configured OpenAI-compatible AI provider.
    Returns: 'SPAM', 'NSFW', or 'CLEAN'.
    """
    cfg = get_ai_config()
    primary = cfg.get("primary")
    if not primary or primary not in _clients:
        return "CLEAN"

    client = _clients[primary]
    info = PROVIDERS.get(primary, {})
    model = info.get("model", "")

    if not text or not text.strip():
        return "CLEAN"

    mod_sys_prompt = (
        "You are a strict content moderator. "
        "Analyze the message and reply with EXACTLY ONE word: 'SPAM', 'NSFW', or 'CLEAN'.\n"
        "- SPAM: Advertisements, promo links, scams.\n"
        "- NSFW: Pornography, explicit sexual content, nudity.\n"
        "- CLEAN: Normal conversation."
    )

    candidate_models = [model]
    if "cerebras" in str(client.base_url).lower():
        candidate_models = ["llama3.3-70b", "llama3.1-8b", "llama-3.3-70b", "llama-3.1-8b", model]

    for m_target in candidate_models:
        try:
            resp = await client.chat.completions.create(
                model=m_target,
                messages=[
                    {"role": "system", "content": mod_sys_prompt},
                    {"role": "user", "content": text.strip()}
                ],
                temperature=0.0,
                max_tokens=10,
            )
            res = resp.choices[0].message.content.strip().upper()
            if "SPAM" in res:
                return "SPAM"
            elif "NSFW" in res:
                return "NSFW"
            return "CLEAN"
        except Exception as e:
            if "404" in str(e) or "model_not_found" in str(e):
                continue
            logger.warning(f"[AI-Moderator] Classification failed with model {m_target}: {e}")
            return "CLEAN"
    return "CLEAN"

# Backward compatibility aliases
classify_content_with_gemini = classify_content_with_ai

def reinit_gemini(key: str):
    pass

# ── Startup ─────────────────────────────────────────────────────────────
if __name__ != "__main__":
    init_all()
