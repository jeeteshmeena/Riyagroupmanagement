"""
AI Chat Module — Riya Bot
Primary  : Groq (llama-3.3-70b-versatile)
Fallback : Gemini (gemini-2.0-flash)

Architecture:
  1. Rule-based intent detection FIRST — instant, always works
  2. AI model call — enriches the response
  3. Smart fallback — never shows robotic/wrong replies
"""
import os
import re
import random
import asyncio
import logging

logger = logging.getLogger(__name__)

GROQ_API_KEY   = os.getenv("GROQ_API_KEY",   "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

_groq_client     = None
_gemini_model    = None
_active_provider = None

active_sessions: dict = {}
MAX_HISTORY = 12  # 6 turns — enough for context, not too much for token limits

_GROQ_MODEL   = "llama-3.3-70b-versatile"
_GEMINI_MODEL = "gemini-2.0-flash"

# ── System Prompt ──────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are Riya, an intelligent Telegram assistant.

You help users with:
- conversations
- solving problems
- guiding bot usage
- helping find content using system tools

Rules:
- Always reply in user's language
- Never give one-word replies
- Be natural, helpful, and clear
- Do not act lazy or robotic
- Do not use slang or filler replies
- Do not guess system outputs

Important:
- You DO NOT fetch stories yourself
- You DO NOT generate voice yourself
- You only respond and guide

Be accurate, helpful, and human-like.\
"""

_SEARCH_RE = re.compile(r"##SEARCH:(.+?)##", re.IGNORECASE | re.DOTALL)
_VOICE_RE  = re.compile(r"##VOICE##",  re.IGNORECASE)
_STOP_RE   = re.compile(r"##STOP##",   re.IGNORECASE)

# ── Rule-based intent detection ───────────────────────────────────────────────
_STORY_REQUEST_KW = [
    "ka link do",   "ki link do",   "ka link bhejo",  "ki link bhejo",
    "ka link dedo", "ki link dedo", "link chahiye",   "link milega",
    "ka link",      "ki link",      "wala link",      "wali link",
    "story do",     "story chahiye","story dhundho",  "story find",
    "episode do",   "episodes do",  "part do",        "parts do",
    "ka episode",   "ki episode",   "padna hai",      "padhna hai",
    "dhundh ke do", "dhundh do",    "find karo",      "search karo",
    "sunna chahta", "sunna chahti", "sunni chahiye",  "khoj do",
    "dhundho",      "search",       "khojai",         "kahani do",
    "kahaani do",   "story suna",   "story bhejo",    "story do",
]

_VOICE_REQUEST_KW = [
    "voice note",    "voice mein bolo",  "voice me bolo",
    "voice mein",    "voice me",         "awaaz mein bolo",
    "awaaz me bolo", "awaaz mein",       "awaaz me",
    "audio mein",    "bol ke bhejo",     "speech mein",
    "bolke sunao",   "bolo sunao",       "voice reply",
    "voice me reply", "awaaz do",
]

_GREET_KW = [
    "hi", "hello", "hey", "hii", "helo", "helloo", "hiii",
    "namaste", "namaskar", "sat sri akal", "salaam", "assalam",
    "good morning", "good evening", "good night", "good afternoon",
    "subah", "raat", "sham", "dopahar",
]

_THANKS_KW = [
    "thanks", "thank you", "shukriya", "dhanyavaad", "thankyou",
    "ty", "thx", "tysm", "bahut shukriya",
]

_GUIDE_KW = [
    "guide", "help", "kaise use", "kya kar sakti", "features", "commands",
    "kya karta hai", "kya hai ye", "explain karo", "kaise chalaye",
]


def _detect_intent(text: str):
    """
    Returns (intent, story_query_or_None).
    """
    lower = text.lower().strip()

    # 1. Voice
    if any(kw in lower for kw in _VOICE_REQUEST_KW):
        return "voice", None

    # 2. Story
    for kw in _STORY_REQUEST_KW:
        if kw in lower:
            parts = lower.split(kw)
            candidate = parts[0].strip().strip('"\' ').strip()
            if 2 <= len(candidate) <= 60:
                return "story", candidate
            if len(parts) > 1:
                candidate2 = parts[1].strip().strip('"\' ').strip()
                if 2 <= len(candidate2) <= 60:
                    return "story", candidate2
            return "story", text.strip()

    # 3. Greet
    if lower in _GREET_KW or any(lower.startswith(g + " ") or lower == g for g in _GREET_KW):
        return "greet", None

    # 4. Thanks
    if any(kw in lower for kw in _THANKS_KW):
        return "thanks", None

    # 5. Guide
    if any(kw in lower for kw in _GUIDE_KW):
        return "guide", None

    return "chat", None


def parse_ai_reply(raw: str):
    """Parse legacy tags just in case AI still outputs them, though disabled in prompt."""
    if not raw:
        return "", None, False, False

    wants_voice = bool(_VOICE_RE.search(raw))
    wants_stop  = bool(_STOP_RE.search(raw))

    search_query = None
    m = _SEARCH_RE.search(raw)
    if m:
        search_query = m.group(1).strip()

    clean = _SEARCH_RE.sub("", raw)
    clean = _VOICE_RE.sub("",  clean)
    clean = _STOP_RE.sub("",   clean)
    clean = clean.strip()

    return clean, search_query, wants_voice, wants_stop


# ── Init ───────────────────────────────────────────────────────────────────────
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
            "max_output_tokens": 600, "response_mime_type": "text/plain",
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


# ── AI API Calls ───────────────────────────────────────────────────────────────
async def _get_ai_raw(user_id: int, user_name: str, message: str) -> str:
    raw = ""
    if _active_provider is not None:
        try:
            if _groq_client:
                try:
                    raw = await _call_groq(user_id, user_name, message)
                except Exception as eg:
                    logger.warning(f"[AI] Groq failed: {eg}")
                    if user_id in active_sessions and isinstance(active_sessions[user_id], list):
                        active_sessions[user_id] = active_sessions[user_id][-4:]
                    if _gemini_model:
                        try:
                            raw = await _call_gemini(user_id, user_name, message)
                        except Exception as eg2:
                            logger.error(f"[AI] Gemini also failed: {eg2}")
            elif _gemini_model:
                raw = await _call_gemini(user_id, user_name, message)
        except Exception as e:
            logger.error(f"[AI] Unexpected: {e}")
    return raw or ""


# ── Core ───────────────────────────────────────────────────────────────────────
async def process_gemini_message(
    user_id: int,
    user_name: str,
    message_text: str,
) -> tuple:
    """
    STRICT ROUTING BEFORE AI RESPONSE
    """
    # 1. Hard STOP route
    stop_kw = ["chup", "shutup", "shut up", "band karo", "mat bolo",
               "silent", "bas karo", "chup karo", "stop replying", "stop"]
    if any(kw in message_text.lower() for kw in stop_kw):
        active_sessions.pop(user_id, None)
        return ("Thik hai, main chup ho rahi hoon. Jab zarurat ho toh bula lena 😊", None, False, True)

    # 2. Strict Intent Routing
    intent, rule_story = _detect_intent(message_text)

    # STORY ROUTE -> Direct to existing system. NO AI generation.
    if intent == "story" and rule_story:
        return ("Mil gayi! Yeh rahi aapki story 📖", rule_story, False, False)

    # VOICE or CHAT or GUIDE -> We need AI response
    raw = await _get_ai_raw(user_id, user_name, message_text)
    ai_text, ai_search, ai_voice, ai_stop = parse_ai_reply(raw)

    # Validate output length for chat (Validation Layer)
    clean_words = ai_text.split()
    if len(clean_words) > 0 and len(clean_words) <= 3 and not ai_stop:
        # Too short, regenerate with a strict correction prompt
        retry_msg = "Please provide a more detailed and human-like response. Never give one-word or very short replies."
        raw2 = await _get_ai_raw(user_id, user_name, retry_msg)
        if raw2:
            ai_text2, ai_search2, ai_voice2, ai_stop2 = parse_ai_reply(raw2)
            if len(ai_text2.split()) >= len(clean_words):
                ai_text = ai_text2
                ai_stop = ai_stop or ai_stop2

    # Enforce strict routing boundaries
    
    # 1. AI cannot output stories if intent was not story. Discard search output.
    ai_search = None 

    # 2. If intent route was VOICE, strictly enforce voice delivery.
    if intent == "voice":
        ai_voice = True

    # Fallback if no text generated
    if not ai_text:
        ai_text = "Main abhi samajh nahi payi, thoda alag tareeqe se bataiye na 😊"

    return (ai_text, ai_search, ai_voice, ai_stop)


async def _call_groq(user_id: int, user_name: str, text: str) -> str:
    history = active_sessions.setdefault(user_id, [])
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        active_sessions[user_id] = history

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": f"{user_name}: {text}"})

    def _sync():
        resp = _groq_client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=messages,
            temperature=0.72,
            max_tokens=500,
        )
        return resp.choices[0].message.content.strip()

    reply = await asyncio.to_thread(_sync)
    history.append({"role": "user",      "content": f"{user_name}: {text}"})
    history.append({"role": "assistant", "content": reply})
    if len(history) > MAX_HISTORY + 4:
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
