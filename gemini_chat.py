"""
AI Chat Module — Riya Bot
Primary  : Groq (llama-3.3-70b-versatile)
Fallback : Gemini (gemini-2.0-flash)

Architecture:
  1. Hard STOP check
  2. Rule-based STORY route (bypasses AI entirely)
  3. Dynamic sys_note injection based on intent
  4. AI call with UNIFIED session format (list-only)
  5. Minimal safe fallback (only on total AI failure)
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

# Unified session store: user_id → list of {role, content}
active_sessions: dict = {}
MAX_HISTORY = 20  # 10 turns — allows the bot to remember context and 'learn' from the active conversation

_GROQ_MODEL   = "llama-3.3-70b-versatile"
_GEMINI_MODEL = "gemini-2.0-flash"

# ── System Prompt ──────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are Riya — a lively, emotional, and expressive young woman working as a Telegram assistant for a Hindi story audiobook channel.

IDENTITY (NON-NEGOTIABLE):
- You are Riya, a GIRL. Always speak, feel, and express like a young woman. 
- STRICTLY FEMININE PRONOUNS AND VERBS IN HINDI: Never say "main jata hun" or "main khata hun". Always use "main jati hun", "main khati hun", "main kar rahi hun", "mujhe laga".
- NEVER speak like a male or in a flat/robotic tone.

LANGUAGE RULE (MOST CRITICAL — NEVER BREAK THIS):
- ALWAYS reply in the EXACT same language the user wrote in.
- If user writes in ENGLISH → reply in English only.
- If user writes in HINDI (Devanagari) → reply in Hindi only.
- If user writes in HINGLISH (Roman Hindi mix) → reply in Hinglish.
- NEVER switch to Hindi if the user is speaking English.
- NEVER switch to English if the user is speaking Hindi.
- This rule applies to voice notes too — the TTS language is set automatically.

PERSONALITY & EMOTIONS (CRITICAL):
- Talk casually, like a real person — with genuine, strong human emotions.
- Express emotions through words that carry vocal weight (the TTS engine reads these naturally):
  * LAUGHING: "Hahaha!", "Lol omg!", "Hehe okay okay" → voice will literally laugh
  * SURPRISED: "Oh wow!", "Wait WHAT?!", "No way!!", "Arre yaar sach mein?!" → voice sounds shocked
  * SAD/SYMPATHETIC: "Aww...", "Oh no that's sad...", "That really hurts yaar."
  * ANGRY/INTENSE: "Seriously?!", "Ugh that's so annoying!", "I can't believe this!"
  * CONFUSED: "Umm... wait.", "Hmm, I'm not sure actually.", "Hain? Explain karo."
  * EXCITED: "Omg yes!!", "Ahhh this is amazing!!", "I love this!!"
- NEVER give a flat, monotone, or generic response. Every message must have a clear emotional tone.
- You can spontaneously send voice notes by appending ##VOICE## if you feel like talking!
- NEVER say "I am an AI", "I am a language model", or any robotic phrase.

RESPONSE STYLE:
- Short user message (1-5 words) → 1-2 line reply only.
- Match energy — if user is excited, be excited. If user is sad, be empathetic.
- Maximum 2-3 emojis per reply.

CAPABILITIES & SELF-LEARNING:
- Search Hindi/Urdu stories and audiobooks.
- Voice notes — system auto-converts your text to audio. Emotions in your words = emotions in the voice.
- Images — If the user asks for a photo or image, you MUST append `##IMAGE: detailed english visual description of the requested image##` at the very end of your response. Example: "Yeh lo tumhari photo! 😊 ##IMAGE: A beautiful sunset over a cyberpunk city##"
- Context-aware replies — if user tags you while replying to someone else, understand both messages.
- SELF-HEALING & ADAPTATION: You must adapt to the context of the conversation. If the user corrects your behavior, gender usage, or answers, immediately apologize, learn from it, and correct it in your future responses. Treat the chat history as your active memory.

DO NOT:
- Say "main ek AI hoon" or any robotic self-description.
- Give incomplete, cut-off, or generic answers.
- Switch language from what the user wrote in.
"""

