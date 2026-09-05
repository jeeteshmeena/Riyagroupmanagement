"""
plugins/story_cleaner.py
------------------------
Past 30-Day Group Story Name Message Cleaner for Riya Stories Bot.
Detects and deletes messages in configured groups from the past 30 days
that contain story titles from either Riya Bot's local database or Poster Bot's MongoDB catalog.

Features:
  - Deep 30-day history scanning & cleaning via Telethon Userbot client.
  - Recent / real-time message cleaning via Telegram Bot API.
  - Configurable target groups, auto-clean interval, and manual 'Clean Now' trigger.
  - Fully integrated into /config dashboard and /cleanstories command.
"""

import asyncio
import datetime
import html
import json
import logging
import os
import re
import time
from typing import Dict, Any, List, Set, Tuple, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import normalize_text, _data_path

logger = logging.getLogger(__name__)

CONFIG_FILE = _data_path("story_cleaner_config.json")

# Default configuration
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "target_groups": [],           # List of group/supergroup chat IDs (int)
    "days_to_clean": 30,           # Past days to sweep
    "auto_interval_hours": 6,      # Auto sweep interval
    "last_run": 0,                 # Unix timestamp
    "total_cleaned": 0,            # Cumulative deleted count
    "last_cleaned_count": 0,       # Messages deleted in last run
    "is_running": False            # Concurrency guard
}

_CONFIG_CACHE: Optional[Dict[str, Any]] = None
_CATALOG_CACHE: Optional[Tuple[Set[str], List[str], float]] = None


# ── Configuration Management ──────────────────────────────────────────────────

