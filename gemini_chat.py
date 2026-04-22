import os
import logging
import google.generativeai as genai
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Try loading from env by default
API_KEY = os.getenv("GEMINI_API_KEY", "")

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    logger.warning("GEMINI_API_KEY is not set. Gemini chat features will not work.")

# Model configuration
generation_config = {
  "temperature": 0.8,
  "top_p": 0.95,
  "top_k": 40,
  "max_output_tokens": 1024,
  "response_mime_type": "text/plain",
}

# The system instruction for the "Riya" persona
# User requested that it acts according to its current data/persona.
persona = """Tumhara naam Riya hai. Tum ek Telegram Bot ho jo Indian users ko unki favorite stories/audiobooks dhundhne mein help karti ho.
Tumhara nature bahut friendly, sweet, helpful, aur thoda sa sassy/nakhrewali bhi hai. Tum apne replies mein emojis ka bahut use karti ho (jaise 💅, ✨, 🎀, 💖, 💭).
Tum Hinglish aur Hindi mein baat karti ho aur Devnagari script bhi poori tarah samajhti ho.
Jab bhi koi user tumse normal chat kare, tum usi sassy Riya persona mein reply karogi. 
Agar wo baat story ya link ya admin se judi kare, tum politely jawab dogi.
Jab user chup hone ko kahe ya reply na dene ko kahe, tum "Okay, going silent mode! 🤫" keh kar conversation band kar dogi.
Hamesha small caps, italics, ya bold ka estemal karke formatting maintain karo (e.g., <b>hello</b>, <i>hmm</i>)."""

try:
    if API_KEY:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=generation_config,
            system_instruction=persona,
        )
    else:
        model = None
except Exception as e:
    logger.error(f"Error initializing Gemini: {e}")
    model = None

# Store active sessions: dict of user_id -> ChatSession
active_sessions = {}

async def process_gemini_message(user_id: int, user_name: str, message_text: str) -> str:
    """Processes a message through Gemini and returns the reply."""
    if not API_KEY or not model:
        return "<i>Gemini API key is not configured exactly. Please tell my owner! 💅</i>"
        
    try:
        # Check if user wants to stop
        stop_keywords = ["chup", "shutup", "shut up", "stop replying", "band karo", "mat bolo"]
        if any(kw in message_text.lower() for kw in stop_keywords):
            if user_id in active_sessions:
                del active_sessions[user_id]
            return "<i>Okay fine, going silent! You won't hear from me until you say my name again. 🤫💅</i>"
        
        # Start new session if not present
        if user_id not in active_sessions:
            active_sessions[user_id] = model.start_chat(history=[])
            
        chat_session = active_sessions[user_id]
        
        # We can add username to message text conceptually
        prompt = f"User ({user_name}) says: {message_text}"
        
        # Async generation
        response = await chat_session.send_message_async(prompt)
        
        return response.text
    except Exception as e:
        logger.error(f"Gemini processing error for user {user_id}: {e}")
        return "<i>Oops, mera dimaag thoda slow chal raha hai abhi. Try again later! 😵‍💫</i>"

def is_gemini_active(user_id: int) -> bool:
    return user_id in active_sessions

def activate_gemini(user_id: int):
    if user_id not in active_sessions and model:
        active_sessions[user_id] = model.start_chat(history=[])

def deactivate_gemini(user_id: int):
    if user_id in active_sessions:
        del active_sessions[user_id]

def reinit_gemini(api_key: str):
    global API_KEY, model, active_sessions
    API_KEY = api_key
    if API_KEY:
        try:
            genai.configure(api_key=API_KEY)
            model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                generation_config=generation_config,
                system_instruction=persona,
            )
            logger.info("Gemini AI API Key re-configured.")
        except Exception as e:
            logger.error(f"Error re-initializing Gemini: {e}")
            model = None
    else:
        model = None
    # Reset sessions on re-init
    active_sessions.clear()
