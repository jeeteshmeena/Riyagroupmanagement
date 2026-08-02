"""
plugins/group/spam.py
──────────────────────
Strict group content filter:
  • NSFW keywords in text/caption  → delete + permanent mute
  • Any URL not from sliceurl.app  → delete + permanent mute
"""
import re
import logging
from telegram import Update, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

ALLOWED_DOMAIN = "sliceurl.app"

# ── NSFW / Explicit keywords ──────────────────────────────────────────────────
_NSFW_KEYWORDS = [
    "porn","porno","xxx","nude","nudes","naked","sex ","sexy","boob","boobs",
    "tit","tits","dick","cock","pussy","vagina","penis","orgasm","masturbat",
    "erotic","onlyfans","nsfw","hentai","loli","18+","adult content","bdsm",
    "blowjob","cumshot","creampie","hardcore","slutty","slut","whore",
    "asshole","fuck","fucking","fucked","milf","threesome","gangbang",
    "dildo","vibrator","muschi","spritz","nacktes","loch","filmt","spanner",
    "chudai","lund","chut","gaand","randi","madarchod","bhosdi","harami","lauda",
]

# ── URL regex — covers http/https/www/bare domains ───────────────────────────
_URL_RE = re.compile(
    r"(?:https?://|ftp://|www\.)"   # must have a scheme or www
    r"[^\s<>\"']+",                  # rest of URL
    re.IGNORECASE,
)

def _extract_all_urls(text: str) -> list:
    """Extract ALL URLs from raw text — with or without scheme."""
    if not text:
        return []
    # Single pattern, no capturing groups → always returns list of strings
    return re.findall(
        r"(?:https?://|ftp://|www\.)\S+"   # URLs with scheme or www
        r"|"
        r"\b[a-z0-9][a-z0-9\-]*\.[a-z]{2,}(?:/\S*)?",  # bare domain.tld
        text,
        re.IGNORECASE,
    )


def _is_nsfw(text: str) -> bool:
    t = text.lower()
    # Replace witch variants ("chudail", "chudali", etc.) to prevent false positive match on "chudai"
    t = re.sub(r'\bchudai[li]\w*', 'witch', t)
    for kw in _NSFW_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", t):
            return True
    return False


async def _take_action(context, chat_id: int, user, msg, reason: str, action_taken: str = "Message Deleted • User Muted"):
    """Delete message, permanently mute user, send alert."""
    # Never act on system accounts (777000 / channel posts / bots)
    if user.id in (777000, 1087742242, context.bot.id) or user.is_bot:
        return

    # 1. Delete
    try:
        await msg.delete()
    except Exception as e:
        logger.warning(f"[StrictSpam] Delete failed: {e}")

    # 2. Permanent mute
    try:
        await context.bot.restrict_chat_member(
            chat_id, user.id,
            permissions=ChatPermissions(can_send_messages=False),
        )
    except Exception as e:
        logger.warning(f"[StrictSpam] Mute failed: {e}")

    # 3. Alert
    try:
        import html
        mention = f"<a href='tg://user?id={user.id}'>{html.escape(user.first_name)}</a>"
        alert_text = (
            "<b>❈ 𝗦𝗲𝗰𝘂𝗿𝗶𝘁𝘆 𝗔𝗹𝗲𝗿𝘁</b>\n\n"
            f"<b>𝗨𝘀𝗲𝗿</b>                ➠ {mention}\n"
            f"<b>𝗧𝗲𝗹𝗲𝗴𝗿𝗮𝗺 𝗜𝗗</b>       ➠ <code>{user.id}</code>\n"
            f"<b>𝗩𝗶𝗼𝗹𝗮𝘁𝗶𝗼𝗻 𝗥𝗲𝗮𝘀𝗼𝗻</b> ➠ {reason}\n\n"
            f"<b>𝗔𝗰𝘁𝗶𝗼𝗻</b>             ➠ {action_taken}\n\n"
            "If you believe this action was taken in error,\n"
            "please contact an <b>administrator</b> using the button below."
        )

        admin_url = "https://t.me/ItsNewtonPlanet"
        try:
            from database import load_config
            cfg = load_config()
            admin_url = cfg.get("admin_contact", "https://t.me/ItsNewtonPlanet")
        except Exception:
            pass

        await context.bot.send_message(
            chat_id,
            text=alert_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("「 𝗖𝗼𝗻𝘁𝗮𝗰𝘁 𝗔𝗱𝗺𝗶𝗻 」", url=admin_url)
            ]])
        )
    except Exception as e:
        logger.warning(f"[StrictSpam] Alert failed: {e}")


async def strict_spam_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main guard: runs on every group message."""
    try:
        msg  = update.message
        chat = update.effective_chat
        user = update.effective_user

        if not msg or not chat or not user:
            return
        if chat.type not in ("group", "supergroup"):
            return

        # Skip Telegram System Accounts, Channel Linked Posts (777000), Bots, Admins & Creator
        if user.id in (777000, 1087742242, context.bot.id) or user.is_bot:
            return

        if msg.is_automatic_forward or getattr(msg, "sender_chat", None):
            return

        chat_id = chat.id
        logger.debug(f"[StrictSpam] Checking msg from {user.id} in {chat_id}")

        # Skip admins / creators
        try:
            cm = await context.bot.get_chat_member(chat_id, user.id)
            if cm.status in ("administrator", "creator"):
                logger.debug(f"[StrictSpam] Skipping admin {user.id}")
                return
        except Exception as e:
            # If we cannot check, proceed with scan (safe default)
            logger.warning(f"[StrictSpam] Admin check error: {e}")

        full_text = " ".join(filter(None, [msg.text, msg.caption]))

        # ── Check 1: NSFW text ────────────────────────────────────────────────
        if _is_nsfw(full_text):
            logger.info(f"[StrictSpam] NSFW from {user.id}: {full_text[:60]}")
            await _take_action(context, chat_id, user, msg, "NSFW / Inappropriate Content")
            return

        # ── Check 2: Unauthorized links ───────────────────────────────────────
        all_urls = _extract_all_urls(full_text)
        logger.debug(f"[StrictSpam] URLs found: {all_urls}")

        for url in all_urls:
            if ALLOWED_DOMAIN not in url.lower():
                logger.info(f"[StrictSpam] Blocked URL from {user.id}: {url}")
                await _take_action(
                    context, chat_id, user, msg,
                    f"Unauthorized Link\n(Only <code>{ALLOWED_DOMAIN}</code> links are permitted)"
                )
                return

        # ── Check 3: AI Smart Moderation (Gemini-powered) ─────────────────────
        words_count = len(full_text.strip().split())
        # To optimize and avoid API overhead, we only scan messages containing 4 or more words
        if words_count >= 4:
            from ai_engine import classify_content_with_gemini
            ai_result = await classify_content_with_gemini(text=full_text)
            
            if ai_result == "SPAM":
                logger.info(f"[StrictSpam] AI SPAM from {user.id}: {full_text[:60]}")
                await _take_action(context, chat_id, user, msg, "AI Moderation Guard: Spam/Advertisement detected")
                return
            elif ai_result == "NSFW":
                logger.info(f"[StrictSpam] AI NSFW from {user.id}: {full_text[:60]}")
                await _take_action(context, chat_id, user, msg, "AI Moderation Guard: Inappropriate/NSFW text detected")
                return

    except Exception as e:
        logger.error(f"[StrictSpam] Unhandled error: {e}", exc_info=True)