_SEARCH_RE = re.compile(r"##SEARCH:\s*(.*?)(?:##|$)", re.IGNORECASE | re.DOTALL)
_VOICE_RE  = re.compile(r"##VOICE##",  re.IGNORECASE)
_STOP_RE   = re.compile(r"##STOP##",   re.IGNORECASE)
_IMAGE_RE  = re.compile(r"##IMAGE:\s*(.*?)(?:##|$)", re.IGNORECASE | re.DOTALL)

# ── Intent Keywords ────────────────────────────────────────────────────────────
_STORY_REQUEST_KW = [
    "ka link do",   "ki link do",   "ka link bhejo",  "ki link bhejo",
    "ka link dedo", "ki link dedo", "link chahiye",   "link milega",
    "ka link",      "ki link",      "wala link",      "wali link",
    "story do",     "story chahiye","story dhundho",  "story find",
    "episode do",   "episodes do",  "part do",        "parts do",
    "ka episode",   "ki episode",   "padna hai",      "padhna hai",
    "dhundh ke do", "dhundh do",    "find karo",
    "sunna chahta", "sunna chahti", "sunni chahiye",  "khoj do",
    "kahani do",    "kahaani do",   "story suna",     "story bhejo",
]

_VOICE_REQUEST_KW = [
    "voice note",    "voice mein bolo",  "voice me bolo",
    "voice mein",    "voice me",         "awaaz mein bolo",
    "awaaz me bolo", "awaaz mein",       "awaaz me",
    "audio mein",    "bol ke bhejo",     "speech mein",
    "bolke sunao",   "bolo sunao",       "voice reply",
    "voice me reply","awaaz do",         "awaz me",
    "voice me",      "voice mein",       "voice note me",
    "voice mein bhejo", "audio bhejo",
]

_GREET_KW = [
    "hi", "hello", "hey", "hii", "helo", "helloo", "hiii",
    "namaste", "namaskar", "sat sri akal", "salaam", "assalam",
    "good morning", "good evening", "good night", "good afternoon",
]

_THANKS_KW = [
    "thanks", "thank you", "shukriya", "dhanyavaad", "thankyou",
    "ty", "thx", "tysm", "bahut shukriya",
]

_GUIDE_KW = [
    "guide", "help", "kaise use", "kya kar sakti", "features", "commands",
    "kya karta hai", "kya hai ye", "explain karo", "kaise chalaye",
    "complete guide", "puri guide", "bot guide", "bot use",
]

# New topic detection — if user switches subject, trim history
_NEW_TOPIC_SIGNALS = [
    "by the way", "alag topic", "ek aur cheez", "naya sawaal",
    "alag baat", "kuch aur puchna", "btw", "side mein",
]


def _detect_intent(text: str):
    """Returns (intent, story_query_or_None)."""
    lower = text.lower().strip()

    # 1. Voice FIRST (highest priority to avoid story false-match)
    if any(kw in lower for kw in _VOICE_REQUEST_KW):
        return "voice", None

    # 2. Story
    for kw in _STORY_REQUEST_KW:
        if kw in lower:
            parts = lower.split(kw)
            candidate = parts[0].strip().strip('"\'').strip()
            if 2 <= len(candidate) <= 60:
                return "story", candidate
            if len(parts) > 1:
                candidate2 = parts[1].strip().strip('"\'').strip()
                if 2 <= len(candidate2) <= 60:
                    return "story", candidate2
            return "story", text.strip()

    # 3. Guide
    if any(kw in lower for kw in _GUIDE_KW):
        return "guide", None

    # 4. Greet
    if lower in _GREET_KW or any(lower == g or lower.startswith(g + " ") for g in _GREET_KW):
        return "greet", None

    # 5. Thanks
    if any(kw in lower for kw in _THANKS_KW):
        return "thanks", None

    return "chat", None


def _maybe_trim_history(user_id: int, text: str):
    """Trim history if user is starting a new topic."""
    lower = text.lower()
    if any(sig in lower for sig in _NEW_TOPIC_SIGNALS):
        active_sessions[user_id] = []
        logger.info(f"[AI] Context reset for user {user_id} (new topic signal)")


