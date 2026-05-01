"""
groq_helper.py — AI text utility (Groq removed, now uses ai_engine)
"""
import asyncio

async def clean_description(text: str) -> str:
    """Clean story description using active AI provider."""
    try:
        from ai_engine import _get_ai_raw, _ai_config
        prompt = (
            f"Clean the following story description. Remove links, ads, symbols, noise. "
            f"Do NOT rewrite or summarize. Keep original meaning.\n\n{text}"
        )
        result = await _get_ai_raw(0, prompt)
        return result.strip() if result and result.strip() else text
    except Exception:
        return text

async def shorten_description(text: str) -> str:
    """Shorten story description using active AI provider."""
    try:
        from ai_engine import _get_ai_raw
        prompt = (
            f"Shorten this story description to 2-3 lines. Keep meaning intact. "
            f"Do not add extra info.\n\n{text}"
        )
        result = await _get_ai_raw(0, prompt)
        return result.strip() if result and result.strip() else text
    except Exception:
        return text
