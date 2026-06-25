import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.ext.filters import MessageFilter

logger = logging.getLogger(__name__)

GLOBAL_BROADCAST_WAITING = set()

class BroadcastFilter(MessageFilter):
    def filter(self, message):
        return message.from_user and message.from_user.id in GLOBAL_BROADCAST_WAITING

broadcast_filter_instance = BroadcastFilter()


def parse_timeframe(tf: str) -> int:
    """Converts timeframe string (e.g., '30m', '2h') to seconds."""
    tf = tf.strip().lower()
    if not tf:
        return 0
    unit = tf[-1]
    try:
        val = int(tf[:-1])
    except ValueError:
        return 0
    if unit == 'm':
        return val * 60
    elif unit == 'h':
        return val * 3600
    elif unit == 'd':
        return val * 86400
    return 0


def _parse_chat_target(cid: str):
    """
    Parse a channel ID entry into (chat_id, message_thread_id).
    Accepted formats:
      -1001234567890          → (int, None)
      @channelname            → (str, None)
      -1001234567890:123      → (int, 123)   ← group topic
    """
    cid = str(cid).strip()
    if ":" in cid and not cid.startswith("@"):
        parts = cid.split(":", 1)
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    if cid.startswith("@"):
        return cid, None
    try:
        return int(cid), None
    except ValueError:
        return cid, None   # fallback: pass as-is and let Telegram reject it


async def broadcast_wait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in GLOBAL_BROADCAST_WAITING:
        return

    msg = update.message
    if not msg:
        return

    GLOBAL_BROADCAST_WAITING.remove(user.id)

    # Determine target chats
    selected = context.user_data.pop("broadcast_selected", None)

    if selected is None:
        # Broadcast to ALL
        from stories_bot import chat_languages, GROUP_ID
        chats = set(chat_languages.keys())
        if GROUP_ID:
            chats.add(str(GROUP_ID))
        label = "all"
    elif isinstance(selected, set) and len(selected) > 0:
        chats = selected
        label = f"{len(chats)} selected"
    else:
        await msg.reply_text("❌ No channels were selected. Broadcast cancelled.")
        return

    status_msg = await msg.reply_text(
        f"🚀 Broadcasting to <b>{label}</b> chats ({len(chats)} total)…",
        parse_mode="HTML"
    )

    success = 0
    failed  = 0

    for cid in chats:
        chat_id, thread_id = _parse_chat_target(cid)
        try:
            if msg.forward_origin or msg.reply_markup:
                # Forward to preserve inline buttons and forward origin
                kwargs = dict(chat_id=chat_id, from_chat_id=msg.chat_id, message_id=msg.message_id)
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                sent_msg = await context.bot.forward_message(**kwargs)
            else:
                kwargs = dict(chat_id=chat_id)
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                sent_msg = await msg.copy(**kwargs)
            
            # Log broadcast record for mass deletion
            if sent_msg:
                try:
                    from database import add_broadcast_record
                    import time
                    add_broadcast_record(chat_id=chat_id, message_id=sent_msg.message_id, timestamp=time.time())
                except Exception as log_err:
                    logger.error(f"[Broadcast Log] Failed to save record: {log_err}")

            success += 1
            await asyncio.sleep(0.05)   # flood control
        except Exception as e:
            logger.warning(f"[Broadcast] Failed for {cid}: {e}")
            failed += 1

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"✔ Successful : <b>{success}</b>\n"
        f"✘ Failed     : <b>{failed}</b>",
        parse_mode="HTML"
    )


