"""
plugins/group/router.py
────────────────────────
Central handler registration for the Group Control System.

Called ONCE from stories_bot.py:

    from plugins.group.router import register_group_handlers
    register_group_handlers(app)

All handlers are registered in group=20 or higher so they never
conflict with the existing bot's handlers (which use default group=0).
Message guards run in priority groups 21-23.
If any import fails the entire plugin fails silently — the main bot
continues normally.
"""
import logging
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

logger = logging.getLogger(__name__)


def register_group_handlers(app: Application) -> None:
    """Register all Group Control System handlers. Safe to call once at startup."""
    try:
        from .panel       import panel_cmd, group_toggle_cmd, panel_callback
        from .welcome     import (new_member_handler, setwelcome_cmd,
                                  clearwelcome_cmd, previewwelcome_cmd)
        from .moderation  import (ban_cmd, dban_cmd, unban_cmd,
                                  mute_cmd, dmute_cmd, unmute_cmd,
                                  warn_cmd, dwarn_cmd, setwarnlimit_cmd,
                                  unmute_callback, remove_warn_callback)
        from .nsfw        import nsfw_guard
        from .locks       import locks_guard
        from .filters     import filters_guard, addfilter_cmd, delfilter_cmd
        from .logs        import setlogchannel_cmd
        from .utils_cmds  import id_cmd, info_cmd

        _g = 20   # Base handler group (above existing bot handlers)

        # ── Panel & toggle ─────────────────────────────────────────────────────
        app.add_handler(CommandHandler("gcp",   panel_cmd),         group=_g)
        app.add_handler(CommandHandler("group", group_toggle_cmd),  group=_g)

        # ── Welcome ────────────────────────────────────────────────────────────
        app.add_handler(CommandHandler("setwelcome",    setwelcome_cmd),    group=_g)
        app.add_handler(CommandHandler("clearwelcome",  clearwelcome_cmd),  group=_g)
        app.add_handler(CommandHandler("previewwelcome",previewwelcome_cmd),group=_g)
        app.add_handler(
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler),
            group=_g,
        )

        # ── Moderation ─────────────────────────────────────────────────────────
        app.add_handler(CommandHandler("ban",          ban_cmd),          group=_g)
        app.add_handler(CommandHandler("dban",         dban_cmd),         group=_g)
        app.add_handler(CommandHandler("unban",        unban_cmd),        group=_g)
        app.add_handler(CommandHandler("mute",         mute_cmd),         group=_g)
        app.add_handler(CommandHandler("dmute",        dmute_cmd),        group=_g)
        app.add_handler(CommandHandler("unmute",       unmute_cmd),       group=_g)
        app.add_handler(CommandHandler("warn",         warn_cmd),         group=_g)
        app.add_handler(CommandHandler("dwarn",        dwarn_cmd),        group=_g)
        app.add_handler(CommandHandler("setwarnlimit", setwarnlimit_cmd), group=_g)

        # ── Filters ────────────────────────────────────────────────────────────
        app.add_handler(CommandHandler("addfilter", addfilter_cmd), group=_g)
        app.add_handler(CommandHandler("delfilter", delfilter_cmd), group=_g)

        # ── Logs ───────────────────────────────────────────────────────────────
        app.add_handler(CommandHandler("setlogchannel", setlogchannel_cmd), group=_g)

        # ── Utilities ──────────────────────────────────────────────────────────
        app.add_handler(CommandHandler("id",   id_cmd),   group=_g)
        app.add_handler(CommandHandler("info", info_cmd), group=_g)

        # ── Message guards (groups only, non-commands) ─────────────────────────
        _grp_msg = filters.ChatType.GROUPS & ~filters.COMMAND
        app.add_handler(MessageHandler(_grp_msg, nsfw_guard),     group=21)
        app.add_handler(MessageHandler(_grp_msg, locks_guard),    group=22)
        app.add_handler(MessageHandler(_grp_msg, filters_guard),  group=23)

        # ── Inline callbacks (pattern ensures no clash) ──────────────────
        app.add_handler(
            CallbackQueryHandler(panel_callback, pattern=r"^grp_"),
            group=_g,
        )
        app.add_handler(
            CallbackQueryHandler(unmute_callback, pattern=r"^mod_unmute_"),
            group=_g,
        )
        app.add_handler(
            CallbackQueryHandler(remove_warn_callback, pattern=r"^mod_rmwarn_"),
            group=_g,
        )

        logger.info("[GCP] ✅ Group Control System loaded — %d handlers registered.",
                    20 + 3 + 8 + 2 + 1 + 2 + 3 + 1)  # rough count for log

    except Exception as e:
        # Plugin fails silently — existing bot is unaffected
        logger.error(f"[GCP] ❌ Failed to load Group Control plugin: {e}")
