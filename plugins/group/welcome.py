"""
plugins/group/welcome.py
────────────────────────
Welcome system: supports text messages (with {name} placeholder),
up to 5 messages per group, optional random selection.
"""
import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .utils import get_welcome, save_welcome, is_enabled

logger = logging.getLogger(__name__)
_DIV = "━━━━━━━━━━━━━━━━━━"


def welcome_panel(chat_id: int) -> tuple:
    """Build the welcome sub-panel text + markup."""
    w = get_welcome(chat_id)
    count = len(w.get("messages", []))
    rand = w.get("random", False)
    text = (
        f"<b>👋 Welcome System</b>\n{_DIV}\n\n"
        f"Messages set: <b>{count}/5</b>\n"
        f"Random mode:  <b>{'🟢 ON' if rand else '🔴 OFF'}</b>\n\n"
        "<i>Commands:\n"
        "/setwelcome &lt;text&gt; — add a welcome message (use {name} for user)\n"
        "/clearwelcome — remove all messages\n"
        "/previewwelcome — preview current message</i>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{'🟢' if rand else '🔴'} Toggle Random",
            callback_data="grp_welcome_toggle_random"
        )],
        [InlineKeyboardButton("« Back", callback_data="grp_back")],
    ])
    return text, markup


async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered when new members join the group."""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        chat_id = update.effective_chat.id
        if not is_enabled(chat_id):
            return

        new_members = (update.message.new_chat_members or []) if update.message else []
        if not new_members:
            return

        w = get_welcome(chat_id)
        msgs = w.get("messages", [])
        if not msgs:
            return

        msg = random.choice(msgs) if w.get("random", False) else msgs[0]

        for member in new_members:
            if member.is_bot:
                continue
            name = member.first_name or "Friend"
            content = msg.get("content", "Welcome!").replace("{name}", f"<b>{name}</b>")
            await update.message.reply_text(content, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"[GCP-Welcome] new_member_handler: {e}")


async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setwelcome <text>  — add a welcome message"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if m.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Admin only.")
            return

        chat_id = update.effective_chat.id
        w = get_welcome(chat_id)
        msgs = w.get("messages", [])

        if len(msgs) >= 5:
            await update.message.reply_text("⚠️ Max 5 welcome messages. Use /clearwelcome first.")
            return

        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text(
                "Usage: /setwelcome &lt;text&gt;\n"
                "<i>Use {name} as placeholder for the new member's name.</i>",
                parse_mode="HTML",
            )
            return

        msgs.append({"type": "text", "content": text})
        w["messages"] = msgs
        save_welcome(chat_id, w)
        await update.message.reply_text(f"✅ Welcome message added! ({len(msgs)}/5)")
    except Exception as e:
        logger.error(f"[GCP-Welcome] setwelcome_cmd: {e}")


async def clearwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clearwelcome — remove all welcome messages"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if m.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Admin only.")
            return
        save_welcome(update.effective_chat.id, {"messages": [], "random": False})
        await update.message.reply_text("✅ All welcome messages cleared.")
    except Exception as e:
        logger.error(f"[GCP-Welcome] clearwelcome_cmd: {e}")


async def previewwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/previewwelcome — show the current welcome message as if a new user joined"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        chat_id = update.effective_chat.id
        w = get_welcome(chat_id)
        msgs = w.get("messages", [])
        if not msgs:
            await update.message.reply_text("❌ No welcome messages set. Use /setwelcome.")
            return
        msg = random.choice(msgs) if w.get("random") else msgs[0]
        name = update.effective_user.first_name or "Friend"
        preview = msg.get("content", "").replace("{name}", f"<b>{name}</b>")
        await update.message.reply_text(
            f"<b>👀 Welcome Preview:</b>\n\n{preview}", parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP-Welcome] previewwelcome_cmd: {e}")