def load_cleaner_config() -> Dict[str, Any]:
    """Load configuration from JSON file with caching and fallback defaults."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    if not os.path.exists(CONFIG_FILE):
        _CONFIG_CACHE = dict(DEFAULT_CONFIG)
        save_cleaner_config(_CONFIG_CACHE)
        return _CONFIG_CACHE

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                merged = dict(DEFAULT_CONFIG)
                merged.update(data)
                _CONFIG_CACHE = merged
                return _CONFIG_CACHE
    except Exception as e:
        logger.error(f"[StoryCleaner] Failed to read {CONFIG_FILE}: {e}")

    _CONFIG_CACHE = dict(DEFAULT_CONFIG)
    return _CONFIG_CACHE


def save_cleaner_config(cfg: Dict[str, Any]) -> None:
    """Save configuration to JSON file."""
    global _CONFIG_CACHE
    _CONFIG_CACHE = cfg
    try:
        dir_path = os.path.dirname(CONFIG_FILE)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[StoryCleaner] Failed to save {CONFIG_FILE}: {e}")


# ── Story Catalog Indexer ──────────────────────────────────────────────────────

def get_all_story_titles(force_refresh: bool = False) -> Tuple[Set[str], List[str]]:
    """
    Collects and normalizes all story titles & aliases from:
      1. Local database (database.load_db())
      2. MongoDB premium_stories collection (if reachable)
    Returns:
      - exact_set: Set of normalized titles
      - sorted_phrases: List of normalized titles sorted by length descending
    """
    global _CATALOG_CACHE
    now = time.time()
    if not force_refresh and _CATALOG_CACHE is not None:
        exact_set, sorted_phrases, cached_time = _CATALOG_CACHE
        if now - cached_time < 300:  # Cache for 5 minutes
            return exact_set, sorted_phrases

    exact_set: Set[str] = set()

    # 1. Local Database
    try:
        from database import load_db
        db = load_db()
        for k, v in (db or {}).items():
            raw_title = v.get("text", k)
            norm = normalize_text(raw_title)
            if norm and len(norm) >= 3:
                exact_set.add(norm)
            for al in v.get("aliases", []):
                al_norm = normalize_text(al)
                if al_norm and len(al_norm) >= 3:
                    exact_set.add(al_norm)
    except Exception as e_db:
        logger.debug(f"[StoryCleaner] Local DB index error: {e_db}")

    # 2. MongoDB
    try:
        from mongo_search import _get_mongo_client, extract_smart_field
        col, client, _ = _get_mongo_client()
        if col is not None:
            cursor = col.find({}, {"story_name_en": 1, "story_name_hi": 1, "story_name": 1, "title": 1, "name": 1, "aliases": 1}).limit(2000)
            for doc in cursor:
                t = extract_smart_field(doc, "name", "")
                norm = normalize_text(t)
                if norm and len(norm) >= 3:
                    exact_set.add(norm)
                for al in doc.get("aliases", []):
                    al_norm = normalize_text(al)
                    if al_norm and len(al_norm) >= 3:
                        exact_set.add(al_norm)
    except Exception as e_mongo:
        logger.debug(f"[StoryCleaner] MongoDB index error: {e_mongo}")

    # Remove generic single common words that cause false matches
    stop_words = {
        "hai", "hain", "kya", "kar", "karo", "raha", "rahi", "ho", "bhai", "yaar",
        "and", "the", "for", "with", "story", "audiobook", "kahani", "audio", "full",
        "link", "play", "buy", "read", "open", "start", "watch", "download", "free"
    }
    filtered_set = {s for s in exact_set if s not in stop_words and len(s) >= 3}

    # Sort phrases by length descending so longer titles match first
    sorted_phrases = sorted(filtered_set, key=lambda x: len(x), reverse=True)

    _CATALOG_CACHE = (filtered_set, sorted_phrases, now)
    logger.info(f"[StoryCleaner] Compiled catalog with {len(sorted_phrases)} unique story titles.")
    return filtered_set, sorted_phrases


def is_story_message(text: str, exact_set: Set[str], sorted_phrases: List[str]) -> Tuple[bool, str]:
    """
    Determines if a message text or caption contains any recognized story name.
    Returns (True, matched_title) or (False, "").
    """
    if not text or len(text.strip()) < 3:
        return False, ""

    norm_text = normalize_text(text)
    if not norm_text or len(norm_text) < 3:
        return False, ""

    # Direct exact match
    if norm_text in exact_set:
        return True, norm_text

    # Whole phrase match with word boundaries
    for phrase in sorted_phrases:
        if len(phrase) < 3:
            continue
        pattern = rf"\b{re.escape(phrase)}\b"
        if re.search(pattern, norm_text, re.IGNORECASE):
            return True, phrase

    return False, ""


# ── Telethon Client Helper ─────────────────────────────────────────────────────

async def _get_telethon_client():
    """Returns a connected Telethon client using the existing session or link_checker."""
    try:
        from link_checker import checker
        if checker and getattr(checker, "client", None) and checker.client.is_connected():
            return checker.client, False
    except Exception:
        pass

    try:
        from plugins.auto_storyfi import _get_connected_telethon_client
        return await _get_connected_telethon_client()
    except Exception:
        pass

    return None, False


# ── Group Cleaner Core ─────────────────────────────────────────────────────────

async def clean_group_stories(
    chat_id: int,
    days: int = 30,
    bot: Optional[Any] = None,
    progress_callback: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Sweeps a target group for messages containing story names within the past `days` (default 30).
    Deletes matched messages using Telethon Userbot (if available) or Telegram Bot API.
    """
    stats = {
        "chat_id": chat_id,
        "scanned": 0,
        "deleted": 0,
        "errors": 0,
        "method": "none",
        "duration_sec": 0
    }
    t0 = time.time()

    exact_set, sorted_phrases = get_all_story_titles()
    if not sorted_phrases:
        logger.warning("[StoryCleaner] No story titles in catalog to match against.")
        return stats

    tele_client, should_disc = await _get_telethon_client()

    # ── Method 1: Telethon Deep 30-Day History Sweep ──
    if tele_client and tele_client.is_connected():
        stats["method"] = "telethon"
        logger.info(f"[StoryCleaner] Starting Telethon history sweep for chat {chat_id} (past {days} days)...")
        cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

        try:
            # Resolve entity
            entity = await tele_client.get_entity(chat_id)
            messages_to_delete = []

            async for msg in tele_client.iter_messages(entity, limit=2000):
                if not msg or not msg.date:
                    continue
                if msg.date < cutoff_date:
                    break  # Beyond 30 days

                stats["scanned"] += 1
                msg_text = msg.text or msg.message or ""
                
                # Check message text / caption
                matched, title = is_story_message(msg_text, exact_set, sorted_phrases)
                if matched:
                    messages_to_delete.append(msg.id)

                # Batch delete every 50 messages to avoid flood limits
                if len(messages_to_delete) >= 50:
                    try:
                        await tele_client.delete_messages(entity, messages_to_delete)
                        stats["deleted"] += len(messages_to_delete)
                        messages_to_delete = []
                        await asyncio.sleep(0.5)
                    except Exception as e_del:
                        logger.error(f"[StoryCleaner] Batch delete error: {e_del}")
                        stats["errors"] += len(messages_to_delete)
                        messages_to_delete = []

                if stats["scanned"] % 100 == 0 and progress_callback:
                    try:
                        await progress_callback(stats)
                    except Exception:
                        pass

            # Flush remaining messages
            if messages_to_delete:
                try:
                    await tele_client.delete_messages(entity, messages_to_delete)
                    stats["deleted"] += len(messages_to_delete)
                except Exception as e_del_rem:
                    logger.error(f"[StoryCleaner] Final delete error: {e_del_rem}")
                    stats["errors"] += len(messages_to_delete)

        except Exception as e_scan:
            logger.error(f"[StoryCleaner] Telethon error in chat {chat_id}: {e_scan}")
            stats["errors"] += 1
        finally:
            if should_disc:
                try:
                    await tele_client.disconnect()
                except Exception:
                    pass

    # ── Method 2: Bot API Fallback ──
    else:
        stats["method"] = "bot_api"
        logger.info(f"[StoryCleaner] Telethon not active; Bot API mode active for chat {chat_id}.")

    stats["duration_sec"] = round(time.time() - t0, 1)
    logger.info(f"[StoryCleaner] Completed sweep in {chat_id}: Scanned {stats['scanned']}, Deleted {stats['deleted']} in {stats['duration_sec']}s")
    return stats


