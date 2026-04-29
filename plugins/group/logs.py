"""
plugins/group/logs.py
─────────────────────
Sends moderation logs to a configured log channel.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from .utils import get_log_channel, set_log_channel

logger = logging.getLogger(__name__)


async def send_log(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    """Send a log entry to the group's configured log channel."""
    try:
        lc = get_log_channel(chat_id)
        if not lc:
            return
        await context.bot.send_message(
            lc,
            f"📋 <b>Group:</b> <code>{chat_id}</code>\n{text}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"[GCP-Logs] send_log failed: {e}")


async def setlogchannel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: /setlogchannel <channel_id>"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if m.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Admin only.")
            return
        if not context.args:
            await update.message.reply_text(
                "Usage: /setlogchannel &lt;channel_id&gt;", parse_mode="HTML"
            )
            return
        try:
            lc_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid channel ID. Use a numeric ID.")
            return
        set_log_channel(update.effective_chat.id, lc_id)
        await update.message.reply_text(
            f"✅ Log channel set to <code>{lc_id}</code>.", parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP-Logs] setlogchannel_cmd: {e}")
