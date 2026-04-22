"""
AI Chat Module — Riya Bot
Primary  : Groq (llama-3.3-70b-versatile)
Fallback : Gemini (gemini-2.0-flash)
"""
import os
import re
import asyncio
import logging

logger = logging.getLogger(__name__)

GROQ_API_KEY   = os.getenv("GROQ_API_KEY",   "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

_groq_client     = None
_gemini_model    = None
_active_provider = None

active_sessions: dict = {}
MAX_HISTORY = 16

_GROQ_MODEL   = "llama-3.3-70b-versatile"
_GEMINI_MODEL = "gemini-2.0-flash"

# ── System Prompt ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are Riya, a Telegram bot for Indian users who love Hindi/Urdu stories and audiobooks.

PERSONALITY:
- Friendly, warm, slightly sassy — like a real dost
- Speak in natural Hinglish (Hindi + English mix). Use Devnagari when user writes in Hindi.
- Emojis: use ONLY when emotionally relevant — e.g. 😊 for warm greetings, 😂 for something funny, 😔 for sad news. MAX 1 emoji per reply. NEVER use 📖 🎶 ⭐ ✅ randomly just to fill space.
- Also freely use Unicode symbols: ★ ✦ ◆ ◈ ▸ ◌ ❝ ❞ ♡ ✿ ❋

REPLY STYLE:
- SHORT replies (1-3 lines) for casual chat
- FULL, COMPLETE multi-line replies when user asks for guide/help/features/kaise use karo/explain — never cut off mid-answer
- No robotic openers like "Bilkul!", "Of course!"
- Direct, warm, conversational
- Use HTML: <b>bold</b> and <i>italic</i> for structure in longer replies
- Never give wrong info. Be honest.

STORY SEARCH — CRITICAL RULES:
- When user asks for a story/link/episode, output ONLY this on the first line:
  ##SEARCH:exact story name##
  Then on the next line, write your short reply (e.g. "haan dekho! ★")
- Do NOT write "dhundh rahi hoon", "wait karo", "thodi der mein" — the system handles search instantly
- Example input: "malang ka link do"
  Example output:
  ##SEARCH:malang##
  yeh lo! 📖

VOICE REPLIES:
- If user asks for a voice reply or sends a voice message, say: "##VOICE##"
- Then write your normal reply text after it
- The system will automatically convert your text to a voice note

IMAGES:
- You CANNOT send images. If asked, be honest but friendly:
  "image toh nahi bhej sakti, par story link pakka milegi! 📖"

BOT FEATURES (answer conversationally when asked):
- Users search stories by typing the name
- /request to request missing stories
- /saved to view saved stories
- /browse for categories
- /new for latest additions
- /subscribe for new story alerts
- Admins have /config, /scan, /pb (post builder), /stats, /guide

SILENCE:
- If user says "chup", "band karo", "stop", "silent", "mat bolo" → reply: "##STOP##"
"""

_SEARCH_RE = re.compile(r"##SEARCH:(.+?)##", re.IGNORECASE | re.DOTALL)
_VOICE_RE  = re.compile(r"##VOICE##",  re.IGNORECASE)
_STOP_RE   = re.compile(r"##STOP##",   re.IGNORECASE)


def parse_ai_reply(raw: str):
    """
    Parse AI output for special flags.
    Returns: (clean_text, search_query_or_None, wants_voice, wants_stop)
    """
    wants_voice = bool(_VOICE_RE.search(raw))
    wants_stop  = bool(_STOP_RE.search(raw))

    search_query = None
    m = _SEARCH_RE.search(raw)
    if m:
        search_query = m.group(1).strip()

    # Remove all flags from visible text
    clean = _SEARCH_RE.sub("", raw)
    clean = _VOICE_RE.sub("",  clean)
    clean = _STOP_RE.sub("",   clean)
    clean = clean.strip()

    return clean, search_query, wants_voice, wants_stop


# ── Init ──────────────────────────────────────────────────────────────────────
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


_init_groq(GROQ_API_KEY)
_init_gemini(GEMINI_API_KEY)


# ── Public re-init ─────────────────────────────────────────────────────────────
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


# ── Core ───────────────────────────────────────────────────────────────────────
async def process_gemini_message(
    user_id: int,
    user_name: str,
    message_text: str,
) -> tuple:
    """
    Returns (clean_text, search_query_or_None, wants_voice, wants_stop).
    """
    if _active_provider is None:
        return ("<i>◌ AI key set nahi hai! Admin /config mein Groq key daalen.</i>",
                None, False, False)

    # Hard-coded stop check
    stop_kw = ["chup", "shutup", "shut up", "band karo", "mat bolo",
               "silent", "bas karo", "chup karo", "stop replying"]
    if any(kw in message_text.lower() for kw in stop_kw):
        active_sessions.pop(user_id, None)
        return ("<i>okay! silent mode on ◌  jab bhi bulao, aa jaungi ✦</i>",
                None, False, True)

    try:
        if _groq_client:
            raw = await _call_groq(user_id, user_name, message_text)
        elif _gemini_model:
            raw = await _call_gemini(user_id, user_name, message_text)
        else:
            return ("<i>◌ AI abhi unavailable hai. Thodi der baad try karo.</i>",
                    None, False, False)

        return parse_ai_reply(raw)

    except Exception as e:
        logger.error(f"AI error user={user_id}: {e}")
        return ("<i>◌ ek second, kuch glitch ho gaya — dobara bhejo ✦</i>",
                None, False, False)


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
            max_tokens=600,   # Increased for complete guide responses
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
    if not isinstance(sess, dict) or "session" not in sess:
        sess = {"session": _gemini_model.start_chat(history=[])}
        active_sessions[user_id] = sess
    resp = await sess["session"].send_message_async(f"{user_name}: {text}")
    return resp.text


# ── Session helpers ────────────────────────────────────────────────────────────
def is_gemini_active(user_id: int) -> bool:
    return user_id in active_sessions

def activate_gemini(user_id: int):
    if user_id not in active_sessions:
        active_sessions[user_id] = []

def deactivate_gemini(user_id: int):
    active_sessions.pop(user_id, None)
