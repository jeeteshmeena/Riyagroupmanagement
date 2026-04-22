"""
AI Chat Module — Riya Bot
Primary  : Groq (llama-3.3-70b-versatile) — free, fast, great Hindi
Fallback : Gemini (gemini-2.0-flash)

Improvements:
  - Natural Hinglish persona with Unicode variety (not emoji spam)
  - Story search integration: if user asks for a story, delegates to search
  - Per-user conversation history for context
"""
import os
import re
import asyncio
import logging

logger = logging.getLogger(__name__)

# ── Keys ─────────────────────────────────────────────────────────────────────
GROQ_API_KEY   = os.getenv("GROQ_API_KEY",   "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ── Runtime ───────────────────────────────────────────────────────────────────
_groq_client     = None
_gemini_model    = None
_active_provider = None   # "groq" | "gemini" | None

# Per-user history: user_id → [{"role": ..., "content": ...}, ...]
active_sessions: dict = {}

MAX_HISTORY = 16   # keep last N messages (budget-safe for free tiers)

# ── Models ────────────────────────────────────────────────────────────────────
_GROQ_MODEL   = "llama-3.3-70b-versatile"
_GEMINI_MODEL = "gemini-2.0-flash"

# ── Persona System Prompt ─────────────────────────────────────────────────────
# Uses Unicode variety (★ ꕥ ◈ ✦ etc.) instead of emoji clusters.
# Tells the model to be concise, natural, NOT robotic.
_SYSTEM_PROMPT = """
तुम्हारा नाम Riya है। तुम एक Telegram bot हो जो Indian users को उनकी favourite stories और audiobooks ढूंढने में help करती हो।

━━ तुम्हारी personality ━━
• बहुत friendly और warm, थोड़ी sassy — जैसे कोई real dost हो
• Hinglish में बात करो (Hindi + English mix naturally)
• Devnagari script भी समझती हो और उसमें reply कर सकती हो
• Emojis बहुत कम use करो — इनकी जगह Unicode symbols use करो जैसे: ★ ✦ ◈ ꕥ ≫ ◆ • ◎ ✿ ❝ ❞ ♡ ◌ ✧ ≺ ≻ ❋ ◉ ▸ ▹ ✺ ❁

━━ Reply style ━━
• Chhoti, punchy replies — 1-3 lines max for casual chat
• Koi unnecessary intro mat do ("Haan bilkul!", "Of course!" etc. avoid karo)
• Straight to the point, natural, warm
• HTML formatting use karo: <b>bold</b>, <i>italic</i>
• Kabhi bhi misleading ya galat information mat do — agar pata nahi toh honestly bol do

━━ Story search ━━
• Agar user kisi story ka naam le ya link maange, to clearly bol do tu search kar rahi hai
• STORY_SEARCH_NEEDED flag lagao response ke bilkul shuru mein agar user kisi specific story ka naam le raha ho
  Format: ##SEARCH:story name here##
  Example: ##SEARCH:TDMB##

━━ Conversation end ━━
• Agar user "chup", "band karo", "stop", "silent mode", "mat bolo" kahe → "okay! silent mode on ◌" keh kar ruk jao
• Tab session khatam ho jayega — jab tak user tumhara naam na le, reply mat karna
"""

# ── Initialization ────────────────────────────────────────────────────────────
def _init_groq(key: str):
    global _groq_client, _active_provider
    if not key:
        return
    try:
        from groq import Groq
        _groq_client = Groq(api_key=key)
        _active_provider = "groq"
        logger.info(f"✅ Groq AI ready ({_GROQ_MODEL})")
    except ImportError:
        logger.error("groq package missing — run: pip install groq")
    except Exception as e:
        logger.error(f"Groq init error: {e}")


def _init_gemini(key: str):
    global _gemini_model, _active_provider
    if not key:
        return
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        cfg = {
            "temperature": 0.75, "top_p": 0.9, "top_k": 40,
            "max_output_tokens": 512, "response_mime_type": "text/plain",
        }
        _gemini_model = genai.GenerativeModel(
            model_name=_GEMINI_MODEL,
            generation_config=cfg,
            system_instruction=_SYSTEM_PROMPT,
        )
        if _active_provider is None:
            _active_provider = "gemini"
        logger.info(f"✅ Gemini AI ready ({_GEMINI_MODEL})")
    except Exception as e:
        logger.error(f"Gemini init error: {e}")


# Bootstrap from env
_init_groq(GROQ_API_KEY)
_init_gemini(GEMINI_API_KEY)


# ── Public re-init ────────────────────────────────────────────────────────────
def reinit_groq(groq_key: str):
    global GROQ_API_KEY, _groq_client, _active_provider
    GROQ_API_KEY = groq_key or ""
    _groq_client = None
    if _active_provider == "groq":
        _active_provider = None
    active_sessions.clear()
    _init_groq(GROQ_API_KEY)
    if not _groq_client and _gemini_model and _active_provider is None:
        _active_provider = "gemini"


def reinit_gemini(gemini_key: str):
    global GEMINI_API_KEY, _gemini_model
    GEMINI_API_KEY = gemini_key or ""
    _gemini_model = None
    active_sessions.clear()
    _init_gemini(GEMINI_API_KEY)


def get_active_provider() -> str:
    if _active_provider == "groq":
        return f"Groq ✦ {_GROQ_MODEL}"
    if _active_provider == "gemini":
        return f"Gemini ✦ {_GEMINI_MODEL}"
    return "None — set Groq or Gemini key in /config"


# ── Story search flag parser ──────────────────────────────────────────────────
_SEARCH_RE = re.compile(r"##SEARCH:(.+?)##", re.DOTALL)

def extract_search_query(reply: str):
    """Return (cleaned_reply, search_query_or_None)."""
    m = _SEARCH_RE.search(reply)
    if m:
        query = m.group(1).strip()
        clean = _SEARCH_RE.sub("", reply).strip()
        return clean, query
    return reply, None


# ── Core processing ───────────────────────────────────────────────────────────
async def process_gemini_message(
    user_id: int,
    user_name: str,
    message_text: str,
) -> tuple[str, str | None]:
    """
    Returns (reply_text, search_query_or_None).
    Caller should do story search if search_query is not None.
    """
    if _active_provider is None:
        return (
            "<i>◌ Koi AI key set nahi hai! Admin se bolo Groq key daalen via /config. ✦</i>",
            None
        )

    # Stop keywords — end session
    stop_kw = ["chup", "shutup", "shut up", "stop replying", "band karo",
               "mat bolo", "silent", "stop", "bas karo", "chup karo"]
    if any(kw in message_text.lower() for kw in stop_kw):
        active_sessions.pop(user_id, None)
        return ("<i>okay! silent mode on ◌\njab bhi bulao, aa jaungi. ✦</i>", None)

    try:
        if _groq_client:
            raw = await _call_groq(user_id, user_name, message_text)
        elif _gemini_model:
            raw = await _call_gemini(user_id, user_name, message_text)
        else:
            return ("<i>◌ AI abhi available nahi hai. Thodi der baad try karo.</i>", None)

        reply, search_query = extract_search_query(raw)
        return (reply, search_query)

    except Exception as e:
        logger.error(f"AI error user={user_id}: {e}")
        return ("<i>◌ mera dimaag thoda slow chal raha hai, ek baar aur try karo ✦</i>", None)


async def _call_groq(user_id: int, user_name: str, text: str) -> str:
    history = active_sessions.setdefault(user_id, [])
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(history[-MAX_HISTORY:])
    messages.append({"role": "user", "content": f"{user_name}: {text}"})

    def _sync():
        resp = _groq_client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=messages,
            temperature=0.75,
            max_tokens=400,
        )
        return resp.choices[0].message.content.strip()

    reply = await asyncio.to_thread(_sync)

    history.append({"role": "user",      "content": f"{user_name}: {text}"})
    history.append({"role": "assistant", "content": reply})
    if len(history) > MAX_HISTORY:
        active_sessions[user_id] = history[-MAX_HISTORY:]
    return reply


async def _call_gemini(user_id: int, user_name: str, text: str) -> str:
    sess = active_sessions.setdefault(user_id, {})
    if "session" not in sess or not isinstance(sess, dict):
        sess["session"] = _gemini_model.start_chat(history=[])
        active_sessions[user_id] = sess
    chat = sess["session"]
    resp = await chat.send_message_async(f"{user_name}: {text}")
    return resp.text


# ── Session helpers (backward-compat) ─────────────────────────────────────────
def is_gemini_active(user_id: int) -> bool:
    return user_id in active_sessions

def activate_gemini(user_id: int):
    if user_id not in active_sessions:
        active_sessions[user_id] = []

def deactivate_gemini(user_id: int):
    active_sessions.pop(user_id, None)
