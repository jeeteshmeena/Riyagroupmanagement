import asyncio
import logging
import time
import uuid
from datetime import datetime
from telegram import Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler
)
from telegram import MessageEntity, InlineKeyboardMarkup
import database
from database import load_ads_db, save_ads_db, load_story_channels

_log = logging.getLogger(__name__)

# Conversation states
AD_NAME, AD_TARGETS, AD_CONTENT, AD_DELETE_TIME, AD_FREQ_TYPE, AD_FREQ_VAL = range(200, 206)

# ── Message Serializer & Deserializer ───────────────────────────────────────────
def message_to_dict(msg):
    """Serialize a Telegram Message to a JSON-safe dictionary preserving all fields."""
    d = {}
    if msg.text:
        d["text"] = msg.text
    if msg.caption:
        d["caption"] = msg.caption
        
    if msg.entities:
        d["entities"] = [ent.to_dict() for ent in msg.entities]
    if msg.caption_entities:
        d["caption_entities"] = [ent.to_dict() for ent in msg.caption_entities]
        
    if msg.photo:
        d["photo"] = [p.to_dict() for p in msg.photo]
    if msg.video:
        d["video"] = msg.video.to_dict()
    if msg.audio:
        d["audio"] = msg.audio.to_dict()
    if msg.document:
        d["document"] = msg.document.to_dict()
    if msg.voice:
        d["voice"] = msg.voice.to_dict()
    if msg.animation:
        d["animation"] = msg.animation.to_dict()
        
    if msg.reply_markup:
        d["reply_markup"] = msg.reply_markup.to_dict()
        
    return d

def _parse_entities(entities_data, bot):
    if not entities_data:
        return None
    return [MessageEntity.de_json(ent, bot) for ent in entities_data]

async def send_serialized_message(bot, chat_id, msg_dict, thread_id=None):
    """Deserialize and send a package of message details, preserving everything."""
    markup = None
    if "reply_markup" in msg_dict:
        markup = InlineKeyboardMarkup.de_json(msg_dict["reply_markup"], bot)
        
    entities = _parse_entities(msg_dict.get("entities"), bot)
    caption_entities = _parse_entities(msg_dict.get("caption_entities"), bot)
    
    kwargs = {}
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    if markup:
        kwargs["reply_markup"] = markup

    if "photo" in msg_dict:
        photo_id = msg_dict["photo"][-1]["file_id"]
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo_id,
            caption=msg_dict.get("caption"),
            caption_entities=caption_entities,
            **kwargs
        )
    elif "video" in msg_dict:
        return await bot.send_video(
            chat_id=chat_id,
            video=msg_dict["video"]["file_id"],
            caption=msg_dict.get("caption"),
            caption_entities=caption_entities,
            **kwargs
        )
    elif "audio" in msg_dict:
        return await bot.send_audio(
            chat_id=chat_id,
            audio=msg_dict["audio"]["file_id"],
            caption=msg_dict.get("caption"),
            caption_entities=caption_entities,
            **kwargs
        )
    elif "document" in msg_dict:
        return await bot.send_document(
            chat_id=chat_id,
            document=msg_dict["document"]["file_id"],
            caption=msg_dict.get("caption"),
            caption_entities=caption_entities,
            **kwargs
        )
    elif "voice" in msg_dict:
        return await bot.send_voice(
            chat_id=chat_id,
            voice=msg_dict["voice"]["file_id"],
            caption=msg_dict.get("caption"),
            caption_entities=caption_entities,
            **kwargs
        )
    elif "animation" in msg_dict:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=msg_dict["animation"]["file_id"],
            caption=msg_dict.get("caption"),
            caption_entities=caption_entities,
            **kwargs
        )
    else:
        return await bot.send_message(
            chat_id=chat_id,
            text=msg_dict.get("text", ""),
            entities=entities,
            disable_web_page_preview=True,
            **kwargs
        )

