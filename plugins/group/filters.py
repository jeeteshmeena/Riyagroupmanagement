"""
plugins/group/filters.py
─────────────────────────
Keyword auto-reply system.
- Case-insensitive keyword matching
- First match wins (per message)
- /addfilter keyword | reply
- /delfilter keyword
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from .utils import get_filters as _get_filters, add_filter, remove_filter, is_enabled

logger = logging.getLogger(__name__)


# ── Re-export for use in panel.py ─────────────────────────────────────────────
def get_filters(chat_id: int) -> list:
    return _get_filters(chat_id)


# ── Auto-reply guard ──────────────────────────────────────────────────────────
async def filters_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check messages against keyword filters and auto-reply."""
    try:
        if not update.message or not update.effective_chat:
            return
        if update.effective_chat.type not in ("group", "supergroup"):
            return

        chat_id = update.effective_chat.id
        if not is_enabled(chat_id):
            return

        text = (update.message.text or update.message.caption or "").lower()
        if not text:
            return

        for f in _get_filters(chat_id):
            if f.get("keyword", "") in text:
                await update.message.reply_text(f["reply"], parse_mode="HTML")
                return  # Only trigger first match per message

    except Exception as e:
        logger.warning(f"[GCP-Filters] filters_guard: {e}")


# ── Admin commands ────────────────────────────────────────────────────────────
async def addfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addfilter <keyword> | <reply>"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        cm = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if cm.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Admin only.")
            return

        raw = " ".join(context.args) if context.args else ""
        if "|" not in raw:
            await update.message.reply_text(
                "Usage: /addfilter &lt;keyword&gt; | &lt;reply&gt;", parse_mode="HTML"
            )
            return

        keyword, reply = raw.split("|", 1)
        keyword = keyword.strip()
        reply = reply.strip()

        if not keyword or not reply:
            await update.message.reply_text("⚠️ Both keyword and reply are required.")
            return

        add_filter(update.effective_chat.id, keyword, reply)
        await update.message.reply_text(
            f"✅ Filter added for: <code>{keyword}</code>", parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[GCP-Filters] addfilter_cmd: {e}")


async def delfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delfilter <keyword>"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        cm = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if cm.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Admin only.")
            return

        kw = " ".join(context.args).strip() if context.args else ""
        if not kw:
            await update.message.reply_text(
                "Usage: /delfilter &lt;keyword&gt;", parse_mode="HTML"
            )
            return

        ok = remove_filter(update.effective_chat.id, kw)
        if ok:
            await update.message.reply_text(
                f"✅ Filter removed: <code>{kw}</code>", parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"❌ No filter found for: <code>{kw}</code>", parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"[GCP-Filters] delfilter_cmd: {e}")
