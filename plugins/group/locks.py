"""
plugins/group/locks.py
───────────────────────
Content lock enforcement: links, media, stickers, forwards.
If a locked content type is sent by a non-admin, the message is deleted.
"""
import logging
from telegram import Update
from telegram.constants import MessageEntityType
from telegram.ext import ContextTypes
from .utils import get_settings, is_enabled
from .logs import send_log

logger = logging.getLogger(__name__)


async def locks_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete messages that violate active content locks."""
    try:
        if not update.message or not update.effective_chat:
            return
        if update.effective_chat.type not in ("group", "supergroup"):
            return

        chat_id = update.effective_chat.id
        if not is_enabled(chat_id):
            return

        # Always skip admins
        try:
            cm = await context.bot.get_chat_member(chat_id, update.effective_user.id)
            if cm.status in ("administrator", "creator"):
                return
        except Exception:
            return

        s = get_settings(chat_id)
        locks = s.get("locks", {})
        msg = update.message
        violated = None

        # Links lock
        if locks.get("links"):
            ents = msg.entities or []
            cents = msg.caption_entities or []
            all_ents = ents + cents
            if any(e.type in (MessageEntityType.URL, MessageEntityType.TEXT_LINK) for e in all_ents):
                violated = "links"

        # Media lock
        if not violated and locks.get("media"):
            if msg.photo or msg.video or msg.document or msg.audio or msg.animation or msg.voice or msg.video_note:
                violated = "media"

        # Stickers lock
        if not violated and locks.get("stickers"):
            if msg.sticker:
                violated = "stickers"

        # Forwards lock
        if not violated and locks.get("forwards"):
            # Check for modern PTB v20+ forward attribute
            if hasattr(msg, "forward_origin") and msg.forward_origin:
                violated = "forwards"
            # Fallback for older versions if still somehow used
            elif getattr(msg, "forward_date", None):
                violated = "forwards"

        if violated:
            try:
                await msg.delete()
            except Exception:
                pass
            user = update.effective_user
            try:
                await context.bot.send_message(
                    chat_id,
                    f"🔒 <b>{user.first_name}</b>, <b>{violated}</b> are locked in this group.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await send_log(
                context, chat_id,
                f"🔒 Deleted locked {violated} from {user.first_name} ({user.id})",
            )

    except Exception as e:
        logger.warning(f"[GCP-Locks] locks_guard: {e}")
