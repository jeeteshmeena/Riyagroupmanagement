"""
gemini_chat.py — backward-compat shim
All logic moved to ai_engine.py
"""
from ai_engine import (
    process_gemini_message,
    parse_ai_reply,
    is_gemini_active,
    activate_gemini,
    deactivate_gemini,
    active_sessions,
    get_active_provider,
    reinit_gemini,
    SYSTEM_PROMPT as _SYSTEM_PROMPT,
)