# ── Background loop scheduler task ──────────────────────────────────────────────
async def auto_ads_loop_task(app):
    """Background task to run auto ads and delete expired ads every 15s."""
    while True:
        try:
            now = time.time()
            db = load_ads_db()
            db_changed = False
            
            for ad_id, ad in list(db.items()):
                # 1. Process Auto-Delete of Active Posts
                active_posts = ad.get("active_posts", [])
                remaining_posts = []
                for post in active_posts:
                    if now >= post.get("delete_at", 0):
                        try:
                            await app.bot.delete_message(
                                chat_id=post["chat_id"],
                                message_id=post["message_id"]
                            )
                            _log.info(f"[Auto-Ads] Deleted expired post {post['message_id']} in {post['chat_id']}")
                        except Exception as e:
                            _log.warning(f"[Auto-Ads] Failed to delete expired post {post['message_id']} in {post['chat_id']}: {e}")
                        db_changed = True
                    else:
                        remaining_posts.append(post)
                ad["active_posts"] = remaining_posts
                
                # 2. Process Auto-Send of Due Ads
                if ad.get("status") == "active" and now >= ad.get("next_send_at", 0):
                    channels = ad.get("channels", [])
                    msg_dict = ad.get("message_dict")
                    auto_delete_mins = ad.get("auto_delete_minutes", 0)
                    
                    if channels and msg_dict:
                        new_active_posts = []
                        _log.info(f"[Auto-Ads] Sending Ad '{ad.get('name')}' ({ad_id}) to {len(channels)} channels...")
                        
                        for cid in channels:
                            from plugins.broadcast import _parse_chat_target
                            chat_id, thread_id = _parse_chat_target(cid)
                            try:
                                sent = await send_serialized_message(app.bot, chat_id, msg_dict, thread_id)
                                if sent and auto_delete_mins > 0:
                                    new_active_posts.append({
                                        "chat_id": chat_id,
                                        "message_id": sent.message_id,
                                        "delete_at": now + (auto_delete_mins * 60)
                                    })
                                await asyncio.sleep(0.05) # flood limit
                            except Exception as ex:
                                _log.warning(f"[Auto-Ads] Failed to send {ad_id} to {cid}: {ex}")
                        
                        if new_active_posts:
                            ad.setdefault("active_posts", []).extend(new_active_posts)
                            
                        # Recalculate schedule
                        ad["last_sent_at"] = now
                        interval_type = ad.get("interval_type", "hours")
                        interval_value = ad.get("interval_value", 1)
                        daily_frequency = ad.get("daily_frequency", 1)
                        
                        if interval_type == "minutes":
                            ad["next_send_at"] = now + (interval_value * 60)
                        elif interval_type == "hours":
                            ad["next_send_at"] = now + (interval_value * 3600)
                        elif interval_type == "daily":
                            ad["next_send_at"] = now + (24.0 / daily_frequency * 3600.0)
                        else:
                            ad["next_send_at"] = now + 3600.0
                            
                        db_changed = True
            
            if db_changed:
                save_ads_db(db)
                
        except Exception as exc:
            _log.error(f"[Auto-Ads] Loop task error: {exc}", exc_info=True)
            
        await asyncio.sleep(15)

# ── Ads Config dashboard views ───────────────────────────────────────────────────
def _get_ads_main_panel(caller_id: int) -> tuple:
    """Build the main Ads panel for the config menu."""
    db = load_ads_db()
    
    body_lines = []
    rows = []
    
    if db:
        for ad_id, ad in sorted(db.items(), key=lambda x: x[1].get("name", "")):
            status_emoji = "🟢" if ad.get("status") == "active" else "🔴"
            name = ad.get("name", "Ad")
            interval_type = ad.get("interval_type", "hours")
            interval_val = ad.get("interval_value", 1)
            daily_freq = ad.get("daily_frequency", 1)
            del_mins = ad.get("auto_delete_minutes", 0)
            
            if interval_type == "minutes":
                sched_str = f"every {interval_val} min"
            elif interval_type == "hours":
                sched_str = f"every {interval_val}h"
            else:
                sched_str = f"{daily_freq}x a day"
                
            del_str = f"{del_mins}m del" if del_mins > 0 else "no del"
            next_run_ts = ad.get("next_send_at", 0)
            next_run_str = datetime.fromtimestamp(next_run_ts).strftime("%H:%M") if next_run_ts else "?"
            
            body_lines.append(f"🎫 <b>{name}</b> ({status_emoji})\n   ⏰ {sched_str} | 🗑 {del_str} | ➔ {next_run_str}")
            rows.append([InlineKeyboardButton(f"⚙ Manage: {name}", callback_data=f"cfg|ads_view|{ad_id}|{caller_id}")])
    else:
        body_lines.append("<i>No automated ads configured.</i>")
        
    text = (
        "<b>🎫 Ads Automation Manager</b>\n"
        "<i>Create and schedule recurring ads with auto-delete timers.</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(body_lines)
        + "\n\n"
        "<i>✧ Use /newad to create a new ad.\n"
        "✧ Multi-ad configurations run fully in background.</i>"
    )
    
    rows.append([
        InlineKeyboardButton("➕ Create Ad", callback_data=f"cfg|ads_cnew||{caller_id}"),
        InlineKeyboardButton("➦ Back", callback_data=f"cfg|broadcast||{caller_id}")
    ])
    rows.append([InlineKeyboardButton("✖ Close", callback_data=f"cfg|close||{caller_id}")])
    
    return text, InlineKeyboardMarkup(rows)

