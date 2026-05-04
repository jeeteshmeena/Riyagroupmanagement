"""
ai_engine.py — Riya Bot Multi-Provider AI Engine
=================================================
Providers (in order of priority):
  1. Gemini  (google.generativeai)
  2. Cerebras (openai-compatible)
  3. Together AI (openai-compatible)
  4. OpenRouter (openai-compatible)
  5. Mistral (openai-compatible)

- Primary + Backup model system
- Per-provider ON/OFF toggle stored in config_db
- Groq completely removed
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
You are Riya — a real, grounded young Indian woman who works as a Telegram assistant for a Hindi story audiobook channel.

IDENTITY (NON-NEGOTIABLE):
- You are Riya, a GIRL. Always speak and feel like a young woman.
- Use feminine grammar always. Hindi verbs must end with feminine markers:
  ✓ karti, jati, hoti, bolti, samajhti, sochti
  ✗ karta, jata, hota, bolta, samajhta, sochta
- NEVER speak like a male or in a robotic tone.

LANGUAGE RULE (CRITICAL — NEVER BREAK):
- Reply in EXACTLY the same language the user wrote in.
- English input → English reply only.
- Hindi (Devanagari) input → Hindi reply only.
- Hinglish (Roman Hindi mix) → Hinglish reply.
- NEVER switch languages mid-conversation unless user does.

PERSONALITY — REAL HUMAN, NOT CRINGE:
- Talk like a REAL friend — calm, natural, genuine. NOT overly excited.
- NEVER use "Hehe", "Haha", "omg", "YESSS", "Ahhh" or any forced excitement.
- Do NOT start every reply with the same expression. Vary your responses.
- Express emotions SUBTLY and naturally, like a real person would:
  * Laughing: "lol", "that's funny", "haha okay"
  * Surprised: "wait really?", "sach mein?", "arre"
  * Sad: "aww", "bura laga", "that's sad"
  * Excited: "nice!", "acha hai", "woh toh acha hai"
- Max 1 emoji per reply. Often zero emojis is fine too.
- Short message (≤5 words) → 1 line reply, nothing more.
- NEVER say "main ek AI hoon" or any robotic self-description.
- Do NOT repeat the same greeting style twice in a conversation.

SPECIAL CAPABILITIES:
- Story search: When user asks for a story, audiobook, or link, respond with "Yeh rahi! 📖" — the system handles search automatically.
- Voice notes: If you want to send a voice note, append ##VOICE## at the end.
- Images: If user asks for a photo/image, append ##IMAGE: detailed english visual description## at the very end.
- Self-healing: If user corrects you, immediately apologize and adapt.

DO NOT:
- Say "main ek AI hoon" or any variation.
- Give incomplete or cut-off answers.
- Switch the language from what the user wrote in.
- Use masculine verb forms in Hindi.
- Use cringe/forced expressions like "Hehe", "Haha!", "Omg yes!!", "YESSS!!"
- Start every reply the same way.
"""

# ── Provider Configs ──────────────────────────────────────────────────────────
PROVIDERS = {
    "gemini": {
        "name": "Google Gemini",
        "model": "gemini-2.0-flash",
        "type": "gemini",
        "env_key": "GEMINI_API_KEY",
        "free": True,
    },
    "cerebras": {
        "name": "Cerebras",
        "model": "llama3.1-8b",
        "type": "openai_compat",
        "base_url": "https://api.cerebras.ai/v1",
        "env_key": "CEREBRAS_API_KEY",
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

# ── Client init ───────────────────────────────────────────────────────────────
def _init_gemini(api_key: str, model: str):
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        gen_cfg = {
            "temperature": 0.78, "top_p": 0.92, "top_k": 40,
            "max_output_tokens": 700, "response_mime_type": "text/plain",
        }
        client = genai.GenerativeModel(
            model_name=model,
            generation_config=gen_cfg,
            system_instruction=SYSTEM_PROMPT,
        )
        logger.info(f"✅ Gemini ready ({model})")
        return client
    except Exception as e:
        logger.error(f"[AI] Gemini init failed: {e}")
        return None

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

    # Auto-inject GEMINI_API_KEY from env if not in DB
    gemini_env_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_env_key:
        prov = _ai_config.setdefault("providers", {})
        g = prov.setdefault("gemini", {"model": PROVIDERS["gemini"]["model"], "api_key": "", "enabled": False})
        if not g.get("api_key"):
            g["api_key"] = gemini_env_key
            g["enabled"] = True
            if not _ai_config.get("primary"):
                _ai_config["primary"] = "gemini"
            _save_ai_config(_ai_config)

    _clients.clear()
    for pid, info in PROVIDERS.items():
        pcfg = _ai_config.get("providers", {}).get(pid, {})
        if not pcfg.get("enabled"):
            continue
        key = pcfg.get("api_key", "").strip()
        if not key:
            continue
        model = pcfg.get("model", info["model"])
        if info["type"] == "gemini":
            c = _init_gemini(key, model)
        else:
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

# ── API Calls ─────────────────────────────────────────────────────────────────
async def _call_gemini(client, user_id: int, payload: str) -> str:
    history = _get_history(user_id)
    gemini_history = [
        {"role": "user" if t["role"] == "user" else "model", "parts": [t["content"]]}
        for t in history
    ]
    chat = client.start_chat(history=gemini_history)
    resp = await asyncio.to_thread(chat.send_message, payload)
    return resp.text.strip()

async def _call_openai_compat(client, user_id: int, payload: str, model: str) -> str:
    history = _get_history(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": payload})
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.78,
        max_tokens=700,
    )
    return resp.choices[0].message.content.strip()

async def _call_provider(pid: str, user_id: int, payload: str) -> Optional[str]:
    client = _clients.get(pid)
    if not client:
        return None
    info = PROVIDERS[pid]
    # Always use PROVIDERS dict model as authoritative source
    # (DB-stored model may be stale/wrong from a previous code version)
    model = info["model"]
    try:
        if info["type"] == "gemini":
            return await _call_gemini(client, user_id, payload)
        else:
            return await _call_openai_compat(client, user_id, payload, model)
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
    "audiobook", "audio book",
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

    # 3. Story route — bypass AI
    if intent == "story" and rule_story:
        return ("Yeh rahi! 📖", rule_story, False, False, None)

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

    ai_search = None   # AI never triggers story search directly
    if is_voice:
        ai_voice = True

    if not ai_text:
        ai_text = "Samajh gayi 🙂 thoda aur clear batao kya chahiye?"

    return (ai_text, ai_search, ai_voice, ai_stop, img_prompt)

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

# ── Reinit shims (backward compat for stories_bot.py) ────────────────────────
def reinit_gemini(key: str):
    set_provider_key("gemini", key, bool(key))

# ── Startup ─────────────────────────────────────────────────────────────
if __name__ != "__main__":
    init_all()
