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
    # Avoid false positive: "chudail" contains the NSFW substring "chudai"
    t = re.sub(r'chudail\w*', 'ghost', t)
    
    for kw in _NSFW_KEYWORDS:
        if kw in t:
            return True
    for pat in _NSFW_LINK_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            return True
    return False


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

        s = get_settings(chat_id)
        if not s.get("nsfw", True):
            return

        user = update.effective_user
        msg  = update.message
        if not user or not msg:
            return
        if user.id in (777000, 1087742242, context.bot.id) or user.is_bot:
            return
        if msg.is_automatic_forward or getattr(msg, "sender_chat", None):
            return

        # Skip admins and creators
        try:
            cm = await context.bot.get_chat_member(chat_id, user.id)
            if cm.status in ("administrator", "creator"):
                return
        except Exception:
            return

        text = update.message.text or update.message.caption or ""
        
        # ── 1. Fast static check (for text keywords/links) ─────────────────────
        is_violation = False
        reason = "NSFW / Inappropriate Content"
        
        if text and _is_nsfw(text):
            is_violation = True
            
        # ── 2. AI Multimodal check (for media or suspicious text) ─────────────
        if not is_violation:
            photo_bytes = None
            
            # Extract photo
            if update.message.photo:
                try:
                    tg_file = await update.message.photo[-1].get_file()
                    photo_bytes = bytes(await tg_file.download_as_bytearray())
                except Exception as e:
                    logger.warning(f"[GCP-NSFW] Photo download failed: {e}")
            
            # Extract static sticker
            elif update.message.sticker and not update.message.sticker.is_animated:
                try:
                    tg_file = await update.message.sticker.get_file()
                    photo_bytes = bytes(await tg_file.download_as_bytearray())
                except Exception as e:
                    logger.warning(f"[GCP-NSFW] Sticker download failed: {e}")
            
            # Extract GIF / animation thumbnail
            elif update.message.animation and update.message.animation.thumbnail:
                try:
                    tg_file = await update.message.animation.thumbnail.get_file()
                    photo_bytes = bytes(await tg_file.download_as_bytearray())
                except Exception as e:
                    logger.warning(f"[GCP-NSFW] Animation thumbnail download failed: {e}")

            if photo_bytes:
                from ai_engine import classify_content_with_gemini
                ai_result = await classify_content_with_gemini(text=text, photo_bytes=photo_bytes)
                if ai_result == "NSFW":
                    is_violation = True
                    reason = "AI Guard: NSFW/Explicit content detected"
                elif ai_result == "SPAM":
                    is_violation = True
                    reason = "AI Guard: Spam/Advertisement detected"

        if not is_violation:
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
            f"🚫 {reason} from {user.first_name} ({user.id}). Warn {count}/{limit}",
        )

        if count >= limit:
            # Ban
            try:
                await context.bot.ban_chat_member(chat_id, user.id)
                reset_warns(chat_id, user.id)
                await context.bot.send_message(
                    chat_id,
                    f"🔨 <b>{user.first_name}</b> banned for repeated violations ({reason}).",
                    parse_mode="HTML",
                )
                await send_log(context, chat_id,
                               f"🔨 Auto-ban: {user.first_name} ({user.id}) due to repeated violations")
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
                    f"🔇 <b>{user.first_name}</b> muted for {reason}. "
                    f"({count}/{limit} warnings)",
                    parse_mode="HTML",
                )
            except Exception as ex:
                logger.warning(f"[GCP-NSFW] mute failed: {ex}")

        else:
            try:
                await context.bot.send_message(
                    chat_id,
                    f"⚠️ <b>{user.first_name}</b>, {reason} is NOT allowed here. "
                    f"Warning {count}/{limit}.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"[GCP-NSFW] nsfw_guard: {e}")