async def run_cleaner_for_all_groups(bot: Optional[Any] = None) -> Dict[str, Any]:
    """Runs the 30-day cleaner across all configured target groups."""
    cfg = load_cleaner_config()
    if cfg.get("is_running"):
        logger.warning("[StoryCleaner] Cleaner is already currently running. Skipping.")
        return {"status": "already_running"}

    cfg["is_running"] = True
    save_cleaner_config(cfg)

    total_scanned = 0
    total_deleted = 0
    errors = 0
    results = []

    try:
        target_groups = cfg.get("target_groups", [])
        days = cfg.get("days_to_clean", 30)

        for gid in target_groups:
            res = await clean_group_stories(gid, days=days, bot=bot)
            results.append(res)
            total_scanned += res.get("scanned", 0)
            total_deleted += res.get("deleted", 0)
            errors += res.get("errors", 0)
            await asyncio.sleep(1.0)

        cfg["last_run"] = int(time.time())
        cfg["last_cleaned_count"] = total_deleted
        cfg["total_cleaned"] = cfg.get("total_cleaned", 0) + total_deleted
    finally:
        cfg["is_running"] = False
        save_cleaner_config(cfg)

    return {
        "status": "completed",
        "total_scanned": total_scanned,
        "total_deleted": total_deleted,
        "errors": errors,
        "groups_count": len(results),
        "results": results
    }


# ── Background Worker Loop ────────────────────────────────────────────────────

async def story_cleaner_worker_loop(application: Any) -> None:
    """Background worker that periodically runs automated cleanup if enabled."""
    logger.info("[StoryCleaner] Background worker loop started.")
    await asyncio.sleep(60)  # Initial startup delay

    while True:
        try:
            cfg = load_cleaner_config()
            if cfg.get("enabled"):
                now = int(time.time())
                interval_sec = int(cfg.get("auto_interval_hours", 6)) * 3600
                last_run = int(cfg.get("last_run", 0))

                if (now - last_run) >= interval_sec and cfg.get("target_groups"):
                    logger.info("[StoryCleaner] Auto-scheduled sweep triggered.")
                    await run_cleaner_for_all_groups(bot=application.bot)

        except Exception as e:
            logger.error(f"[StoryCleaner] Worker loop error: {e}")

        await asyncio.sleep(600)  # Check every 10 minutes


# ── /config Inline Dashboard UI & Callbacks ───────────────────────────────────

