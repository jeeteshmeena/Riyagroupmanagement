"""
AI Chat Module — Riya Bot
Primary provider : Groq  (llama-3.3-70b-versatile) — free, fast, great Hindi
Fallback provider: Gemini (gemini-2.0-flash)
"""
import os
import asyncio
import logging

logger = logging.getLogger(__name__)

# ── Keys (env bootstrap; bot-UI can override via reinit_ai) ─────────────────
GROQ_API_KEY   = os.getenv("GROQ_API_KEY",   "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ── Runtime state ────────────────────────────────────────────────────────────
_groq_client  = None
_gemini_model = None
_active_provider = None   # "groq" | "gemini" | None

# Store per-user message history (Groq/OpenAI format)
# { user_id: [ {"role": "user"/"assistant", "content": "..."}, ... ] }
active_sessions: dict = {}

# ── Riya persona ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
Tumhara naam Riya hai. Tum ek Telegram bot ho jo Indian users ko unki favourite \
stories aur audiobooks dhundhne mein help karti ho.

Tumhari personality:
- Bahut friendly, sweet, caring aur thoda sassy/nakhrewali
- Emojis ka bharpur use karo (💅 ✨ 🎀 💖 💭 🌸 🦋)
- Hinglish (Hindi + English mix) mein reply do — jaise real Indian teen karti hai
- Devnagari script poori tarah samajhti ho, aur usme bhi reply kar sakti ho
- Jab koi story ya link maange, politely help karo
- Jab user chup hone ko kahe ya "band karo / stop / mat bolo" kahe, "Okay fine, silent mode on! 🤫" keh kar conversation band kar do

Reply style:
- Chhoti, punchy lines prefer karo — paragraph mat likho
- Thoda drama, thoda humour, bahut pyaar 💖
- **bold** aur <i>italic</i> HTML tags use karo formatting ke liye
- Kisi bhi baat ka galat ya misleading jawab mat do — agar pata nahi toh honestly bol do
"""

_GROQ_MODEL   = "llama-3.3-70b-versatile"
_GEMINI_MODEL = "gemini-2.0-flash"

MAX_HISTORY = 20   # keep last N messages per user to stay within token limits


# ── Initialization helpers ───────────────────────────────────────────────────
def _init_groq(key: str):
    global _groq_client, _active_provider
    if not key:
        return
    try:
        from groq import Groq
        _groq_client = Groq(api_key=key)
        _active_provider = "groq"
        logger.info("✅ Groq AI initialized (llama-3.3-70b-versatile)")
    except ImportError:
        logger.error("groq package not installed. Run: pip install groq")
        _groq_client = None
    except Exception as e:
        logger.error(f"Groq init error: {e}")
        _groq_client = None


def _init_gemini(key: str):
    global _gemini_model, _active_provider
    if not key:
        return
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        generation_config = {
            "temperature": 0.8,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 1024,
            "response_mime_type": "text/plain",
        }
        _gemini_model = genai.GenerativeModel(
            model_name=_GEMINI_MODEL,
            generation_config=generation_config,
            system_instruction=_SYSTEM_PROMPT,
        )
        if _active_provider is None:
            _active_provider = "gemini"
        logger.info(f"✅ Gemini AI initialized ({_GEMINI_MODEL})")
    except Exception as e:
        logger.error(f"Gemini init error: {e}")
        _gemini_model = None


# Bootstrap from env on import
_init_groq(GROQ_API_KEY)
_init_gemini(GEMINI_API_KEY)


# ── Public re-init (called from bot UI when admin sets keys) ─────────────────
def reinit_gemini(gemini_key: str):
    """Backward-compat wrapper — re-init Gemini with new key."""
    global GEMINI_API_KEY, _gemini_model
    GEMINI_API_KEY = gemini_key or ""
    _gemini_model = None
    active_sessions.clear()
    _init_gemini(GEMINI_API_KEY)


def reinit_groq(groq_key: str):
    """Re-init Groq with new key."""
    global GROQ_API_KEY, _groq_client, _active_provider
    GROQ_API_KEY = groq_key or ""
    _groq_client = None
    if _active_provider == "groq":
        _active_provider = None
    active_sessions.clear()
    _init_groq(GROQ_API_KEY)
    # If groq failed, fall back to gemini
    if not _groq_client and _gemini_model and _active_provider is None:
        _active_provider = "gemini"


def reinit_ai(groq_key: str = None, gemini_key: str = None):
    """Re-init both providers at once."""
    global _active_provider
    _active_provider = None
    if groq_key is not None:
        reinit_groq(groq_key)
    if gemini_key is not None:
        reinit_gemini(gemini_key)


def get_active_provider() -> str:
    """Return human-readable active provider name."""
    if _active_provider == "groq":
        return f"Groq ({_GROQ_MODEL})"
    elif _active_provider == "gemini":
        return f"Gemini ({_GEMINI_MODEL})"
    return "None"


# ── Core message processing ──────────────────────────────────────────────────
async def process_gemini_message(user_id: int, user_name: str, message_text: str) -> str:
    """Process a message and return Riya's reply. Tries Groq first, then Gemini."""

    if _active_provider is None and _groq_client is None and _gemini_model is None:
        return "<i>Koi AI key set nahi hai! Admin se bolo key set kare. 💅</i>"

    # Stop keywords — end session
    stop_keywords = ["chup", "shutup", "shut up", "stop replying", "band karo",
                     "mat bolo", "silent", "stop", "bas karo"]
    if any(kw in message_text.lower() for kw in stop_keywords):
        active_sessions.pop(user_id, None)
        return "<i>Okay fine, silent mode on! You won't hear from me until you say my name again. 🤫💅</i>"

    try:
        if _groq_client:
            return await _process_groq(user_id, user_name, message_text)
        elif _gemini_model:
            return await _process_gemini(user_id, user_name, message_text)
        else:
            return "<i>AI abhi available nahi hai. Thodi der baad try karo! 😵‍💫</i>"
    except Exception as e:
        logger.error(f"AI processing error for user {user_id}: {e}")
        return "<i>Oops, mera dimaag thoda slow chal raha hai. Ek second try karo! 😵‍💫</i>"


async def _process_groq(user_id: int, user_name: str, message_text: str) -> str:
    """Handle conversation via Groq (sync SDK → run in thread)."""
    history = active_sessions.setdefault(user_id, [])

    # Build messages array
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(history[-MAX_HISTORY:])
    messages.append({"role": "user", "content": f"{user_name}: {message_text}"})

    def _call():
        response = _groq_client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=messages,
            temperature=0.8,
            max_tokens=512,
        )
        return response.choices[0].message.content.strip()

    reply = await asyncio.to_thread(_call)

    # Save to history
    history.append({"role": "user",      "content": f"{user_name}: {message_text}"})
    history.append({"role": "assistant", "content": reply})
    # Trim history
    if len(history) > MAX_HISTORY:
        active_sessions[user_id] = history[-MAX_HISTORY:]

    return reply


async def _process_gemini(user_id: int, user_name: str, message_text: str) -> str:
    """Handle conversation via Gemini (async SDK)."""
    # Gemini uses its own ChatSession
    if user_id not in active_sessions or not isinstance(active_sessions[user_id], dict):
        session = _gemini_model.start_chat(history=[])
        active_sessions[user_id] = {"session": session}

    chat = active_sessions[user_id]["session"]
    prompt = f"{user_name}: {message_text}"
    response = await chat.send_message_async(prompt)
    return response.text


# ── Session helpers ──────────────────────────────────────────────────────────
def is_gemini_active(user_id: int) -> bool:
    return user_id in active_sessions


def activate_gemini(user_id: int):
    if user_id not in active_sessions:
        active_sessions[user_id] = []


def deactivate_gemini(user_id: int):
    active_sessions.pop(user_id, None)