async def clean_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes broadcasted messages sent within a specific timeframe (e.g., 30m, 1h)."""
    from stories_bot import is_admin
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "<b>🧹 Broadcast Cleaner</b>\n\n"
            "Deletes messages sent by this bot within a given timeframe.\n\n"
            "<b>Usage:</b>\n"
            "• <code>/clean_broadcast <timeframe></code> (deletes logged broadcasts from all chats)\n"
            "• <code>/clean_broadcast <timeframe> <chat_id></code> (deletes logged broadcasts in a specific chat)\n"
            "• <code>/clean_broadcast <timeframe> <keyword></code> (scans channel history and deletes matching messages)\n\n"
            "<b>Examples:</b>\n"
            "• <code>/clean_broadcast 30m</code> (delete last 30 mins from logs)\n"
            "• <code>/clean_broadcast 1h Popuper</code> (delete last 1 hour containing 'Popuper')\n"
            "• <code>/clean_broadcast 2h -1002737616327</code> (delete last 2 hours in this channel)\n\n"
            "<i>Timeframes: 1m, 30m, 1h, 2h, 24h</i>",
            parse_mode="HTML"
        )
        return

    tf = args[0]
    seconds = parse_timeframe(tf)
    if seconds <= 0:
        await update.message.reply_text("❌ Invalid timeframe. Use e.g. 30m, 1h, 2h.")
        return

    # Parse optional chat_id or keyword
    target = args[1] if len(args) > 1 else None
    keyword = None
    target_chat_id = None

    if target:
        if target.startswith("-") or target.isdigit():
            try:
                target_chat_id = int(target)
            except ValueError:
                keyword = target
        else:
            keyword = " ".join(args[1:])

    status_msg = await update.message.reply_text("🧹 <b>Processing cleanup... Please wait.</b>", parse_mode="HTML")

    success = 0
    failed = 0

    # 1. Deletion via Database Logs (fastest, uses Bot API)
    from database import load_broadcast_history, save_broadcast_history
    import time
    
    try:
        history = load_broadcast_history()
    except Exception as e:
        history = []
        logger.error(f"Failed to load broadcast history: {e}")

    now = time.time()
    to_delete = []
    remaining = []

    for record in history:
        r_time = record.get("timestamp", 0)
        r_chat = record.get("chat_id")
        r_msg = record.get("message_id")
        
        if now - r_time <= seconds:
            if target_chat_id is None or r_chat == target_chat_id:
                to_delete.append(record)
                continue
        remaining.append(record)

    if to_delete:
        await status_msg.edit_text(f"🧹 Deleting {len(to_delete)} logged broadcast messages...")
        for record in to_delete:
            try:
                await context.bot.delete_message(chat_id=record["chat_id"], message_id=record["message_id"])
                success += 1
            except Exception as e:
                logger.warning(f"Failed to delete logged message {record['message_id']} in {record['chat_id']}: {e}")
                failed += 1
        try:
            save_broadcast_history(remaining)
        except Exception as e:
            logger.error(f"Failed to save remaining broadcast history: {e}")

    # 2. Deletion via Telethon Scan (fallback for unlogged posts or keyword-specific delete)
    # Perform a Telethon history scan to find unlogged messages
    if keyword or (success == 0 and target_chat_id is None):
        await status_msg.edit_text("🔍 Scanning channels via Telethon to clean matching messages...")
        from database import load_story_channels, GROUP_ID
        from config import API_ID, API_HASH, SESSION_STRING
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from datetime import datetime, timezone

        if not SESSION_STRING:
            await status_msg.edit_text(
                f"🧹 <b>Cleanup Complete!</b>\n\n"
                f"Deleted from database logs: <b>{success}</b>\n"
                f"Failed: <b>{failed}</b>\n\n"
                f"⚠️ <i>Note: Telethon scan skipped because SESSION_STRING is empty in .env.</i>",
                parse_mode="HTML"
            )
            return

        # Build list of channels to scan
        scan_chats = set()
        from stories_bot import chat_languages
        for cid in chat_languages.keys():
            if str(cid).startswith("-100"):
                scan_chats.add(str(cid))
                
        try:
            story_ch = load_story_channels()
            for cat in ["ongoing", "completed"]:
                for ch in story_ch.get(cat, []):
                    scan_chats.add(str(ch))
        except Exception as e:
            logger.error(f"Failed to load story channels: {e}")

        if GROUP_ID:
            scan_chats.add(str(GROUP_ID))

        if target_chat_id:
            scan_chats = {str(target_chat_id)}

        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await status_msg.edit_text("❌ Telethon user session not authorized. Cannot scan history.")
                return

            for cid in scan_chats:
                parsed_chat, _ = _parse_chat_target(cid)
                try:
                    entity = await client.get_entity(parsed_chat)
                    async for message in client.iter_messages(entity, limit=50):
                        age_seconds = (datetime.now(timezone.utc) - message.date).total_seconds()
                        if age_seconds > seconds:
                            break # stop scanning older messages

                        match = False
                        if keyword:
                            if message.text and keyword.lower() in message.text.lower():
                                match = True
                        else:
                            # Fallback: identify Riya Bot posts by inline buttons
                            if message.reply_markup:
                                btn_text = str(message.reply_markup).lower()
                                if "play now" in btn_text or "backup" in btn_text or "more?" in btn_text:
                                    match = True

                        if match:
                            try:
                                await message.delete()
                                success += 1
                            except Exception as del_err:
                                logger.warning(f"Failed to delete message {message.id} in {cid} via Telethon: {del_err}")
                                failed += 1
                except Exception as ch_err:
                    logger.warning(f"Failed to scan/delete in {cid} via Telethon: {ch_err}")
        except Exception as client_err:
            logger.error(f"Telethon client error: {client_err}")
        finally:
            await client.disconnect()

    await status_msg.edit_text(
        f"🧹 <b>Cleanup Complete!</b>\n\n"
        f"✔ Messages Deleted: <b>{success}</b>\n"
        f"✘ Failed Deletions : <b>{failed}</b>",
        parse_mode="HTML"
    )