def _get_ad_detail_panel(ad_id: str, caller_id: int) -> tuple:
    """Build the details panel for a specific Ad."""
    db = load_ads_db()
    ad = db.get(ad_id)
    if not ad:
        return "❌ Ad not found.", InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data=f"cfg|ads_main||{caller_id}")]])
        
    status_str = "🟢 Active" if ad.get("status") == "active" else "🔴 Paused"
    name = ad.get("name", "Ad")
    interval_type = ad.get("interval_type", "hours")
    interval_val = ad.get("interval_value", 1)
    daily_freq = ad.get("daily_frequency", 1)
    del_mins = ad.get("auto_delete_minutes", 0)
    channels_count = len(ad.get("channels", []))
    
    if interval_type == "minutes":
        sched_str = f"Every {interval_val} Minutes"
    elif interval_type == "hours":
        sched_str = f"Every {interval_val} Hours"
    else:
        sched_str = f"{daily_freq} Times a Day"
        
    del_str = f"Delete after {del_mins} minutes" if del_mins > 0 else "Auto-delete disabled"
    
    next_ts = ad.get("next_send_at", 0)
    next_str = datetime.fromtimestamp(next_ts).strftime("%Y-%m-%d %H:%M") if next_ts else "?"
    
    text = (
        f"<b>🎫 Ad: {name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"▪ <b>Status:</b> {status_str}\n"
        f"▪ <b>Target:</b> {channels_count} channel(s)\n"
        f"▪ <b>Schedule:</b> {sched_str}\n"
        f"▪ <b>Auto-Delete:</b> {del_str}\n"
        f"▪ <b>Next Run:</b> <code>{next_str}</code>\n"
    )
    
    toggle_label = "⏸ Pause Ad" if ad.get("status") == "active" else "▶ Resume Ad"
    
    rows = [
        [
            InlineKeyboardButton(toggle_label, callback_data=f"cfg|ads_toggle|{ad_id}|{caller_id}"),
            InlineKeyboardButton("🚀 Send Now", callback_data=f"cfg|ads_now|{ad_id}|{caller_id}")
        ],
        [
            InlineKeyboardButton("🗑 Delete Ad", callback_data=f"cfg|ads_del|{ad_id}|{caller_id}"),
            InlineKeyboardButton("« Back", callback_data=f"cfg|ads_main||{caller_id}")
        ]
    ]
    return text, InlineKeyboardMarkup(rows)