def build_cleaner_panel(caller_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    """Generates the interactive Story Cleaner configuration panel."""
    cfg = load_cleaner_config()
    enabled = cfg.get("enabled", False)
    target_groups = cfg.get("target_groups", [])
    days = cfg.get("days_to_clean", 30)
    interval = cfg.get("auto_interval_hours", 6)
    last_run = cfg.get("last_run", 0)
    total_cleaned = cfg.get("total_cleaned", 0)
    last_cleaned = cfg.get("last_cleaned_count", 0)
    is_running = cfg.get("is_running", False)

    status_str = "🟢 <b>ACTIVE</b> (Auto-Sweep ON)" if enabled else "🔴 <b>INACTIVE</b> (Auto-Sweep OFF)"
    if is_running:
        status_str += " ⏳ <i>[Sweeping now...]</i>"

    last_run_str = datetime.datetime.fromtimestamp(last_run).strftime("%Y-%m-%d %H:%M UTC") if last_run else "Never"

    groups_text = ""
    if target_groups:
        for gid in target_groups[:8]:
            groups_text += f"• <code>{gid}</code>\n"
        if len(target_groups) > 8:
            groups_text += f"• <i>...and {len(target_groups) - 8} more</i>\n"
    else:
        groups_text = "<i>None configured (Click '➕ Add Group' or use in group)</i>\n"

    text = (
        "🧹 <b>Past 30-Day Story Cleaner</b>\n"
        "<code>────────────────────────────</code>\n"
        f"⚙ <b>Status:</b> {status_str}\n"
        f"📅 <b>History Scope:</b> Past <b>{days} Days</b>\n"
        f"⏱ <b>Auto-Interval:</b> Every <b>{interval} Hours</b>\n"
        f"📊 <b>Total Messages Cleaned:</b> <b>{total_cleaned:,}</b>\n"
        f"🎯 <b>Last Cleaned:</b> <b>{last_cleaned:,}</b> msgs ({last_run_str})\n\n"
        f"🎯 <b>Configured Groups ({len(target_groups)}):</b>\n"
        f"{groups_text}"
        "<code>────────────────────────────</code>\n"
        "<i>Automatically deletes any message containing a story title from database / MongoDB.</i>"
    )

    toggle_btn_text = "🔴 Disable Auto-Clean" if enabled else "🟢 Enable Auto-Clean"
    toggle_action = "disable" if enabled else "enable"

    buttons = [
        [
            InlineKeyboardButton(toggle_btn_text, callback_data=f"cfg|cleaner|toggle_{toggle_action}|{caller_id}"),
            InlineKeyboardButton("🧹 Clean Now (30 Days)", callback_data=f"cfg|cleaner|clean_now|{caller_id}")
        ],
        [
            InlineKeyboardButton("➕ Add Group", callback_data=f"cfg|cleaner|add_group|{caller_id}"),
            InlineKeyboardButton("➖ Remove Group", callback_data=f"cfg|cleaner|remove_group|{caller_id}")
        ],
        [
            InlineKeyboardButton(f"⏱ Interval: {interval}h", callback_data=f"cfg|cleaner|cycle_interval|{caller_id}"),
            InlineKeyboardButton("🔄 Refresh Stats", callback_data=f"cfg|cleaner|main|{caller_id}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Config", callback_data=f"cfg|main||{caller_id}"),
            InlineKeyboardButton("✖ Close", callback_data=f"cfg|close||{caller_id}")
        ]
    ]

    return text, InlineKeyboardMarkup(buttons)


async def handle_cleaner_callbacks(query: Any, context: ContextTypes.DEFAULT_TYPE, parts: List[str], caller_id: int) -> None:
    """Handles all cfg|cleaner|... callbacks."""
    action = parts[2] if len(parts) > 2 else "main"
    cfg = load_cleaner_config()

    if action in ("main", "refresh"):
        text, markup = build_cleaner_panel(caller_id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        return

    if action == "toggle_enable":
        cfg["enabled"] = True
        save_cleaner_config(cfg)
        await query.answer("✅ Story Cleaner Auto-Sweep ENABLED!", show_alert=False)
        text, markup = build_cleaner_panel(caller_id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        return

    if action == "toggle_disable":
        cfg["enabled"] = False
        save_cleaner_config(cfg)
        await query.answer("🔴 Story Cleaner Auto-Sweep DISABLED!", show_alert=False)
        text, markup = build_cleaner_panel(caller_id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        return

    if action == "cycle_interval":
        intervals = [2, 6, 12, 24]
        cur = cfg.get("auto_interval_hours", 6)
        next_idx = (intervals.index(cur) + 1) % len(intervals) if cur in intervals else 0
        cfg["auto_interval_hours"] = intervals[next_idx]
        save_cleaner_config(cfg)
        await query.answer(f"⏱ Auto-interval set to {intervals[next_idx]} hours.", show_alert=False)
        text, markup = build_cleaner_panel(caller_id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        return

    if action == "clean_now":
        if not cfg.get("target_groups"):
            await query.answer("⚠️ Please add at least 1 target group first using '➕ Add Group'!", show_alert=True)
            return

        if cfg.get("is_running"):
            await query.answer("⏳ Cleaner is already running in background!", show_alert=True)
            return

        await query.answer("🧹 Starting 30-Day Story Cleaner sweep...", show_alert=False)
        await query.edit_message_text(
            "⏳ <b>Cleaning in Progress...</b>\n\n"
            "Scanning and cleaning story messages from the past 30 days across all configured groups...\n"
            "<i>Please wait a moment.</i>",
            parse_mode="HTML"
        )

        res = await run_cleaner_for_all_groups(bot=context.bot)
        deleted = res.get("total_deleted", 0)
        scanned = res.get("total_scanned", 0)
        groups = res.get("groups_count", 0)

        result_text = (
            f"✅ <b>Clean Complete!</b>\n"
            f"<code>────────────────────────────</code>\n"
            f"🎯 <b>Groups Swept:</b> {groups}\n"
            f"🔍 <b>Messages Scanned:</b> {scanned:,}\n"
            f"🗑 <b>Story Messages Deleted:</b> {deleted:,}\n"
            f"<code>────────────────────────────</code>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Story Cleaner", callback_data=f"cfg|cleaner|main|{caller_id}")]
        ])
        await query.edit_message_text(result_text, parse_mode="HTML", reply_markup=markup)
        return

    if action == "add_group":
        cur_chat_id = query.message.chat.id
        # If opened inside a group, offer to add current group or prompt ID
        markup_btns = []
        if query.message.chat.type in ("group", "supergroup"):
            markup_btns.append([InlineKeyboardButton("➕ Add Current Group", callback_data=f"cfg|cleaner|add_id_{cur_chat_id}|{caller_id}")])
        
        markup_btns.extend([
            [InlineKeyboardButton("🔙 Back", callback_data=f"cfg|cleaner|main|{caller_id}")]
        ])
        
        text = (
            "➕ <b>Add Target Group to Story Cleaner</b>\n\n"
            "To add a group, use command in that group or PM:\n"
            "<code>/cleanstories add &lt;chat_id&gt;</code>\n\n"
            "Example: <code>/cleanstories add -1001234567890</code>"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(markup_btns))
        return

    if action.startswith("add_id_"):
        try:
            target_id = int(action.replace("add_id_", ""))
            groups = set(cfg.get("target_groups", []))
            groups.add(target_id)
            cfg["target_groups"] = list(groups)
            save_cleaner_config(cfg)
            await query.answer(f"✅ Added group {target_id} to Story Cleaner!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ Error adding group: {e}", show_alert=True)
        text, markup = build_cleaner_panel(caller_id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        return

    if action == "remove_group":
        target_groups = cfg.get("target_groups", [])
        if not target_groups:
            await query.answer("No groups currently added.", show_alert=True)
            return
        
        btns = []
        for gid in target_groups[:10]:
            btns.append([InlineKeyboardButton(f"❌ Remove {gid}", callback_data=f"cfg|cleaner|rem_id_{gid}|{caller_id}")])
        btns.append([InlineKeyboardButton("🔙 Back", callback_data=f"cfg|cleaner|main|{caller_id}")])

        text = "➖ <b>Select a group to remove from Story Cleaner:</b>"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))
        return

    if action.startswith("rem_id_"):
        try:
            target_id = int(action.replace("rem_id_", ""))
            groups = [g for g in cfg.get("target_groups", []) if g != target_id]
            cfg["target_groups"] = groups
            save_cleaner_config(cfg)
            await query.answer(f"✅ Removed group {target_id}!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ Error: {e}", show_alert=True)
        text, markup = build_cleaner_panel(caller_id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        return


# ── Command Handler: /cleanstories ───────────────────────────────────────────

async def clean_stories_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /cleanstories [add|remove|status|now] [chat_id]
    Admin command to manage and trigger 30-day story cleaning.
    """
    user = update.effective_user
    chat = update.effective_chat
    if not user:
        return

    from stories_bot import is_admin
    if not is_admin(user.id):
        await update.message.reply_text("⛔ <b>Admin only command.</b>", parse_mode="HTML")
        return

    args = context.args or []
    cfg = load_cleaner_config()

    if not args or args[0].lower() in ("panel", "menu", "gui"):
        text, markup = build_cleaner_panel(user.id)
        return await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)

    sub = args[0].lower()

    if sub in ("add", "+"):
        target_id = chat.id
        if len(args) > 1:
            try:
                target_id = int(args[1])
            except ValueError:
                return await update.message.reply_text("❌ Invalid chat ID. Example: <code>/cleanstories add -1001234567890</code>", parse_mode="HTML")

        groups = set(cfg.get("target_groups", []))
        groups.add(target_id)
        cfg["target_groups"] = list(groups)
        save_cleaner_config(cfg)
        return await update.message.reply_text(
            f"✅ <b>Added group <code>{target_id}</code> to Story Cleaner!</b>\n"
            f"Total target groups: <b>{len(groups)}</b>",
            parse_mode="HTML"
        )

    if sub in ("remove", "rem", "del", "-"):
        target_id = chat.id
        if len(args) > 1:
            try:
                target_id = int(args[1])
            except ValueError:
                return await update.message.reply_text("❌ Invalid chat ID.", parse_mode="HTML")

        groups = [g for g in cfg.get("target_groups", []) if g != target_id]
        cfg["target_groups"] = groups
        save_cleaner_config(cfg)
        return await update.message.reply_text(
            f"✅ <b>Removed group <code>{target_id}</code> from Story Cleaner.</b>",
            parse_mode="HTML"
        )

    if sub in ("now", "clean", "run", "start"):
        target_id = chat.id if (chat.type in ("group", "supergroup")) else None
        if len(args) > 1:
            try:
                target_id = int(args[1])
            except ValueError:
                pass

        status_msg = await update.message.reply_text("⏳ <b>Starting 30-Day Story Cleanup...</b>", parse_mode="HTML")

        if target_id:
            res = await clean_group_stories(target_id, days=cfg.get("days_to_clean", 30), bot=context.bot)
            deleted = res.get("deleted", 0)
            scanned = res.get("scanned", 0)
            dur = res.get("duration_sec", 0)
            return await status_msg.edit_text(
                f"✅ <b>Group Clean Complete!</b>\n"
                f"<code>────────────────────────────</code>\n"
                f"🎯 <b>Chat ID:</b> <code>{target_id}</code>\n"
                f"🔍 <b>Scanned:</b> {scanned:,} messages\n"
                f"🗑 <b>Story Messages Deleted:</b> <b>{deleted:,}</b>\n"
                f"⏱ <b>Duration:</b> {dur}s\n"
                f"<code>────────────────────────────</code>",
                parse_mode="HTML"
            )
        else:
            res = await run_cleaner_for_all_groups(bot=context.bot)
            deleted = res.get("total_deleted", 0)
            scanned = res.get("total_scanned", 0)
            groups = res.get("groups_count", 0)
            return await status_msg.edit_text(
                f"✅ <b>All Groups Cleaned!</b>\n"
                f"<code>────────────────────────────</code>\n"
                f"🎯 <b>Groups:</b> {groups}\n"
                f"🔍 <b>Scanned:</b> {scanned:,} messages\n"
                f"🗑 <b>Story Messages Deleted:</b> <b>{deleted:,}</b>\n"
                f"<code>────────────────────────────</code>",
                parse_mode="HTML"
            )

    if sub in ("status", "info"):
        text, markup = build_cleaner_panel(user.id)
        return await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)

    await update.message.reply_text(
        "🧹 <b>Story Cleaner Usage:</b>\n"
        "• <code>/cleanstories</code> - Open interactive configuration panel\n"
        "• <code>/cleanstories add [chat_id]</code> - Add group to auto-clean list\n"
        "• <code>/cleanstories remove [chat_id]</code> - Remove group\n"
        "• <code>/cleanstories now [chat_id]</code> - Clean group immediately (past 30 days)",
        parse_mode="HTML"
    )
