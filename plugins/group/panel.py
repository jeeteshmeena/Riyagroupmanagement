"""
plugins/group/panel.py
───────────────────────
Main Group Control Panel — opens via /gcp
Only visible/usable by group admins.
All callback_data prefixed with "grp_" so it never clashes with
existing bot callbacks.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .utils import get_settings, save_settings, is_enabled
from .welcome import welcome_panel

logger = logging.getLogger(__name__)
_DIV = "━━━━━━━━━━━━━━━━━━"


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_bot_admin(user_id: int) -> bool:
    try:
        from config import ADMIN_ID, OWNER_ID
        from database import load_config
        if user_id in (OWNER_ID, ADMIN_ID):
            return True
        cfg = load_config()
        mods = cfg.get("moderators", [])
        return str(user_id) in mods or user_id in mods
    except Exception:
        return False


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return is_bot_admin(update.effective_user.id)


def _in_group(update: Update) -> bool:
    return (
        update.effective_chat is not None
        and update.effective_chat.type in ("group", "supergroup")
    )


# ── Main panel builder ────────────────────────────────────────────────────────
def _build_main_panel(chat_id: int) -> tuple:
    s = get_settings(chat_id)
    icon = "🟢" if s["enabled"] else "🔴"
    text = (
        f"<b>👮 Group Control Panel</b>\n{_DIV}\n"
        f"<i>Status: {icon} {'Active' if s['enabled'] else 'Inactive'}</i>\n\n"
        "Select a module to configure:"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👋 Welcome",    callback_data="grp_welcome"),
            InlineKeyboardButton("🛡 Moderation", callback_data="grp_mod"),
        ],
        [
            InlineKeyboardButton("🚫 NSFW",        callback_data="grp_nsfw"),
            InlineKeyboardButton("🔒 Locks",       callback_data="grp_locks"),
        ],
        [
            InlineKeyboardButton("📝 Filters",     callback_data="grp_filters"),
            InlineKeyboardButton("⚠️ Warnings",    callback_data="grp_warns"),
        ],
        [
            InlineKeyboardButton("📊 Logs",        callback_data="grp_logs"),
            InlineKeyboardButton("⚙️ Utilities",   callback_data="grp_utils"),
        ],
        [InlineKeyboardButton(f"{icon} Toggle System", callback_data="grp_toggle")],
        [InlineKeyboardButton("❌ Close",           callback_data="grp_close")],
    ])
    return text, markup


# ── /gcp command ──────────────────────────────────────────────────────────────
async def panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the Group Control Panel (admin only)."""
    try:
        if not _in_group(update):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
        text, markup = _build_main_panel(update.effective_chat.id)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        logger.error(f"[GCP] panel_cmd: {e}")


