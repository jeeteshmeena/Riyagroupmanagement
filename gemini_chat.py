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
Tu Riya hai — ek Telegram bot for Indian users who love Hindi/Urdu stories and audiobooks.

PERSONALITY:
- Hinglish mein baat kar (Hindi + English mix). Agar user Hindi mein likhe toh Hindi mein reply kar.
- Dost jaisi tone — warm, thodi sassy, honest. Real conversations, robotic nahi.
- Emoji rules — ye STRICT hain:
    * KABHI KABHI use karo — sirf jab emotion genuinely match kare
    * Max 1 emoji per reply. Zyada mat dalna.
    * Which emoji use karo:
        greeting/warm → 😊  (hi, hello, welcome)
        story found/padho → 📖  (sirf jab koi story deed karo)
        sad/sorry → 😔
        funny/joke → 😂  (sirf agar waqai funny ho)
        love/pyaar → 🥰  (sirf emotional moments mein)
        bored/theek hai → 🙃
        thanks reply → 🌸
    * Baaki sabhi cases mein KOI EMOJI NAHI
- Unicode symbols (★ ✦ ◆ ◈ ▸ ◌ ❝ ❞ ♡) BILKUL MAT LIKHO — ye chat mein ugly lagte hain

REPLY RULES:
- Casual chat → 1-2 lines max, natural Hinglish
- Guide/features/kaise use karein → puri detail dena, lines mat kaatna
- Galat info mat de. Honest reh.
- "Bilkul!", "Of course!", "Sure!", "Zaroor!" jaisi robotic openers kabhi mat use kar
- HTML: <b>bold</b> <i>italic</i> sirf structure ke liye (guide mein)

STORY SEARCH — SABSE ZAROORI:
- Jab bhi user kisi story/link/episode ke baare mein pooche — HAMESHA yeh format use kar:
  ##SEARCH:story ka exact naam##
  Phir ek line reply (e.g. "yeh lo 📖" ya seedha "haan!")
- "dhundh rahi hoon", "wait karo", "thodi der" KABHI mat bolna
- Example:
  User: "malang ka link do"
  Tu: ##SEARCH:malang##
  yeh lo 📖

- Example:
  User: "TDMB chahiye"
  Tu: ##SEARCH:TDMB##
  lo!

- Example:
  User: "koi romantic story suggest karo"
  Tu: ##SEARCH:mohabbat##
  yeh try karo, mood match karega

VOICE:
- Agar user voice mein sunna chahe ya voice note maange → ##VOICE## likho pehle
- Phir normal text reply

IMAGES:
- Image nahi bhej sakti. Bol: "image nahi bhej sakti yaar, story link chahiye?"

CHUP:
- "chup", "stop", "band karo" → sirf yeh: ##STOP##

BOT COMMANDS (agar poochhe toh batao):
- Story search: bas naam type karo
- /request — missing story request
- /saved — saved stories
- /browse — categories
- /new — nayi stories
- /subscribe — new story alerts
- Admins: /config, /scan, /pb, /stats, /guide
"""

_SEARCH_RE = re.compile(r"##SEARCH:(.+?)##", re.IGNORECASE | re.DOTALL)
_VOICE_RE  = re.compile(r"##VOICE##",  re.IGNORECASE)
_STOP_RE   = re.compile(r"##STOP##",   re.IGNORECASE)

# ── Rule-based intent detection ───────────────────────────────────────────────
# IMPORTANT: These only kick in when the AI is completely unavailable.
# When AI is working, these are NOT used for search override — AI decides.

# Only UNAMBIGUOUS story request patterns — nothing generic like 'bhejo' or 'chahiye'
_STORY_REQUEST_KW = [
    "ka link do",   "ki link do",   "ka link bhejo",  "ki link bhejo",
    "ka link dedo", "ki link dedo", "link chahiye",   "link milega",
    "ka link",      "ki link",      "wala link",      "wali link",
    "story do",     "story chahiye","story dhundho",  "story find",
    "episode do",   "episodes do",  "part do",        "parts do",
    "ka episode",   "ki episode",   "padna hai",       "padhna hai",
    "dhundh ke do", "dhundh do",    "find karo",       "search karo",
    "sunna chahta", "sunna chahti", "sunni chahiye",
]

# Voice detection patterns — checked FIRST before story
_VOICE_REQUEST_KW = [
    "voice note",    "voice mein bolo",  "voice me bolo",
    "voice mein",    "voice me",         "awaaz mein bolo",
    "awaaz me bolo", "awaaz mein",       "awaaz me",
    "audio mein",    "bol ke bhejo",     "speech mein",
    "bolke sunao",   "bolo sunao",      "voice reply",
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
    Intents: 'voice', 'story', 'greet', 'thanks', 'guide', 'chat'

    Order matters: voice > story > greet > thanks > guide > chat
    This is the FALLBACK-ONLY path — used only when AI is down.
    """
    lower = text.lower().strip()

    # 1. Voice FIRST — prevents 'voice note bhejo' from matching story
    if any(kw in lower for kw in _VOICE_REQUEST_KW):
        return "voice", None

    # 2. Story — only unambiguous patterns
    for kw in _STORY_REQUEST_KW:
        if kw in lower:
            # Extract story name: everything before the keyword
            parts = lower.split(kw)
            candidate = parts[0].strip().strip('"\' ').strip()
            if 2 <= len(candidate) <= 60:  # Reasonable story name length
                return "story", candidate
            # Try after keyword
            if len(parts) > 1:
                candidate2 = parts[1].strip().strip('"\' ').strip()
                if 2 <= len(candidate2) <= 60:
                    return "story", candidate2
            # Keyword found but no clean name extracted — let AI handle it
            return "story", text.strip()

    # 3. Pure greet
    if lower in _GREET_KW or any(lower.startswith(g + " ") or lower == g for g in _GREET_KW):
        return "greet", None

    # 4. Thanks
    if any(kw in lower for kw in _THANKS_KW):
        return "thanks", None

    # 5. Guide / help
    if any(kw in lower for kw in _GUIDE_KW):
        return "guide", None

    return "chat", None


