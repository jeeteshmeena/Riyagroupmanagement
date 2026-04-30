"""
plugins/group/utils_cmds.py
────────────────────────────
Utility commands: /id  /info
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/id — show chat ID and user ID(s)"""
    try:
        chat = update.effective_chat
        user = update.effective_user

        lines = [
            "<b>🆔 ID Info</b>",
            "",
            f"<b>Chat ID:</b>   <code>{chat.id}</code>",
            f"<b>Chat Title:</b> {chat.title or 'N/A'}",
            "",
            f"<b>Your ID:</b>   <code>{user.id}</code>",
            f"<b>Your Name:</b> {user.full_name}",
        ]

        if update.message and update.message.reply_to_message:
            r = update.message.reply_to_message.from_user
            lines += [
                "",
                f"<b>Replied ID:</b>       <code>{r.id}</code>",
                f"<b>Replied Name:</b>     {r.full_name}",
                f"<b>Replied Username:</b> {'@' + r.username if r.username else 'N/A'}",
            ]

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"[GCP-Utils] id_cmd: {e}")


async def userinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/userinfo [reply or user_id] — show detailed user info"""
    try:
        chat_id = update.effective_chat.id
        target = None

        if update.message and update.message.reply_to_message:
            target = update.message.reply_to_message.from_user
        elif context.args:
            try:
                uid = int(context.args[0])
                cm = await context.bot.get_chat_member(chat_id, uid)
                target = cm.user
            except Exception:
                await update.message.reply_text("❌ Could not find a user with that ID.")
                return
        else:
            target = update.effective_user

        if not target:
            await update.message.reply_text("❌ Could not resolve user.")
            return

        try:
            cm = await context.bot.get_chat_member(chat_id, target.id)
            status = cm.status
        except Exception:
            status = "unknown"

        mention = f'<a href="tg://user?id={target.id}">{target.full_name}</a>'
        lines = [
            "<b>👤 User Info</b>",
            "",
            f"<b>Name:</b>     {mention}",
            f"<b>User ID:</b>  <code>{target.id}</code>",
            f"<b>Username:</b> {'@' + target.username if target.username else 'N/A'}",
            f"<b>Status:</b>   {status}",
            f"<b>Bot:</b>      {'Yes' if target.is_bot else 'No'}",
        ]
        await update.message.reply_text(
            "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"[GCP-Utils] info_cmd: {e}")
