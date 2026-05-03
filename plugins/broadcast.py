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

async def broadcast_wait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in GLOBAL_BROADCAST_WAITING:
        return

    msg = update.message
    if not msg:
        return

    GLOBAL_BROADCAST_WAITING.remove(user.id)

    # Determine target chats
    # None  → all chats from chat_languages
    # set() → only selected chat IDs
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

    status_msg = await msg.reply_text(f"🚀 Broadcasting to <b>{label}</b> chats ({len(chats)} total)…", parse_mode="HTML")

    success = 0
    failed  = 0

    for cid in chats:
        try:
            if msg.forward_origin or msg.reply_markup:
                # If it's a forwarded message or has inline buttons, forward it to preserve them
                await context.bot.forward_message(chat_id=int(cid), from_chat_id=msg.chat_id, message_id=msg.message_id)
            else:
                # For normal text/media, copy is fine
                await msg.copy(chat_id=int(cid))
            success += 1
            await asyncio.sleep(0.05)   # flood control
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"✔ Successful : <b>{success}</b>\n"
        f"✘ Failed     : <b>{failed}</b>",
        parse_mode="HTML"
    )