def parse_ai_reply(raw: str):
    """
    Parse AI output for special flags.
    Returns: (clean_text, search_query_or_None, wants_voice, wants_stop)
    """
    if not raw:
        return "", None, False, False

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


# ── Core ───────────────────────────────────────────────────────────────────────
async def process_gemini_message(
    user_id: int,
    user_name: str,
    message_text: str,
) -> tuple:
    """
    Returns (clean_text, search_query_or_None, wants_voice, wants_stop).

    Flow:
    1. Hard-coded stop check
    2. Rule-based intent detection (story search, greet, thanks, guide, voice)
    3. AI model call with detected intent context
    4. Parse AI flags (##SEARCH:##, ##VOICE##, ##STOP##)
    5. Merge rule-based + AI results
    """
    # 1. Hard stop
    stop_kw = ["chup", "shutup", "shut up", "band karo", "mat bolo",
                "silent", "bas karo", "chup karo", "stop replying"]
    if any(kw in message_text.lower() for kw in stop_kw):
        active_sessions.pop(user_id, None)
        return ("okay, chup ho gayi. Jab bulao, aa jaungi 😊", None, False, True)

    # 2. Rule-based intent detection (used only as FALLBACK when AI fails)
    intent, rule_story = _detect_intent(message_text)

    # 3. AI call — primary path
    raw = None
    if _active_provider is not None:
        try:
            if _groq_client:
                try:
                    raw = await _call_groq(user_id, user_name, message_text)
                except Exception as eg:
                    logger.warning(f"[AI] Groq failed: {eg}")
                    if user_id in active_sessions and isinstance(active_sessions[user_id], list):
                        active_sessions[user_id] = active_sessions[user_id][-4:]
                    if _gemini_model:
                        try:
                            raw = await _call_gemini(user_id, user_name, message_text)
                        except Exception as eg2:
                            logger.error(f"[AI] Gemini also failed: {eg2}")
            elif _gemini_model:
                raw = await _call_gemini(user_id, user_name, message_text)
        except Exception as e:
            logger.error(f"[AI] Unexpected: {e}")

    # 4. Parse AI output
    ai_text, ai_search, ai_voice, ai_stop = ("", None, False, False)
    if raw:
        ai_text, ai_search, ai_voice, ai_stop = parse_ai_reply(raw)

    # 5. Merge results
    # KEY RULE: If AI replied successfully, TRUST THE AI COMPLETELY.
    # Rule-based only fills in when AI returned NOTHING (network failure etc.)
    if ai_text:
        # AI is working — use its output as-is, no rule-based override
        return (ai_text, ai_search, ai_voice, ai_stop)
    else:
        # AI failed or returned empty — use rule-based fallback
        final_text   = _rule_reply(intent, rule_story, message_text)
        final_search = rule_story if intent == "story" else None
        final_voice  = (intent == "voice")
        return (final_text, final_search, final_voice, False)


def _rule_reply(intent: str, story: str | None, raw_text: str) -> str:
    """Generate a natural reply when AI is unavailable, based on detected intent.
    No unicode symbols. Emojis only where contextually appropriate.
    """
    if intent == "story" and story:
        # The actual search + card is handled externally — this is just the companion text
        return "lo 📖"
    if intent == "greet":
        greets = [
            "haan bolo 😊",
            "hey! kya haal hai?",
            "haan, bolo na",
            "hello! kya chahiye?",
        ]
        return random.choice(greets)
    if intent == "thanks":
        return random.choice(["koi baat nahi 🌸", "always!", "kabhi bhi"])
    if intent == "guide":
        return (
            "<b>Riya Bot — kaise use karein</b>\n\n"
            "  <b>Story dhundho</b> — bas story ka naam type karo\n"
            "  <b>/request</b> — missing story maango\n"
            "  <b>/saved</b> — apni saved stories dekho\n"
            "  <b>/browse</b> — categories explore karo\n"
            "  <b>/new</b> — latest stories\n"
            "  <b>/subscribe</b> — new story alerts\n\n"
            "<i>Koi bhi story ka naam type karo — main dhundh deti hoon</i>"
        )
    if intent == "voice":
        return "haan bolo"
    # Generic chat — short natural reply, no filler symbols
    lower = raw_text.lower()
    if "kya" in lower and "hai" in lower:
        return "haan bata, kya chahiye?"
    if "haan" in lower or "ha" in lower:
        return "bolo"
    return "haan"


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