# ── Callback Handler for Ads Control ────────────────────────────────────────────
async def handle_ads_callbacks(query, context: ContextTypes.DEFAULT_TYPE, parts, caller_id):
    """Route all callback queries starting with cfg|ads_"""
    action = parts[1].split("_", 1)[1] if (len(parts) > 1 and "_" in parts[1]) else ""
    ad_id = parts[2] if len(parts) > 2 else ""
    user = query.from_user
    
    from stories_bot import is_admin
    if not is_admin(user.id):
        await query.answer("⛔ Admin only.", show_alert=True)
        return
        
    async def _safe_edit(text, markup=None):
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            if "Message is not modified" not in str(e):
                _log.warning(f"Error editing message: {e}")

    if action == "main" or not action:
        text, markup = _get_ads_main_panel(caller_id)
        await _safe_edit(text, markup)
        
    elif action == "view":
        text, markup = _get_ad_detail_panel(ad_id, caller_id)
        await _safe_edit(text, markup)
        
    elif action == "toggle":
        db = load_ads_db()
        if ad_id in db:
            ad = db[ad_id]
            ad["status"] = "paused" if ad.get("status") == "active" else "active"
            save_ads_db(db)
            await query.answer(f"Status changed to {ad['status'].upper()} ✅")
        text, markup = _get_ad_detail_panel(ad_id, caller_id)
        await _safe_edit(text, markup)
        
    elif action == "now":
        db = load_ads_db()
        if ad_id in db:
            ad = db[ad_id]
            # Reset schedule to now
            ad["next_send_at"] = time.time()
            ad["status"] = "active"
            save_ads_db(db)
            await query.answer("🚀 Triggered! Will broadcast in the next background check.")
        text, markup = _get_ad_detail_panel(ad_id, caller_id)
        await _safe_edit(text, markup)
        
    elif action == "del":
        db = load_ads_db()
        if ad_id in db:
            ad = db.pop(ad_id)
            # Try to delete sent active posts first
            active_posts = ad.get("active_posts", [])
            for post in active_posts:
                try:
                    await context.bot.delete_message(chat_id=post["chat_id"], message_id=post["message_id"])
                except:
                    pass
            save_ads_db(db)
            await query.answer("🗑 Ad deleted and active posts removed.")
        text, markup = _get_ads_main_panel(caller_id)
        await _safe_edit(text, markup)
        
    elif action == "cnew":
        await query.answer()
        # Abort panel and ask to use /newad
        await query.message.reply_text(
            "<b>🎫 Add New Ad</b>\n\n"
            "To add a new ad, send the command <code>/newad</code> in this chat.\n\n"
            "<i>This will launch the premium step-by-step Ad wizard.</i>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        try: await query.message.delete()
        except: pass

# ── Ads creation Step-by-Step ConversationHandler ──────────────────────────────
async def ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wizard Start: /newad command."""
    from stories_bot import is_admin
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return ConversationHandler.END
        
    context.user_data["new_ad"] = {}
    
    await update.message.reply_text(
        "<b>🎫 Automated Ad Creation Wizard</b>\n\n"
        "Step 1 of 5 — Send the <b>Name</b> for this Ad\n"
        "<i>Example: <code>Pocket FM Promo</code> or <code>SL Ads 1</code></i>\n\n"
        "Send /cancel to abort the wizard.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    return AD_NAME

async def ad_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Please enter a valid name.")
        return AD_NAME
        
    context.user_data["new_ad"]["name"] = name
    
    kb = ReplyKeyboardMarkup([
        ["🌐 All Channels / Groups"],
        ["▶ Ongoing Channels", "✔ Completed Channels"],
        ["« Custom Comma-Separated List"]
    ], resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        f"<b>Name saved:</b> <code>{name}</code>\n\n"
        "Step 2 of 5 — Choose the <b>Target Channels</b>\n"
        "Select an option below, or choose Custom List to type usernames manually.",
        parse_mode="HTML",
        reply_markup=kb
    )
    return AD_TARGETS

async def ad_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    channels = []
    
    from stories_bot import chat_languages
    from database import load_story_channels
    story_channels_db = load_story_channels()
    
    if text == "🌐 All Channels / Groups":
        from stories_bot import GROUP_ID
        ch_list = set(chat_languages.keys())
        if GROUP_ID:
            ch_list.add(str(GROUP_ID))
        channels = list(ch_list)
    elif text == "▶ Ongoing Channels":
        channels = story_channels_db.get("ongoing", [])
    elif text == "✔ Completed Channels":
        channels = story_channels_db.get("completed", [])
    elif text == "« Custom Comma-Separated List":
        await update.message.reply_text(
            "Type or paste target channel usernames/IDs separated by commas:\n"
            "<i>Example: <code>@MyChannel, -1001234567890</code></i>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        return AD_TARGETS
    else:
        # Fallback: parse comma separated values
        items = [i.strip() for i in text.split(",") if i.strip()]
        if not items:
            await update.message.reply_text("❌ No target channels detected. Try again:")
            return AD_TARGETS
        channels = items
        
    if not channels:
        await update.message.reply_text("❌ Selected target list is empty. Choose target again:", reply_markup=ReplyKeyboardRemove())
        return AD_NAME
        
    context.user_data["new_ad"]["channels"] = channels
    
    await update.message.reply_text(
        f"<b>Target selected:</b> <code>{len(channels)} channels</code>\n\n"
        "Step 3 of 5 — Send the <b>exact Ad Post</b>\n"
        "<i>You can send an image, video, file, audio, plain text, or a forwarded post with inline buttons. ALL formatting and formatting fonts will be preserved.</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    return AD_CONTENT

async def ad_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    msg_dict = message_to_dict(msg)
    
    context.user_data["new_ad"]["message_dict"] = msg_dict
    
    kb = ReplyKeyboardMarkup([
        ["Disabled (0)", "15", "30"],
        ["60 (1 hour)", "120 (2 hours)", "240 (4 hours)"]
    ], resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "<b>Ad message saved successfully!</b>\n\n"
        "Step 4 of 5 — Set <b>Auto-Delete Time</b> (in minutes)\n"
        "Select an option below or type the number of minutes after which this ad will be automatically deleted from all channels:",
        parse_mode="HTML",
        reply_markup=kb
    )
    return AD_DELETE_TIME

async def ad_delete_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    
    mins = 0
    if "disabled" in text or "0" in text:
        mins = 0
    elif "1 hour" in text or "60" in text:
        mins = 60
    elif "2 hours" in text or "120" in text:
        mins = 120
    elif "4 hours" in text or "240" in text:
        mins = 240
    else:
        try:
            mins = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid input. Please type a valid number of minutes:")
            return AD_DELETE_TIME
            
    context.user_data["new_ad"]["auto_delete_minutes"] = mins
    
    kb = ReplyKeyboardMarkup([
        ["Minutes", "Hours"],
        ["Daily Frequency"]
    ], resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        f"<b>Auto-delete:</b> <code>{mins} minutes</code>\n\n"
        "Step 5 of 5 — Choose the <b>Schedule Type</b>\n"
        "Choose how you want to schedule the ad sends:",
        parse_mode="HTML",
        reply_markup=kb
    )
    return AD_FREQ_TYPE

async def ad_freq_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text == "Minutes":
        context.user_data["new_ad"]["interval_type"] = "minutes"
        prompt = "How many minutes? (e.g. 15, 30, 45):"
    elif text == "Hours":
        context.user_data["new_ad"]["interval_type"] = "hours"
        prompt = "How many hours? (e.g. 1, 2, 4, 6):"
    elif text == "Daily Frequency":
        context.user_data["new_ad"]["interval_type"] = "daily"
        prompt = "How many times a day? (e.g. 10 to send equally spaced 10 times a day):"
    else:
        await update.message.reply_text("❌ Select from keyboard:")
        return AD_FREQ_TYPE
        
    await update.message.reply_text(prompt, reply_markup=ReplyKeyboardRemove())
    return AD_FREQ_VAL

async def ad_freq_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        val = int(text)
        if val <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a positive integer:")
        return AD_FREQ_VAL
        
    new_ad = context.user_data.pop("new_ad")
    ad_id = f"ad_{int(time.time())}"
    
    interval_type = new_ad["interval_type"]
    if interval_type == "minutes":
        new_ad["interval_value"] = val
    elif interval_type == "hours":
        new_ad["interval_value"] = val
    elif interval_type == "daily":
        new_ad["daily_frequency"] = val
        
    new_ad["status"] = "active"
    new_ad["last_sent_at"] = 0
    new_ad["next_send_at"] = time.time()
    new_ad["active_posts"] = []
    
    db = load_ads_db()
    db[ad_id] = new_ad
    save_ads_db(db)
    
    await update.message.reply_text(
        "<b>✅ Automated Ad Scheduled Successfully!</b>\n\n"
        f"Name: <code>{new_ad['name']}</code>\n"
        f"Schedule: <code>{interval_type.capitalize()} value of {val}</code>\n"
        f"Auto-delete: <code>{new_ad['auto_delete_minutes']} minutes</code>\n\n"
        "<i>Ad has been activated and will run instantly in the background loop.</i>",
        parse_mode="HTML"
    )
    return ConversationHandler.END

async def ad_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_ad", None)
    await update.message.reply_text("❌ Ad Wizard cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ── Handler Registration Helpers ────────────────────────────────────────────────
def get_ads_creator_handler():
    """Return the ConversationHandler for the /newad wizard."""
    return ConversationHandler(
        entry_points=[CommandHandler("newad", ad_start)],
        states={
            AD_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_name)],
            AD_TARGETS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_targets)],
            AD_CONTENT:     [MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.Document.ALL | filters.VOICE) & ~filters.COMMAND, ad_content)],
            AD_DELETE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_delete_time)],
            AD_FREQ_TYPE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_freq_type)],
            AD_FREQ_VAL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_freq_val)],
        },
        fallbacks=[CommandHandler("cancel", ad_cancel)],
        allow_reentry=True,
    )
