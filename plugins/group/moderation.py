"""
plugins/group/moderation.py
───────────────────────────
Admin moderation commands: /ban /unban /mute /unmute /warn /dban /dmute /dwarn /dban
- dmute / dban / dwarn delete the replied message before moderating
- Auto-action (mute or ban) when warn count hits warn_limit
- Inline buttons for /mute (🔘 Unmute) and /warn (🔘 Remove Warn)
"""
import logging
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .utils import get_settings, save_settings, add_warn, reset_warns, is_enabled
from .logs import send_log

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


async def _get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return (user, reason_str) from a reply or first argument."""
    if update.message and update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        reason = " ".join(context.args) if context.args else ""
        return u, reason
    if context.args:
        try:
            uid = int(context.args[0])
            cm = await context.bot.get_chat_member(update.effective_chat.id, uid)
            reason = " ".join(context.args[1:])
            return cm.user, reason
        except Exception:
            pass
    return None, ""


def _in_group(update: Update) -> bool:
    return (
        update.effective_chat is not None
        and update.effective_chat.type in ("group", "supergroup")
    )


# ── /ban ─────────────────────────────────────────────────────────────────────
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
        target, reason = await _get_target(update, context)
        if not target:
            await update.message.reply_text("⚠️ Reply to a user or provide a user_id.")
            return
        
        chat = update.effective_chat
        admin = update.effective_user
        
        await context.bot.ban_chat_member(chat.id, target.id)
        
        res = reason if reason else "No reason provided"
        await update.message.reply_text(
            f"Admin <code>{admin.full_name}</code> has successfully banned <code>{target.full_name}</code> in <b>{chat.title}</b> !\n"
            f"<b>Reason: {res}</b>",
            parse_mode="HTML"
        )
        await send_log(context, chat.id,
                       f"🚫 Banned <code>{target.full_name}</code> ({target.id}).\nReason: {res}")
    except Exception as e:
        logger.error(f"[GCP-Mod] ban_cmd: {e}")


# ── /dban (delete + ban) ──────────────────────────────────────────────────────
async def dban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            return
        if update.message and update.message.reply_to_message:
            try:
                await update.message.reply_to_message.delete()
            except Exception:
                pass
        await ban_cmd(update, context)
        try:
            await update.message.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[GCP-Mod] dban_cmd: {e}")


# ── /unban ────────────────────────────────────────────────────────────────────
async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
        target, _ = await _get_target(update, context)
        if not target:
            await update.message.reply_text("⚠️ Reply to a user or provide a user_id.")
            return
        
        await context.bot.unban_chat_member(update.effective_chat.id, target.id, only_if_banned=True)
        await update.message.reply_text(
            f"Yep, this <code>{target.first_name}</code> can join!", parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP-Mod] unban_cmd: {e}")


# ── /mute ─────────────────────────────────────────────────────────────────────
async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
        target, reason = await _get_target(update, context)
        if not target:
            await update.message.reply_text("⚠️ Reply to a user or provide a user_id.")
            return
        
        chat = update.effective_chat
        admin = update.effective_user
        
        # Check if already muted (has right to speak)
        try:
            m = await context.bot.get_chat_member(chat.id, target.id)
            if m.status in ("left", "kicked"):
                await update.message.reply_text("This user isn't even in the chat, unmuting them won't make them talk more than they already do!")
                return
            if m.can_send_messages is False:
                await update.message.reply_text(f"This user already has the right to speak in <b>{chat.title}</b> .", parse_mode="HTML")
                return
        except Exception:
            pass

        await context.bot.restrict_chat_member(
            chat.id, target.id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        
        res = reason if reason else "No reason provided"
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔘 Unmute", callback_data=f"mod_unmute_{target.id}")
        ]])
        
        await update.message.reply_text(
            f"<code>{target.full_name}</code> was muted by <code>{admin.full_name}</code> in <b>{chat.title}</b>\n"
            f"<b>Reason: {res}</b>",
            parse_mode="HTML",
            reply_markup=markup
        )
        await send_log(context, chat.id,
                       f"🔇 Muted <code>{target.full_name}</code> ({target.id}).\nReason: {res}")
    except Exception as e:
        logger.error(f"[GCP-Mod] mute_cmd: {e}")


# ── /dmute (delete + mute) ────────────────────────────────────────────────────
async def dmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            return
        if update.message and update.message.reply_to_message:
            try:
                await update.message.reply_to_message.delete()
            except Exception:
                pass
        await mute_cmd(update, context)
        try:
            await update.message.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[GCP-Mod] dmute_cmd: {e}")


# ── /unmute ───────────────────────────────────────────────────────────────────
async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
        target, _ = await _get_target(update, context)
        if not target:
            await update.message.reply_text("⚠️ Reply to a user or provide a user_id.")
            return
        
        chat = update.effective_chat
        
        # Check if in chat
        try:
            m = await context.bot.get_chat_member(chat.id, target.id)
            if m.status in ("left", "kicked"):
                await update.message.reply_text("This user isn't even in the chat, unmuting them won't make them talk more than they already do!")
                return
        except Exception:
            pass

        await context.bot.restrict_chat_member(
            chat.id, target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        await update.message.reply_text(
            f"Yep, <code>{target.full_name}</code> can start talking again in <b>{chat.title}</b> !",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP-Mod] unmute_cmd: {e}")


# ── /warn ─────────────────────────────────────────────────────────────────────
async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
        target, reason = await _get_target(update, context)
        if not target:
            await update.message.reply_text("⚠️ Reply to a user or provide a user_id.")
            return
        
        chat_id = update.effective_chat.id
        chat_title = update.effective_chat.title
        s = get_settings(chat_id)
        limit = s.get("warn_limit", 3)
        count = add_warn(chat_id, target.id)
        res = reason if reason else "No reason provided"
        
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔘 Remove Warn", callback_data=f"mod_rmwarn_{target.id}")
        ]])

        if count >= limit:
            mode = s.get("warn_mode", "mute")
            reset_warns(chat_id, target.id)
            
            if mode == "ban":
                await context.bot.ban_chat_member(chat_id, target.id)
                await update.message.reply_text(
                    f"User <code>{target.full_name}</code> has been banned for reaching <b>{limit}</b> warnings!",
                    parse_mode="HTML"
                )
                await send_log(context, chat_id,
                               f"🔨 <code>{target.full_name}</code> auto-banned after {limit} warnings.")
            else:
                await context.bot.restrict_chat_member(
                    chat_id, target.id,
                    permissions=ChatPermissions(can_send_messages=False),
                )
                await update.message.reply_text(
                    f"User <code>{target.full_name}</code> has been muted for reaching <b>{limit}</b> warnings!",
                    parse_mode="HTML"
                )
                await send_log(context, chat_id,
                               f"🔇 <code>{target.full_name}</code> auto-muted after {limit} warnings.")
        else:
            await update.message.reply_text(
                f"User <code>{target.full_name}</code> has been warned!\n"
                f"They have <b>{count}</b> warnings.",
                parse_mode="HTML",
                reply_markup=markup
            )
            await send_log(context, chat_id,
                           f"⚠️ Warned <code>{target.full_name}</code> ({target.id}). {count}/{limit}.\nReason: {res}")
    except Exception as e:
        logger.error(f"[GCP-Mod] warn_cmd: {e}")


# ── /dwarn (delete + warn) ────────────────────────────────────────────────────
async def dwarn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            return
        if update.message and update.message.reply_to_message:
            try:
                await update.message.reply_to_message.delete()
            except Exception:
                pass
        await warn_cmd(update, context)
        try:
            await update.message.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[GCP-Mod] dwarn_cmd: {e}")


# ── /setwarnlimit ─────────────────────────────────────────────────────────────
async def setwarnlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text(
                "Usage: /setwarnlimit &lt;number&gt;", parse_mode="HTML"
            )
            return
        chat_id = update.effective_chat.id
        s = get_settings(chat_id)
        s["warn_limit"] = int(context.args[0])
        save_settings(chat_id, s)
        await update.message.reply_text(
            f"✅ Warn limit set to <b>{s['warn_limit']}</b>.", parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP-Mod] setwarnlimit_cmd: {e}")


# ── Callbacks ─────────────────────────────────────────────────────────────────
async def unmute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if not query.data.startswith("mod_unmute_"):
            return
        
        chat = update.effective_chat
        admin = update.effective_user
        
        # Admin check
        m = await context.bot.get_chat_member(chat.id, admin.id)
        if m.status not in ("administrator", "creator"):
            await query.answer("⛔ Admin only!", show_alert=True)
            return
        
        target_id = int(query.data.split("_")[2])
        
        await context.bot.restrict_chat_member(
            chat.id, target_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        
        await query.answer("User unmuted!")
        await query.edit_message_text(
            f"Yep , This User Unmuted by <code>{admin.full_name}</code> In <b>{chat.title}</b> .",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP-Mod] unmute_callback: {e}")


async def remove_warn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if not query.data.startswith("mod_rmwarn_"):
            return
        
        chat = update.effective_chat
        admin = update.effective_user
        
        # Admin check
        m = await context.bot.get_chat_member(chat.id, admin.id)
        if m.status not in ("administrator", "creator"):
            await query.answer("⛔ Admin only!", show_alert=True)
            return
        
        target_id = int(query.data.split("_")[2])
        reset_warns(chat.id, target_id)
        
        await query.answer("Warn removed!")
        await query.edit_message_text(
            f"Yep , Warn removed by <code>{admin.full_name}</code> .",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP-Mod] remove_warn_callback: {e}")
