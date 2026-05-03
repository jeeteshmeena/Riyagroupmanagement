"""
plugins/group/spam.py
──────────────────────
Strict spam, link, and media filter.
Permanently mutes users who send unauthorized links, media, stickers, or forwards.
Only 'sliceurl.app' is allowed as an external link.
"""
import logging
from telegram import Update, ChatPermissions
from telegram.constants import MessageEntityType
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

ALLOWED_DOMAIN = "sliceurl.app"

async def strict_spam_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Strictly delete and mute for forwards, stickers, media, and unauthorized links."""
    try:
        if not update.message or not update.effective_chat:
            return
        if update.effective_chat.type not in ("group", "supergroup"):
            return

        chat_id = update.effective_chat.id
        msg = update.message
        user = update.effective_user

        # Always skip admins
        try:
            cm = await context.bot.get_chat_member(chat_id, user.id)
            if cm.status in ("administrator", "creator"):
                return
        except Exception:
            return

        violation = None

        # 1. Forwards
        if hasattr(msg, "forward_origin") and msg.forward_origin:
            violation = "Forwarded messages"
        elif getattr(msg, "forward_date", None):
            violation = "Forwarded messages"

        # 2. Media & Stickers (including "porn videos" and "stickers")
        if not violation:
            if msg.sticker:
                violation = "Stickers"
            elif msg.video or msg.document or msg.animation or msg.photo or msg.video_note:
                violation = "Media/Videos"

        # 3. Links (only sliceurl.app allowed)
        if not violation:
            ents = msg.entities or []
            cents = msg.caption_entities or []
            all_ents = ents + cents
            
            has_link = False
            for e in all_ents:
                if e.type in (MessageEntityType.URL, MessageEntityType.TEXT_LINK):
                    url_text = ""
                    if e.type == MessageEntityType.TEXT_LINK:
                        url_text = e.url or ""
                    else:
                        # Extract the URL from the text
                        text = msg.text or msg.caption or ""
                        url_text = text[e.offset : e.offset + e.length]
                    
                    if url_text and ALLOWED_DOMAIN not in url_text.lower():
                        has_link = True
                        break
            if has_link:
                violation = f"External links (only {ALLOWED_DOMAIN} is allowed)"

        if violation:
            # Delete message
            try:
                await msg.delete()
            except Exception:
                pass
            
            # Permanently mute user
            try:
                await context.bot.restrict_chat_member(
                    chat_id, user.id,
                    permissions=ChatPermissions(can_send_messages=False)
                )
            except Exception as e:
                logger.warning(f"[StrictSpam] Mute failed: {e}")

            # Notify and tag user, reporting their ID
            try:
                alert_text = (
                    f"🚫 <b>Violation Detected!</b>\n"
                    f"👤 User: <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
                    f"🆔 ID: <code>{user.id}</code>\n\n"
                    f"⚠️ <b>{violation}</b> are strictly prohibited here.\n"
                    f"🔇 <i>You have been permanently muted.</i>"
                )
                await context.bot.send_message(
                    chat_id,
                    text=alert_text,
                    parse_mode="HTML"
                )
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"[StrictSpam] Error: {e}")
