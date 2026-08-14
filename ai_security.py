"""
ai_security.py — Riya Bot AI-Powered Security & Content Moderation
====================================================================
Uses FREE AI providers (Cerebras → Together → OpenRouter, same as ai_engine.py)
to detect and auto-moderate:
  1. Phishing / scam links
  2. Adult / porn content
  3. Spam / mass-advertising
  4. Abusive language

Actions on violation:
  - Delete message
  - Mute / ban user (configurable)
  - Notify admin in LOG_CHANNEL
  - Warn user via DM

Zero cost — uses the same free API keys already configured in .env
"""
import os
import re
import asyncio
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── Known-bad URL patterns (fast regex, no AI needed) ─────────────────────────
_PHISH_PATTERNS = re.compile(
    r"(bit\.ly|tinyurl|t\.me/\+|t\.me/joinchat|free.*prize|click.*win|"
    r"verify.*account|confirm.*wallet|crypto.*giveaway|bitcoin.*free|"
    r"airdrop|nude|xxx|porn|onlyfans|18\+.*free|adult.*content|"
    r"earn.*money.*day|investment.*profit|double.*bitcoin|"
    r"phish|hack|crack|keygen|torrent.*crack)",
    re.IGNORECASE
)

_URL_PATTERN = re.compile(
    r"https?://[^\s]+|www\.[^\s]+|t\.me/[^\s]+",
    re.IGNORECASE
)

# ── Security config (loaded from bot config_db) ────────────────────────────────
_DEFAULT_SECURITY_CONFIG = {
    "enabled": True,
    "auto_delete": True,       # Delete violating messages
    "auto_mute": True,         # Mute user on violation
    "auto_ban_threshold": 3,   # Ban after N violations
    "mute_duration_mins": 60,  # 0 = permanent mute
    "check_links": True,
    "check_porn": True,
    "check_scam": True,
    "check_spam": True,
    "whitelist_admins": True,  # Never moderate admins
    "ai_moderation": True,     # Use AI for borderline cases
}

# Per-user violation counter (in-memory, resets on restart)
_violation_counts: dict = {}   # user_id → int

# ── AI provider selection (same keys as ai_engine.py) ─────────────────────────
async def _call_ai_moderation(text: str) -> Tuple[bool, str, str]:
    """
    Ask AI to classify the text.
    Returns: (is_violation, category, reason)
    category: 'scam' | 'phishing' | 'porn' | 'spam' | 'abuse' | 'safe'
    """
    PROVIDERS_ORDER = [
        ("cerebras",   "https://api.cerebras.ai/v1",        os.getenv("CEREBRAS_API_KEY", ""),  "llama-3.3-70b"),
        ("together",   "https://api.together.xyz/v1",        os.getenv("TOGETHER_API_KEY", ""),  "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"),
        ("openrouter", "https://openrouter.ai/api/v1",       os.getenv("OPENROUTER_API_KEY", ""), "meta-llama/llama-3.1-8b-instruct:free"),
    ]

    moderation_prompt = """You are a Telegram group content moderator. Classify this message strictly and briefly.

Respond with EXACTLY this format (one line each):
VERDICT: safe OR violation
CATEGORY: safe OR scam OR phishing OR porn OR spam OR abuse
REASON: one short sentence

Rules:
- 'scam' = fake offers, fraud, money scams, crypto fraud
- 'phishing' = fake links, account verification tricks, login theft  
- 'porn' = adult/sexual/NSFW content, nude links, 18+ content
- 'spam' = mass advertising, unrelated promotion, invite flooding
- 'abuse' = hate speech, serious threats, harassment
- 'safe' = normal message, story request, question, casual chat

Be conservative. Only flag clear violations. Hinglish/Hindi casual chat is SAFE.

Message to classify:
"""

    for provider_id, base_url, api_key, model in PROVIDERS_ORDER:
        if not api_key:
            continue
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": moderation_prompt},
                        {"role": "user", "content": text[:1000]}  # Limit input
                    ],
                    max_tokens=80,
                    temperature=0.0,  # Deterministic for moderation
                ),
                timeout=8.0
            )
            result = resp.choices[0].message.content.strip()
            # Parse response
            verdict_match  = re.search(r"VERDICT:\s*(\w+)", result, re.IGNORECASE)
            category_match = re.search(r"CATEGORY:\s*(\w+)", result, re.IGNORECASE)
            reason_match   = re.search(r"REASON:\s*(.+)", result, re.IGNORECASE)

            verdict  = (verdict_match.group(1) or "safe").lower()
            category = (category_match.group(1) or "safe").lower()
            reason   = (reason_match.group(1) or "No reason given").strip() if reason_match else "No reason given"

            is_violation = verdict == "violation"
            logger.debug(f"[Security/{provider_id}] verdict={verdict} cat={category} reason={reason}")
            return is_violation, category, reason

        except asyncio.TimeoutError:
            logger.warning(f"[Security] {provider_id} timeout, trying next provider")
            continue
        except Exception as e:
            logger.warning(f"[Security] {provider_id} error: {e}, trying next")
            continue

    # If all AI providers fail → fallback to regex-only (non-blocking)
    return False, "safe", "AI unavailable — regex only"