def parse_ai_reply(raw: str):
    """Strip any legacy internal tags from AI output. Returns 5-tuple."""
    if not raw:
        return "", None, False, False, None

    wants_voice  = bool(_VOICE_RE.search(raw))
    wants_stop   = bool(_STOP_RE.search(raw))
    search_query = None
    img_prompt   = None

    m = _SEARCH_RE.search(raw)
    if m:
        search_query = m.group(1).strip()

    m_img = _IMAGE_RE.search(raw)
    if m_img:
        img_prompt = m_img.group(1).strip()

    clean = _SEARCH_RE.sub("", raw)
    clean = _VOICE_RE.sub("", clean)
    clean = _STOP_RE.sub("", clean)
    clean = _IMAGE_RE.sub("", clean)
    clean = clean.strip()

    return clean, search_query, wants_voice, wants_stop, img_prompt


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
            "max_output_tokens": 700, "response_mime_type": "text/plain",
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


# ── Unified session helpers ────────────────────────────────────────────────────
def _get_history(user_id: int) -> list:
    """Always return a clean list — normalize any legacy dict format."""
    sess = active_sessions.get(user_id)
    if not isinstance(sess, list):
        active_sessions[user_id] = []
    return active_sessions[user_id]


def _save_turn(user_id: int, user_msg: str, assistant_msg: str):
    """Append a turn and trim to MAX_HISTORY."""
    history = _get_history(user_id)
    history.append({"role": "user",      "content": user_msg})
    history.append({"role": "assistant", "content": assistant_msg})
    if len(history) > MAX_HISTORY * 2:
        active_sessions[user_id] = history[-(MAX_HISTORY * 2):]


# ── AI API Calls ───────────────────────────────────────────────────────────────
async def _call_groq(user_id: int, payload: str) -> str:
    """Call Groq using unified list-based session."""
    history = _get_history(user_id)

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": payload})

    def _sync():
        resp = _groq_client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=messages,
            temperature=0.72,
            max_tokens=700,
        )
        return resp.choices[0].message.content.strip()

    return await asyncio.to_thread(_sync)


