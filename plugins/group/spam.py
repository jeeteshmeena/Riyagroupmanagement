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

# Also catch bare domains like "elevenlabs.io" (no scheme)
_BARE_DOMAIN_RE = re.compile(
    r"(?<![/@\w])"                   # not part of an email or longer word
    r"(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)"  # sub/domain
    r"(?:com|net|org|io|app|me|ly|xyz|ru|co|uk|in|info|biz|link|site|online|top|club|ai)"
    r"(?:/[^\s]*)?",
    re.IGNORECASE,
)


def _extract_all_urls(text: str) -> list[str]:
    urls = _URL_RE.findall(text)
    urls += _BARE_DOMAIN_RE.findall(text)
    return urls


def _is_nsfw(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _NSFW_KEYWORDS)


async def _take_action(context, chat_id: int, user, msg, reason: str):
    """Delete message, permanently mute user, send alert."""
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
        mention = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
        await context.bot.send_message(
            chat_id,
            text=(
                f"<b>» Security Alert</b>\n"
                f"<code>──────────────────</code>\n"
                f"<b>User  »</b> {mention}\n"
                f"<b>ID     »</b> <code>{user.id}</code>\n"
                f"<code>──────────────────</code>\n"
                f"<b>Reason »</b> {reason}\n"
                f"<code>──────────────────</code>\n"
                f"<i>Message deleted · User muted\n"
                f"If this was a mistake, contact admin via button below.</i>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Contact Admin", url="https://t.me/ItsNewtonPlanet")
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

    except Exception as e:
        logger.error(f"[StrictSpam] Unhandled error: {e}", exc_info=True)
