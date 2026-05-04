import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.ext.filters import MessageFilter

logger = logging.getLogger(__name__)

GLOBAL_BROADCAST_WAITING = set()

class BroadcastFilter(MessageFilter):
    def filter(self, message):
        return message.from_user and message.from_user.id in GLOBAL_BROADCAST_WAITING

broadcast_filter_instance = BroadcastFilter()


def _parse_chat_target(cid: str):
    """
    Parse a channel ID entry into (chat_id, message_thread_id).
    Accepted formats:
      -1001234567890          → (int, None)
      @channelname            → (str, None)
      -1001234567890:123      → (int, 123)   ← group topic
    """
    cid = str(cid).strip()
    if ":" in cid and not cid.startswith("@"):
        parts = cid.split(":", 1)
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    if cid.startswith("@"):
        return cid, None
    try:
        return int(cid), None
    except ValueError:
        return cid, None   # fallback: pass as-is and let Telegram reject it


async def broadcast_wait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in GLOBAL_BROADCAST_WAITING:
        return

    msg = update.message
    if not msg:
        return

    GLOBAL_BROADCAST_WAITING.remove(user.id)

    # Determine target chats
    selected = context.user_data.pop("broadcast_selected", None)

    if selected is None:
        # Broadcast to ALL
        from stories_bot import chat_languages, GROUP_ID
        chats = set(chat_languages.keys())
        if GROUP_ID:
            chats.add(str(GROUP_ID))
        label = "all"
    elif isinstance(selected, set) and len(selected) > 0:
        chats = selected
        label = f"{len(chats)} selected"
    else:
        await msg.reply_text("❌ No channels were selected. Broadcast cancelled.")
        return

    status_msg = await msg.reply_text(
        f"🚀 Broadcasting to <b>{label}</b> chats ({len(chats)} total)…",
        parse_mode="HTML"
    )

    success = 0
    failed  = 0

    for cid in chats:
        chat_id, thread_id = _parse_chat_target(cid)
        try:
            if msg.forward_origin or msg.reply_markup:
                # Forward to preserve inline buttons and forward origin
                kwargs = dict(chat_id=chat_id, from_chat_id=msg.chat_id, message_id=msg.message_id)
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                await context.bot.forward_message(**kwargs)
            else:
                kwargs = dict(chat_id=chat_id)
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                await msg.copy(**kwargs)
            success += 1
            await asyncio.sleep(0.05)   # flood control
        except Exception as e:
            logger.warning(f"[Broadcast] Failed for {cid}: {e}")
            failed += 1

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"✔ Successful : <b>{success}</b>\n"
        f"✘ Failed     : <b>{failed}</b>",
        parse_mode="HTML"
    )