def _fast_regex_check(text: str) -> Tuple[bool, str, str]:
    """Quick regex check before AI (saves API calls for obvious cases)."""
    # Check for phishing/scam/porn patterns in text or URLs
    urls = _URL_PATTERN.findall(text)
    full_check = text + " ".join(urls)

    if _PHISH_PATTERNS.search(full_check):
        # Identify which category triggered
        for pattern, cat in [
            (r"(nude|xxx|porn|onlyfans|18\+.*free|adult)", "porn"),
            (r"(phish|hack|crack|keygen)", "phishing"),
            (r"(bit\.ly|tinyurl|t\.me/\+|t\.me/joinchat)", "scam"),
            (r"(free.*prize|click.*win|verify.*account|crypto.*giveaway|bitcoin.*free|airdrop|earn.*money|investment.*profit|double.*bitcoin)", "scam"),
        ]:
            if re.search(pattern, full_check, re.IGNORECASE):
                return True, cat, f"Matched security pattern: {cat}"
    return False, "safe", ""


async def check_message_security(
    text: str,
    use_ai: bool = True
) -> Tuple[bool, str, str]:
    """
    Main security check function.
    Returns: (is_violation, category, reason)
    
    1. Fast regex check first (instant)
    2. AI check only if regex is clean and use_ai=True
    """
    if not text or not text.strip():
        return False, "safe", ""

    # Step 1: Fast regex
    is_bad, category, reason = _fast_regex_check(text)
    if is_bad:
        return True, category, reason

    # Step 2: AI check for borderline content (only if text has URLs or is long)
    has_url = bool(_URL_PATTERN.search(text))
    is_long = len(text) > 80
    if use_ai and (has_url or is_long):
        return await _call_ai_moderation(text)

    return False, "safe", ""


def get_security_config() -> dict:
    """Load security config from bot config_db."""
    try:
        from database import load_config
        cfg = load_config()
        return cfg.get("security", _DEFAULT_SECURITY_CONFIG)
    except Exception:
        return _DEFAULT_SECURITY_CONFIG.copy()


def save_security_config(sec_cfg: dict):
    """Save security config to bot config_db."""
    try:
        from database import load_config, save_config
        cfg = load_config()
        cfg["security"] = sec_cfg
        save_config(cfg)
    except Exception as e:
        logger.error(f"[Security] save_security_config: {e}")


def record_violation(user_id: int) -> int:
    """Increment violation count. Returns new count."""
    _violation_counts[user_id] = _violation_counts.get(user_id, 0) + 1
    return _violation_counts[user_id]


def reset_violations(user_id: int):
    """Reset violation count for a user (admin action)."""
    _violation_counts.pop(user_id, None)


def get_violations(user_id: int) -> int:
    return _violation_counts.get(user_id, 0)


# ── Category display names ─────────────────────────────────────────────────────
CATEGORY_LABELS = {
    "scam":     "🔴 Scam / Fraud",
    "phishing": "🎣 Phishing Link",
    "porn":     "🔞 Adult / NSFW Content",
    "spam":     "📢 Spam / Advertisement",
    "abuse":    "⚠️ Abusive Content",
    "safe":     "✅ Safe",
}

CATEGORY_LABELS_HI = {
    "scam":     "🔴 स्कैम / धोखाधड़ी",
    "phishing": "🎣 फ़िशिंग लिंक",
    "porn":     "🔞 अश्लील / NSFW सामग्री",
    "spam":     "📢 स्पैम / विज्ञापन",
    "abuse":    "⚠️ अपमानजनक सामग्री",
    "safe":     "✅ सुरक्षित",
}
