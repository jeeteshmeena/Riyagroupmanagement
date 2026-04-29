"""
plugins/group/nsfw.py
──────────────────────
NSFW content detection and enforcement.

Strict mode (default ON): delete → warn → mute → ban
Non-strict mode:           delete only

Keywords and link patterns are checked case-insensitively.
Admins are always skipped.
"""
import re
import logging
from telegram import Update, ChatPermissions
from telegram.ext import ContextTypes
from .utils import get_settings, is_enabled, add_warn, reset_warns
from .logs import send_log

logger = logging.getLogger(__name__)

# ── Detection lists ───────────────────────────────────────────────────────────
_NSFW_KEYWORDS = [
    # English
    "porn", "xxx", "nude", "naked", "sex ", "sexy", "boob", "boobs", "dick",
    "pussy", "vagina", "penis", "orgasm", "masturbat", "erotic", "onlyfans",
    "nsfw", "hentai", "loli", "nudes", "18+", "adult content", "bdsm",
    # Hindi/Hinglish
    "chudai", "lund", "chut", "gaand", "randi", "bhosdike", "madarchod",
    "bhosdi", "harami", "chutiya", "lauda", "gandu",
]

_NSFW_LINK_PATTERNS = [
    r"pornhub\.com", r"xnxx\.com", r"xvideos\.com", r"redtube\.com",
    r"xhamster\.com", r"youporn\.com", r"spankbang\.com",
    r"t\.me/\+",  # Private telegram invite links (common spam vector)
]


def _is_nsfw(text: str) -> bool:
    t = text.lower()
    for kw in _NSFW_KEYWORDS:
        if kw in t:
            return True
    for pat in _NSFW_LINK_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            return True
    return False


# ── Guard handler ─────────────────────────────────────────────────────────────
async def nsfw_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scan every group message for NSFW content."""
    try:
        if not update.message or not update.effective_chat:
            return
        if update.effective_chat.type not in ("group", "supergroup"):
            return

        chat_id = update.effective_chat.id
        if not is_enabled(chat_id):
            return

        # Skip admins and bots
        try:
            cm = await context.bot.get_chat_member(chat_id, update.effective_user.id)
            if cm.status in ("administrator", "creator"):
                return
        except Exception:
            return

        text = update.message.text or update.message.caption or ""
        if not text or not _is_nsfw(text):
            return

        # Always delete the offending message first
        try:
            await update.message.delete()
        except Exception:
            pass

        s = get_settings(chat_id)

        if not s.get("nsfw_strict", True):
            # Non-strict: just delete, no further action
            return

        user = update.effective_user
        limit = s.get("warn_limit", 3)
        count = add_warn(chat_id, user.id)

        await send_log(
            context, chat_id,
            f"🚫 NSFW from {user.first_name} ({user.id}). Warn {count}/{limit}",
        )

        if count >= limit:
            # Ban
            try:
                await context.bot.ban_chat_member(chat_id, user.id)
                reset_warns(chat_id, user.id)
                await context.bot.send_message(
                    chat_id,
                    f"🔨 <b>{user.first_name}</b> banned for repeated NSFW violations.",
                    parse_mode="HTML",
                )
                await send_log(context, chat_id,
                               f"🔨 NSFW auto-ban: {user.first_name} ({user.id})")
            except Exception as ex:
                logger.warning(f"[GCP-NSFW] ban failed: {ex}")

        elif count == limit - 1:
            # Mute (one warn before ban)
            try:
                await context.bot.restrict_chat_member(
                    chat_id, user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                )
                await context.bot.send_message(
                    chat_id,
                    f"🔇 <b>{user.first_name}</b> muted for NSFW content. "
                    f"({count}/{limit} warnings)",
                    parse_mode="HTML",
                )
            except Exception as ex:
                logger.warning(f"[GCP-NSFW] mute failed: {ex}")

        else:
            try:
                await context.bot.send_message(
                    chat_id,
                    f"⚠️ <b>{user.first_name}</b>, NSFW content is NOT allowed here. "
                    f"Warning {count}/{limit}.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"[GCP-NSFW] nsfw_guard: {e}")