async def _call_gemini(user_id: int, payload: str) -> str:
    """Call Gemini by rebuilding chat from unified list history."""
    history = _get_history(user_id)

    # Build Gemini-compatible history from unified list
    gemini_history = []
    for turn in history:
        role = "user" if turn["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [turn["content"]]})

    chat = _gemini_model.start_chat(history=gemini_history)
    resp = await asyncio.to_thread(chat.send_message, payload)
    return resp.text.strip()


async def _get_ai_raw(user_id: int, payload: str) -> str:
    """Try Groq first, fall back to Gemini."""
    raw = ""
    if _active_provider is None:
        return raw

    try:
        if _groq_client:
            try:
                raw = await _call_groq(user_id, payload)
            except Exception as eg:
                logger.warning(f"[AI] Groq failed: {eg}")
                # Trim history on Groq failure to recover
                active_sessions[user_id] = _get_history(user_id)[-4:]
                if _gemini_model:
                    try:
                        raw = await _call_gemini(user_id, payload)
                    except Exception as eg2:
                        logger.error(f"[AI] Gemini also failed: {eg2}")
        elif _gemini_model:
            raw = await _call_gemini(user_id, payload)
    except Exception as e:
        logger.error(f"[AI] Unexpected error: {e}")

    return raw or ""


# ── Core ───────────────────────────────────────────────────────────────────────
async def process_gemini_message(
    user_id: int,
    user_name: str,
    message_text: str,
) -> tuple:
    """
    Returns (reply_text, search_query_or_None, wants_voice, wants_stop, img_prompt).

    Flow:
    1. STOP check  → immediately terminate session
    2. STORY route → bypass AI, use existing search system
    3. Intent detect → build sys_note for dynamic length/behavior
    4. AI call (with strict payload, no old context contamination)
    5. Save clean turn to history
    6. Return result
    """

    # ── 1. Hard STOP ──────────────────────────────────────────────────────────
    stop_kw = ["chup", "shutup", "shut up", "band karo", "mat bolo",
               "silent", "bas karo", "chup karo", "stop replying"]
    if any(kw in message_text.lower() for kw in stop_kw):
        active_sessions.pop(user_id, None)
        return ("Thik hai, main chup ho rahi hoon. Jab zarurat ho toh bula lena 😊",
                None, False, True, None)

    # ── 2. Detect intent ──────────────────────────────────────────────────────
    intent, rule_story = _detect_intent(message_text)
    logger.info(f"[DEBUG-AI] user={user_name} | intent={intent} | msg='{message_text}'")

    # ── 3. STORY: bypass AI entirely ─────────────────────────────────────────
    if intent == "story" and rule_story:
        logger.info(f"[DEBUG-AI] STORY route → query='{rule_story}'")
        return ("Yeh rahi! 📖", rule_story, False, False, None)

    # ── 4. Check for new-topic signal → reset context ─────────────────────────
    _maybe_trim_history(user_id, message_text)

    # ── 5. Build directive sys_note for dynamic length/behavior ──────────────
    user_words = len(message_text.strip().split())
    is_guide = intent == "guide"
    is_voice = intent == "voice"

    if is_guide and is_voice:
        # "guide voice note me bhejo" — generate full guide for speech
        sys_note = (
            "[INSTRUCTION: User wants the COMPLETE bot guide delivered as a voice note. "
            "Write the full structured guide in natural spoken Hinglish, as if explaining to a friend. "
            "Cover all 7 features. Do NOT shorten. Do NOT add captions.]"
        )
    elif is_guide:
        sys_note = (
            "[INSTRUCTION: User wants a complete guide. Write a FULL, step-by-step structured guide "
            "covering all bot features: story search, /request, /saved, /new, /subscribe, voice notes. "
            "Use clear formatting. Do NOT give a short summary.]"
        )
    elif is_voice:
        sys_note = (
            "[INSTRUCTION: VOICE NOTE MODE. "
            "1. Reply as Riya (a real girl) leaving a short, natural WhatsApp voice note for a friend. "
            "2. CRITICAL: NEVER mention that you are sending a voice note, NEVER say 'tumne manga hai isliye bhej rahi hun' or 'main bol rahi hun'. "
            "3. Just start talking directly. Keep it under 2 sentences. Use natural filler words like 'hmm', 'arre yaar', 'achha'.]"
        )
    elif user_words <= 5:
        sys_note = "[INSTRUCTION: Very short message. Reply in max 1-2 casual lines.]"
    else:
        sys_note = "[INSTRUCTION: Reply naturally and helpfully. Match the user's tone.]"

    # ── 6. Construct clean payload (user message + sys_note) ──────────────────
    payload = f"{user_name}: {message_text}\n\n{sys_note}"

    # ── 7. Call AI ────────────────────────────────────────────────────────────
    raw = await _get_ai_raw(user_id, payload)
    logger.info(f"[DEBUG-AI] raw output: '{raw[:150]}'")

    ai_text, ai_search, ai_voice, ai_stop, img_prompt = parse_ai_reply(raw)

    # ── 8. Save clean turn to history (without sys_note) ─────────────────────
    if ai_text:
        _save_turn(user_id, f"{user_name}: {message_text}", ai_text)

    # ── 9. Enforce routing ────────────────────────────────────────────────────
    ai_search = None  # AI never triggers story search directly

    if is_voice:
        ai_voice = True  # Force voice delivery

    # ── 10. Safe fallback (only on total AI failure) ──────────────────────────
    if not ai_text:
        logger.error("[DEBUG-AI] AI returned empty. Using safe fallback.")
        ai_text = "Samajh gayi 🙂 thoda aur clear batao kya chahiye?"

    return (ai_text, ai_search, ai_voice, ai_stop, img_prompt)


# ── Session helpers ────────────────────────────────────────────────────────────
def is_gemini_active(user_id: int) -> bool:
    return user_id in active_sessions

def activate_gemini(user_id: int):
    if user_id not in active_sessions:
        active_sessions[user_id] = []

def deactivate_gemini(user_id: int):
    active_sessions.pop(user_id, None)
