"""
plugins/group/rules.py
──────────────────────
Handles group rules settings and displaying.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .utils import _load_json, _save_json, _data_path, is_enabled

logger = logging.getLogger(__name__)

GRP_RULES_FILE = _data_path("grp_rules.json")


def get_rules(chat_id: int) -> str:
    db = _load_json(GRP_RULES_FILE)
    return db.get(str(chat_id), "")


def save_rules(chat_id: int, rules_text: str) -> None:
    db = _load_json(GRP_RULES_FILE)
    db[str(chat_id)] = rules_text
    _save_json(GRP_RULES_FILE, db)


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


def _in_group(update: Update) -> bool:
    return (
        update.effective_chat is not None
        and update.effective_chat.type in ("group", "supergroup")
    )


# ── /rules ────────────────────────────────────────────────────────────────────
async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
            
        rules_text = get_rules(update.effective_chat.id)
        if not rules_text:
            await update.message.reply_text("No rules set for this chat.")
            return
            
        text = f"<b>Rules for {update.effective_chat.title}:</b>\n\n{rules_text}"
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"[GCP-Rules] rules_cmd: {e}")


# ── /setrules ─────────────────────────────────────────────────────────────────
async def setrules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
            
        if not context.args:
            await update.message.reply_text("Usage: /setrules <rules text>")
            return
            
        rules_text = update.message.text.split(None, 1)[1]
        save_rules(update.effective_chat.id, rules_text)
        await update.message.reply_text("✅ Rules updated successfully.")
    except Exception as e:
        logger.error(f"[GCP-Rules] setrules_cmd: {e}")


# ── /clearrules ───────────────────────────────────────────────────────────────
async def clearrules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not _in_group(update) or not is_enabled(update.effective_chat.id):
            return
        if not await _is_admin(update, context):
            await update.message.reply_text("⛔ Admin only.")
            return
            
        save_rules(update.effective_chat.id, "")
        await update.message.reply_text("✅ Rules cleared successfully.")
    except Exception as e:
        logger.error(f"[GCP-Rules] clearrules_cmd: {e}")
