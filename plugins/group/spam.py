"""
plugins/group/spam.py
──────────────────────
Strict content filter:
- Scans ALL messages (including forwards, stickers, media) for NSFW keywords in text/caption
- Blocks any external link that is NOT from sliceurl.app
- On violation: delete message + permanently mute + tag user with report

Normal forwards, stickers, and media WITHOUT inappropriate content are fully allowed.
"""
import re
import logging
from telegram import Update, ChatPermissions
from telegram.constants import MessageEntityType
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

ALLOWED_DOMAIN = "sliceurl.app"

# ── Expanded NSFW / Scam keyword list ────────────────────────────────────────
_NSFW_KEYWORDS = [
    # English sexual
    "porn", "porno", "xxx", "nude", "nudes", "naked", "sex ", "sexy", "boob",
    "boobs", "tit", "tits", "dick", "cock", "pussy", "vagina", "penis", "orgasm",
    "masturbat", "erotic", "onlyfans", "nsfw", "hentai", "loli", "18+",
    "adult content", "bdsm", "blowjob", "cumshot", "creampie", "hardcore",
    "slutty", "slut", "whore", "bitch", "ass hole", "asshole", "fuck",
    "fucking", "fucked", "milf", "threesome", "gangbang", "dildo", "vibrator",
    # German sexual (common spam language as shown in screenshot)
    "muschi", "spritz", "nacktes", "loch", "umkl", "filmt", "spanner",
    # Hindi sexual
    "chudai", "lund", "chut", "gaand", "randi", "madarchod", "bhosdi",
    "harami", "lauda",
    # Scam indicators
    "earn money fast", "click here now", "free gift", "you won", "winner",
    "verify your account", "account suspended", "login here",
]

# ── Known scam/porn site patterns ────────────────────────────────────────────
_SCAM_LINK_PATTERNS = [
    r"pornhub\.com", r"xnxx\.com", r"xvideos\.com", r"redtube\.com",
    r"xhamster\.com", r"youporn\.com", r"spankbang\.com", r"onlyfans\.com",
    r"stripchat\.com", r"chaturbate\.com",
    r"t\.me/\+",            # Private Telegram invite links (scam/spam vector)
    r"telegra\.ph/",        # Often used for scam landing pages
    r"bit\.ly/", r"tinyurl\.com", r"short\.link",  # Link shorteners hiding scams
]


def _has_nsfw_text(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    for kw in _NSFW_KEYWORDS:
        if kw in t:
            return True
    return False


def _has_scam_link(text: str) -> bool:
    """True if any URL pattern matches a known scam/porn site."""
    if not text:
        return False
    for pat in _SCAM_LINK_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


# Regex to extract ALL URLs from raw text (covers http, https, www, t.me, etc.)
_URL_RE = re.compile(
    r"(?i)"
    r"(?:https?://|ftp://|www\.|t\.me/|@)?"     # optional scheme / prefix
    r"(?:[a-z0-9\-]+\.)+"                         # domain parts
    r"(?:com|net|org|io|app|me|ly|xyz|ru|co|in|info|biz|link|site|online|top|club)"
    r"(?:/[^\s]*)?"                                # optional path
)


def _find_urls_in_text(text: str) -> list:
    """Return all URLs/domains found in raw text via regex."""
    if not text:
        return []
    return _URL_RE.findall(text)


def _has_unauthorized_link(msg) -> bool:
    """
    True if the message contains ANY URL that is NOT sliceurl.app.
    Uses BOTH entity parsing AND raw-text regex to catch all cases.
    """
    full_text = (msg.text or "") + " " + (msg.caption or "")

    # --- Method 1: Telegram entity parsing ---
    ents = (msg.entities or []) + (msg.caption_entities or [])
    for e in ents:
        if e.type == MessageEntityType.TEXT_LINK:
            url = (e.url or "").lower()
            if ALLOWED_DOMAIN not in url:
                logger.info(f"[StrictSpam] Blocked entity TEXT_LINK: {url}")
                return True
        elif e.type == MessageEntityType.URL:
            text = msg.text or msg.caption or ""
            url = text[e.offset: e.offset + e.length].lower()
            if ALLOWED_DOMAIN not in url:
                logger.info(f"[StrictSpam] Blocked entity URL: {url}")
                return True

    # --- Method 2: Regex scan on raw text (catches non-entity URLs) ---
    for url in _find_urls_in_text(full_text):
        if ALLOWED_DOMAIN not in url.lower():
            logger.info(f"[StrictSpam] Blocked raw URL from regex: {url}")
            return True

    return False


# ── Guard handler ─────────────────────────────────────────────────────────────
async def strict_spam_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete + permanently mute for NSFW content or unauthorized links."""
    try:
        if not update.message or not update.effective_chat:
            return
        if update.effective_chat.type not in ("group", "supergroup"):
            return

        chat_id = update.effective_chat.id
        msg = update.message
        user = update.effective_user

        # Skip admins — if check fails, continue (don't abort silently)
        try:
            cm = await context.bot.get_chat_member(chat_id, user.id)
            if cm.status in ("administrator", "creator"):
                return
        except Exception as e:
            logger.warning(f"[StrictSpam] Could not check admin status: {e} — continuing with scan")

        # Combined text (message text + caption, e.g. on photos/videos/forwards)
        full_text = (msg.text or "") + " " + (msg.caption or "")

        violation = None

        # 1. NSFW keywords in text or caption
        if _has_nsfw_text(full_text):
            violation = "🔞 NSFW / Inappropriate Content"

        # 2. Scam/porn site links (even if ALLOWED_DOMAIN check passes)
        if not violation and _has_scam_link(full_text):
            violation = "🔗 Scam / Porn Site Link"

        # 3. Any external link that is NOT sliceurl.app
        if not violation and _has_unauthorized_link(msg):
            violation = f"🔗 Unauthorized External Link\n<i>(Only <code>{ALLOWED_DOMAIN}</code> links are allowed)</i>"

        if not violation:
            return

        # ── Take action ───────────────────────────────────────────────────────

        # Delete the offending message
        try:
            await msg.delete()
        except Exception:
            pass

        # Permanently mute the user
        try:
            await context.bot.restrict_chat_member(
                chat_id, user.id,
                permissions=ChatPermissions(can_send_messages=False)
            )
        except Exception as e:
            logger.warning(f"[StrictSpam] Mute failed: {e}")

        # Send a tagged warning in the group
        try:
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            mention = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
            await context.bot.send_message(
                chat_id,
                text=(
                    f"<b>» Security Alert</b>\n"
                    f"<code>──────────────────</code>\n"
                    f"<b>User  »</b> {mention}\n"
                    f"<b>ID     »</b> <code>{user.id}</code>\n"
                    f"<code>──────────────────</code>\n"
                    f"<b>Reason »</b> {violation}\n"
                    f"<code>──────────────────</code>\n"
                    f"<i>Message deleted · User muted\nIf this was a mistake, contact admin via button below.</i>"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Contact Admin", url="https://t.me/ItsNewtonPlanet")
                ]])
            )
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"[StrictSpam] Error: {e}")
