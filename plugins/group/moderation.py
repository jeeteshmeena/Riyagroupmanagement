"""
plugins/group/moderation.py
───────────────────────────
Admin moderation commands: /ban /unban /mute /unmute /warn /dban /dmute
- dmute / dban delete the replied message before moderating
- Auto-mute when warn count hits warn_limit
- All actions logged to configured log channel
"""
import logging
from telegram import Update, ChatPermissions
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
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        r = f"\n📝 Reason: {reason}" if reason else ""
        await update.message.reply_text(
            f"🚫 <b>{target.first_name}</b> has been banned.{r}", parse_mode="HTML"
        )
        await send_log(context, update.effective_chat.id,
                       f"🚫 Banned {target.first_name} ({target.id}).{r}")
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
            f"✅ <b>{target.first_name}</b> has been unbanned.", parse_mode="HTML"
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
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        r = f"\n📝 Reason: {reason}" if reason else ""
        await update.message.reply_text(
            f"🔇 <b>{target.first_name}</b> has been muted.{r}", parse_mode="HTML"
        )
        await send_log(context, update.effective_chat.id,
                       f"🔇 Muted {target.first_name} ({target.id}).{r}")
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
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        await update.message.reply_text(
            f"✅ <b>{target.first_name}</b> has been unmuted.", parse_mode="HTML"
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
        s = get_settings(chat_id)
        limit = s.get("warn_limit", 3)
        count = add_warn(chat_id, target.id)
        r = f"\n📝 Reason: {reason}" if reason else ""

        if count >= limit:
            await context.bot.restrict_chat_member(
                chat_id, target.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
            reset_warns(chat_id, target.id)
            await update.message.reply_text(
                f"⚠️ <b>{target.first_name}</b> warned ({count}/{limit}).{r}\n"
                f"🔇 Auto-muted for reaching the warn limit!",
                parse_mode="HTML",
            )
            await send_log(context, chat_id,
                           f"🔇 {target.first_name} auto-muted after {limit} warnings.{r}")
        else:
            await update.message.reply_text(
                f"⚠️ <b>{target.first_name}</b> warned ({count}/{limit}).{r}",
                parse_mode="HTML",
            )
            await send_log(context, chat_id,
                           f"⚠️ Warned {target.first_name} ({target.id}). {count}/{limit}.{r}")
    except Exception as e:
        logger.error(f"[GCP-Mod] warn_cmd: {e}")


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
