import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from .utils import is_enabled # if needed

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
        # Not handled by this handler
        return 
        
    # We must intercept it, but wait, python-telegram-bot's MessageHandler 
    # doesn't easily stop propagation if we just return.
    # Actually, in PTB, if a handler triggers, it stops propagation to other handlers in the SAME group.
    # So if we put this in group 0, it stops it.
    
    msg = update.message
    if not msg:
        return

    GLOBAL_BROADCAST_WAITING.remove(user.id)

    from stories_bot import chat_languages, GROUP_ID

    chats = set(chat_languages.keys())
    if GROUP_ID:
        chats.add(str(GROUP_ID))
        
    status_msg = await msg.reply_text(f"🚀 Broadcasting exact post to {len(chats)} groups/channels...")
    
    success = 0
    failed = 0
    
    for cid in chats:
        try:
            await msg.copy(chat_id=int(cid))
            success += 1
            await asyncio.sleep(0.05) # flood control
        except Exception as e:
            failed += 1

    await status_msg.edit_text(f"✅ <b>Global Broadcast Complete!</b>\n\nSuccesful: {success}\nFailed: {failed}", parse_mode="HTML")
    return