# ── /group on|off toggle command ──────────────────────────────────────────────
async def group_toggle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/group on  or  /group off"""
    try:
        if not _in_group(update):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
        chat_id = update.effective_chat.id
        s = get_settings(chat_id)
        if context.args and context.args[0].lower() in ("on", "off"):
            s["enabled"] = context.args[0].lower() == "on"
        else:
            s["enabled"] = not s["enabled"]
        save_settings(chat_id, s)
        state = "🟢 Enabled" if s["enabled"] else "🔴 Disabled"
        await update.message.reply_text(
            f"👮 Group Control System: <b>{state}</b>", parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP] group_toggle_cmd: {e}")


# ── Callback router ───────────────────────────────────────────────────────────
async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all grp_* callback queries."""
    try:
        query = update.callback_query
        if not query or not query.data or not query.data.startswith("grp_"):
            return

        # Admin check
        if not is_bot_admin(update.effective_user.id):
            await query.answer("⛔ Admin only.", show_alert=True)
            return

        await query.answer()
        action = query.data
        chat_id = update.effective_chat.id
        s = get_settings(chat_id)

        # ── Close ─────────────────────────────────────────────────────────────
        if action == "grp_close":
            await query.message.delete()
            return

        # ── Toggle system ──────────────────────────────────────────────────────
        if action == "grp_toggle":
            s["enabled"] = not s["enabled"]
            save_settings(chat_id, s)
            text, markup = _build_main_panel(chat_id)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        # ── Back to main ───────────────────────────────────────────────────────
        if action == "grp_back":
            text, markup = _build_main_panel(chat_id)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        # ── Welcome sub-panel ──────────────────────────────────────────────────
        if action == "grp_welcome":
            text, markup = welcome_panel(chat_id)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        if action == "grp_welcome_toggle_random":
            from .utils import get_welcome, save_welcome
            w = get_welcome(chat_id)
            w["random"] = not w.get("random", False)
            save_welcome(chat_id, w)
            text, markup = welcome_panel(chat_id)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        if action == "grp_welcome_enable":
            from .utils import is_welcome_enabled, set_welcome_enabled
            current = is_welcome_enabled(chat_id)
            set_welcome_enabled(chat_id, not current)
            status = "✅ Enabled" if not current else "❌ Disabled"
            await query.answer(f"Welcome {status}", show_alert=True)
            text, markup = welcome_panel(chat_id)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        # ── NSFW sub-panel ─────────────────────────────────────────────────────
        if action == "grp_nsfw":
            nsfw_on = s.get("nsfw", True)
            nsfw_strict = s.get("nsfw_strict", True)
            text = (
                f"<b>🚫 NSFW Guard</b>\n{_DIV}\n\n"
                f"System: <b>{'🟢 Enabled' if nsfw_on else '🔴 Disabled'}</b>\n"
                f"Mode: <b>{'Strict' if nsfw_strict else 'Lenient'}</b>\n\n"
                "<i>• Strict mode: Deletes content, then warns/mutes/bans the user depending on their warn count.\n"
                "• Lenient mode: Only deletes the content.</i>"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"{'🔴 Turn OFF' if nsfw_on else '🟢 Turn ON'}",
                    callback_data="grp_nsfw_toggle_main"
                )],
                [InlineKeyboardButton(
                    f"{'🔀 Switch to Lenient' if nsfw_strict else '🔀 Switch to Strict'}",
                    callback_data="grp_nsfw_toggle_strict"
                )],
                [InlineKeyboardButton("« Back", callback_data="grp_back")],
            ])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        if action == "grp_nsfw_toggle_main":
            s["nsfw"] = not s.get("nsfw", True)
            save_settings(chat_id, s)
            # Rebuild NSFW panel
            nsfw_on = s.get("nsfw", True)
            nsfw_strict = s.get("nsfw_strict", True)
            text = (
                f"<b>🚫 NSFW Guard</b>\n{_DIV}\n\n"
                f"System: <b>{'🟢 Enabled' if nsfw_on else '🔴 Disabled'}</b>\n"
                f"Mode: <b>{'Strict' if nsfw_strict else 'Lenient'}</b>\n\n"
                "<i>• Strict mode: Deletes content, then warns/mutes/bans the user depending on their warn count.\n"
                "• Lenient mode: Only deletes the content.</i>"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"{'🔴 Turn OFF' if nsfw_on else '🟢 Turn ON'}",
                    callback_data="grp_nsfw_toggle_main"
                )],
                [InlineKeyboardButton(
                    f"{'🔀 Switch to Lenient' if nsfw_strict else '🔀 Switch to Strict'}",
                    callback_data="grp_nsfw_toggle_strict"
                )],
                [InlineKeyboardButton("« Back", callback_data="grp_back")],
            ])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        if action == "grp_nsfw_toggle_strict":
            s["nsfw_strict"] = not s.get("nsfw_strict", True)
            save_settings(chat_id, s)
            # Rebuild NSFW panel
            nsfw_on = s.get("nsfw", True)
            nsfw_strict = s.get("nsfw_strict", True)
            text = (
                f"<b>🚫 NSFW Guard</b>\n{_DIV}\n\n"
                f"System: <b>{'🟢 Enabled' if nsfw_on else '🔴 Disabled'}</b>\n"
                f"Mode: <b>{'Strict' if nsfw_strict else 'Lenient'}</b>\n\n"
                "<i>• Strict mode: Deletes content, then warns/mutes/bans the user depending on their warn count.\n"
                "• Lenient mode: Only deletes the content.</i>"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"{'🔴 Turn OFF' if nsfw_on else '🟢 Turn ON'}",
                    callback_data="grp_nsfw_toggle_main"
                )],
                [InlineKeyboardButton(
                    f"{'🔀 Switch to Lenient' if nsfw_strict else '🔀 Switch to Strict'}",
                    callback_data="grp_nsfw_toggle_strict"
                )],
                [InlineKeyboardButton("« Back", callback_data="grp_back")],
            ])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        # ── Locks sub-panel ────────────────────────────────────────────────────
        if action == "grp_locks":
            locks = s.get("locks", {})
            buttons = [
                [InlineKeyboardButton(
                    f"{'🟢' if locks.get(k) else '🔴'} Lock {k.capitalize()}",
                    callback_data=f"grp_lock_toggle_{k}",
                )]
                for k in ("links", "media", "stickers", "forwards")
            ]
            buttons.append([InlineKeyboardButton("« Back", callback_data="grp_back")])
            lines = "\n".join(
                f"{'🟢 Locked' if locks.get(k) else '🔴 Unlocked'} : <b>{k.capitalize()}</b>"
                for k in ("links", "media", "stickers", "forwards")
            )
            text = f"<b>🔒 Locks Configuration</b>\n{_DIV}\n\n{lines}\n\n<i>Tap the buttons below to lock/unlock specific content types. Locked content will be automatically deleted when sent by normal users.</i>"
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        if action.startswith("grp_lock_toggle_"):
            key = action[len("grp_lock_toggle_"):]
            if "locks" not in s:
                s["locks"] = {}
            # Defaults to False if not present
            s["locks"][key] = not s["locks"].get(key, False)
            save_settings(chat_id, s)
            
            # Rebuild Locks Panel
            locks = s.get("locks", {})
            buttons = [
                [InlineKeyboardButton(
                    f"{'🟢' if locks.get(k) else '🔴'} Lock {k.capitalize()}",
                    callback_data=f"grp_lock_toggle_{k}",
                )]
                for k in ("links", "media", "stickers", "forwards")
            ]
            buttons.append([InlineKeyboardButton("« Back", callback_data="grp_back")])
            lines = "\n".join(
                f"{'🟢 Locked' if locks.get(k) else '🔴 Unlocked'} : <b>{k.capitalize()}</b>"
                for k in ("links", "media", "stickers", "forwards")
            )
            text = f"<b>🔒 Locks Configuration</b>\n{_DIV}\n\n{lines}\n\n<i>Tap the buttons below to lock/unlock specific content types. Locked content will be automatically deleted when sent by normal users.</i>"
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        # ── Warnings sub-panel ─────────────────────────────────────────────────
        if action == "grp_warns":
            limit = s.get("warn_limit", 3)
            mode = s.get("warn_mode", "mute").capitalize()
            text = (
                f"<b>⚠️ Warnings</b>\n{_DIV}\n\n"
                f"Warn limit: <b>{limit}</b>\n"
                f"Action on limit: <b>{mode}</b>\n\n"
                "<i>Use /setwarnlimit &lt;number&gt; to change.\n"
                "On limit: user is auto-muted or banned.</i>"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🔘 Mode: {mode}", callback_data="grp_warn_toggle_mode")],
                [InlineKeyboardButton("« Back", callback_data="grp_back")]
            ])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        if action == "grp_warn_toggle_mode":
            current = s.get("warn_mode", "mute")
            s["warn_mode"] = "ban" if current == "mute" else "mute"
            save_settings(chat_id, s)
            query.data = "grp_warns"
            await panel_callback(update, context)
            return

        # ── Filters sub-panel ──────────────────────────────────────────────────
        if action == "grp_filters":
            from .filters import get_filters
            flts = get_filters(chat_id)
            flist = (
                "\n".join(f"• <code>{f['keyword']}</code>" for f in flts[:20])
                if flts else "<i>No filters set.</i>"
            )
            text = (
                f"<b>📝 Filters</b>\n{_DIV}\n\n"
                f"{flist}\n\n"
                "<i>/addfilter &lt;keyword&gt; | &lt;reply&gt;\n"
                "/delfilter &lt;keyword&gt;</i>"
            )
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("« Back", callback_data="grp_back")
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        # ── Logs sub-panel ─────────────────────────────────────────────────────
        if action == "grp_logs":
            from .utils import get_log_channel
            lc = get_log_channel(chat_id)
            lc_str = f"<code>{lc}</code>" if lc else "<i>Not set</i>"
            text = (
                f"<b>📊 Logs</b>\n{_DIV}\n\n"
                f"Log channel: <b>{lc_str}</b>\n\n"
                "<i>/setlogchannel &lt;channel_id&gt; to configure.</i>"
            )
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("« Back", callback_data="grp_back")
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        # ── Utilities sub-panel ────────────────────────────────────────────────
        if action == "grp_utils":
            text = (
                f"<b>⚙️ Utilities</b>\n{_DIV}\n\n"
                "/id — Get chat/user IDs\n"
                "/userinfo [reply or user_id] — Detailed user profile\n"
                "/rules — View group rules\n"
                "/setrules &lt;text&gt; — Set group rules\n"
                "/clearrules — Clear group rules\n"
            )
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("« Back", callback_data="grp_back")
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

        # ── Moderation sub-panel ───────────────────────────────────────────────
        if action == "grp_mod":
            text = (
                f"<b>🛡 Moderation</b>\n{_DIV}\n\n"
                "/ban [reason] — Ban a user\n"
                "/dban [reason] — Delete msg + ban\n"
                "/unban — Unban a user\n"
                "/kick [reason] — Kick user (can rejoin)\n"
                "/mute [reason] — Mute a user\n"
                "/dmute [reason] — Delete msg + mute\n"
                "/unmute — Unmute a user\n"
                "/warn [reason] — Warn user\n"
                "/setwarnlimit &lt;n&gt; — Set warn threshold\n"
                "/purge — Delete msgs from replied to here\n"
                "/del — Delete replied message\n"
                "/pin [loud] — Pin message\n"
                "/unpin — Unpin message(s)\n"
            )
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("« Back", callback_data="grp_back")
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            return

    except Exception as e:
        logger.error(f"[GCP] panel_callback: {e}")
