"""
plugins/poster_bot.py
---------------------
Standalone 24/7 Telegram Automatic Story Poster Bot plugin for riya-stories-bot.
Fetches story content from MongoDB in READ-ONLY mode, formats post with HTML/Monospace layout,
applies PNG watermark with Pillow, attaches Inline Keyboard deep-links, and auto-deletes published posts.
"""

import asyncio
import html
import io
import json
import logging
import os
import time
from typing import Tuple, Dict, Any, Optional

try:
    import pymongo
except ImportError:
    pymongo = None

import requests
from PIL import Image
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import OWNER_ID, ADMIN_ID, DATA_DIR

logger = logging.getLogger(__name__)

# File path for poster settings
POSTER_SETTINGS_FILE = os.path.join(DATA_DIR, "poster_settings.json")


def load_poster_settings() -> Dict[str, Any]:
    """Load poster configuration settings from local storage."""
    default = {
        "active": False,
        "target_channel": "",
        "interval_minutes": 30,
        "delete_delay_hours": 0,
        "rot_idx": 0,
        "mongo_uri": os.getenv("MONGO_URI", ""),
        "database_name": os.getenv("DATABASE_NAME", "arya_premium"),
        "collection_name": "premium_stories",
        "watermark": {
            "enabled": True,
            "path": "watermark.png",
            "position": "bottom_right",
            "opacity": 0.8,
        },
        "miniapp_url": "",
        "pending_deletions": [],
        "last_post_time": 0,
    }
    if not os.path.exists(POSTER_SETTINGS_FILE):
        return default
    try:
        with open(POSTER_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                for k, v in default.items():
                    data.setdefault(k, v)
                return data
    except Exception as e:
        logger.error(f"[PosterBot] Failed to load poster_settings.json: {e}")
    return default


def save_poster_settings(data: Dict[str, Any]) -> None:
    """Save poster configuration settings to local storage."""
    try:
        os.makedirs(os.path.dirname(POSTER_SETTINGS_FILE), exist_ok=True)
        with open(POSTER_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[PosterBot] Failed to save poster_settings.json: {e}")


# ------------------------------------------------------------------------------
# 1. READ-ONLY MONGODB INTEGRATION
# ------------------------------------------------------------------------------

def _fetch_mongo_story_sync(mongo_uri: str, db_name: str, col_name: str, rot_idx: int) -> Tuple[Optional[Dict[str, Any]], int]:
    """
    Synchronously query MongoDB in STRICT READ-ONLY mode.
    Only count_documents({}) and find().skip().limit() are used.
    NO write, insert, update, or delete operations are ever performed.
    """
    if pymongo is None:
        logger.error("[PosterBot] pymongo library is missing. Install with 'pip install pymongo motor'")
        return None, 0

    if not mongo_uri:
        return None, 0

    client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        db = client[db_name]
        col = db[col_name]
        total = col.count_documents({})
        if total == 0:
            return None, 0

        actual_idx = rot_idx % total
        cursor = col.find().skip(actual_idx).limit(1)
        docs = list(cursor)
        story = docs[0] if docs else None
        return story, total
    except Exception as e:
        logger.error(f"[PosterBot] MongoDB Read-Only query error: {e}")
        return None, 0
    finally:
        client.close()


async def get_next_story_from_mongo(settings: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], int]:
    """Asynchronously fetch the next story from MongoDB in READ-ONLY mode."""
    mongo_uri = settings.get("mongo_uri") or os.getenv("MONGO_URI", "")
    db_name = settings.get("database_name") or os.getenv("DATABASE_NAME", "arya_premium")
    col_name = settings.get("collection_name", "premium_stories")
    rot_idx = settings.get("rot_idx", 0)

    story, total = await asyncio.to_thread(_fetch_mongo_story_sync, mongo_uri, db_name, col_name, rot_idx)
    return story, total


# ------------------------------------------------------------------------------
# 2. STORY FORMATTING & LAYOUT (Monospace & HTML Caption)
# ------------------------------------------------------------------------------

def to_monospace(text: str) -> str:
    """
    Convert alphanumeric text to Mathematical Monospace / Typewriter Unicode font.
    """
    if not text:
        return ""
    res = []
    for char in text:
        code = ord(char)
        if 0x41 <= code <= 0x5A:  # A-Z
            res.append(chr(0x1D670 + (code - 0x41)))
        elif 0x61 <= code <= 0x7A:  # a-z
            res.append(chr(0x1D68A + (code - 0x61)))
        elif 0x30 <= code <= 0x39:  # 0-9
            res.append(chr(0x1D7F6 + (code - 0x30)))
        else:
            res.append(char)
    return "".join(res)


def format_description(desc: str) -> str:
    """
    Format story description:
    If > 12 words: cleanly truncate (First 6 words + ' ... ' + Last 6 words).
    Convert all text to Mathematical Monospace.
    """
    if not desc:
        return to_monospace("No description available.")
    
    words = desc.strip().split()
    if len(words) > 12:
        truncated = " ".join(words[:6]) + " ... " + " ".join(words[-6:])
    else:
        truncated = " ".join(words)
        
    return to_monospace(truncated)


def build_post_caption(story: Dict[str, Any]) -> str:
    """
    Build HTML caption for story post matching exact specification format:
    
    ♨️ <b>Story :</b> <b>{story_name}</b>
    🔰 <b>Status :</b> <b>{status}</b>
    🖥 <b>Platform :</b> <b>{platform}</b>
    🧩 <b>Genre :</b> <b>{first_genre}</b>
    🎬 <b>Episodes :</b> <b>{episodes_str}</b>

       █▓▒▒░░░ᑭᖇIᑕE - ₹<b>{price}</b> ░░░▒▒▓█

    <b>Story Description :</b>
    <blockquote>{desc_monospace}</blockquote>
    """
    story_name = html.escape(str(story.get("name") or story.get("story_name") or story.get("title") or "Untitled Story"))
    status = html.escape(str(story.get("status") or "Completed"))
    platform = html.escape(str(story.get("platform") or "Audiobook"))

    genres = story.get("genre") or story.get("genres") or story.get("category") or ["Drama"]
    if isinstance(genres, list):
        first_genre = genres[0] if genres else "Drama"
    else:
        first_genre = str(genres).split(",")[0].strip() if genres else "Drama"
    first_genre = html.escape(str(first_genre))

    episodes = story.get("episodes") or story.get("total_episodes") or "Full Story"
    episodes_str = f"{episodes} Episodes" if str(episodes).isdigit() else str(episodes)
    episodes_str = html.escape(episodes_str)

    price = story.get("price", "Free")
    if price is None or price == "" or str(price) == "0":
        price = "Free"
    price = html.escape(str(price))

    raw_desc = str(story.get("description") or story.get("desc") or story.get("synopsis") or story.get("about") or "")
    desc_monospace = format_description(raw_desc)

    caption = (
        f"♨️ <b>Story :</b> <b>{story_name}</b>\n"
        f"🔰 <b>Status :</b> <b>{status}</b>\n"
        f"🖥 <b>Platform :</b> <b>{platform}</b>\n"
        f"🧩 <b>Genre :</b> <b>{first_genre}</b>\n"
        f"🎬 <b>Episodes :</b> <b>{episodes_str}</b>\n\n"
        f"   █▓▒▒░░░ᑭᖇIᑕE - ₹<b>{price}</b> ░░░▒▒▓█\n\n"
        f"<b>Story Description :</b>\n"
        f"<blockquote>{desc_monospace}</blockquote>"
    )
    return caption


# ------------------------------------------------------------------------------
# 3. PILLOW WATERMARK IMAGE PROCESSING
# ------------------------------------------------------------------------------

def apply_watermark_to_image(
    image_source: Any,
    wm_path: str,
    position: str = "bottom_right",
    opacity: float = 0.8
) -> io.BytesIO:
    """
    Resizes image & overlays PNG watermark with customizable position & opacity.
    Returns JPEG image in BytesIO buffer.
    """
    # 1. Load Base Image
    if isinstance(image_source, bytes):
        base_img = Image.open(io.BytesIO(image_source)).convert("RGBA")
    elif isinstance(image_source, str):
        if image_source.startswith("http://") or image_source.startswith("https://"):
            resp = requests.get(image_source, timeout=12)
            resp.raise_for_status()
            base_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        elif os.path.exists(image_source):
            base_img = Image.open(image_source).convert("RGBA")
        else:
            raise FileNotFoundError(f"Image not found: {image_source}")
    else:
        base_img = image_source.convert("RGBA")

    # 2. Check Watermark Path
    if not os.path.exists(wm_path):
        wm_path = "watermark.png"
    if not os.path.exists(wm_path):
        wm_path = os.path.join(DATA_DIR, "watermark.png")

    if not os.path.exists(wm_path):
        # Return clean base image if no watermark available
        out = io.BytesIO()
        base_img.convert("RGB").save(out, format="JPEG", quality=95)
        out.seek(0)
        return out

    # 3. Load & Resize Watermark
    wm_img = Image.open(wm_path).convert("RGBA")

    max_wm_w = int(base_img.width * 0.35)
    max_wm_h = int(base_img.height * 0.35)

    wm_w, wm_h = wm_img.size
    ratio = min(max_wm_w / wm_w if wm_w > max_wm_w else 1.0, max_wm_h / wm_h if wm_h > max_wm_h else 1.0)
    if ratio < 1.0:
        new_w = max(10, int(wm_w * ratio))
        new_h = max(10, int(wm_h * ratio))
        wm_img = wm_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        wm_w, wm_h = new_w, new_h

    # 4. Adjust Opacity
    opacity = max(0.1, min(1.0, float(opacity)))
    if opacity < 1.0:
        alpha = wm_img.split()[3].point(lambda p: int(p * opacity))
        wm_img.putalpha(alpha)

    # 5. Position Calculation
    margin = 20
    w_base, h_base = base_img.size

    pos_lower = (position or "bottom_right").lower()
    if pos_lower == "bottom_left":
        x, y = margin, h_base - wm_h - margin
    elif pos_lower == "top_right":
        x, y = w_base - wm_w - margin, margin
    elif pos_lower == "top_left":
        x, y = margin, margin
    elif pos_lower == "center":
        x, y = (w_base - wm_w) // 2, (h_base - wm_h) // 2
    else:  # default bottom_right
        x, y = w_base - wm_w - margin, h_base - wm_h - margin

    x = max(0, min(x, w_base - wm_w))
    y = max(0, min(y, h_base - wm_h))

    # 6. Overlay Watermark & Save
    base_img.paste(wm_img, (x, y), wm_img)

    out = io.BytesIO()
    base_img.convert("RGB").save(out, format="JPEG", quality=95)
    out.seek(0)
    return out


# ------------------------------------------------------------------------------
# 4. PUBLISHING ENGINE & AUTO-DELETE SCHEDULER
# ------------------------------------------------------------------------------

async def publish_next_story(app) -> Tuple[bool, str]:
    """Fetch next story from MongoDB, render post with watermark, and send to target channel."""
    settings = load_poster_settings()
    target_channel = settings.get("target_channel")
    if not target_channel:
        return False, "Target channel is not set."

    story, total = await get_next_story_from_mongo(settings)
    if not story or total == 0:
        return False, "No stories found in MongoDB collection 'premium_stories'."

    # Build Caption & Buttons
    caption = build_post_caption(story)

    # Build Inline Keyboard buttons (Row 1: Buy Now | Tutorial)
    story_id = str(story.get("_id", ""))
    buy_url = f"https://t.me/UseAryaBot/apminibyarya?startapp=story_{story_id}"
    tutorial_url = "https://t.me/StoriesFinderBot?start=guide"

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Buy Now", url=buy_url),
            InlineKeyboardButton("Tutorial", url=tutorial_url)
        ]
    ])

    # Handle Photo & Watermark
    cover_url = story.get("cover") or story.get("image") or story.get("poster") or story.get("photo") or story.get("thumb") or ""
    wm_settings = settings.get("watermark", {})
    wm_enabled = wm_settings.get("enabled", True)
    wm_path = wm_settings.get("path", "watermark.png")
    wm_pos = wm_settings.get("position", "bottom_right")
    wm_opacity = float(wm_settings.get("opacity", 0.8))

    photo_bytes = None
    if cover_url:
        try:
            if wm_enabled:
                photo_bytes = await asyncio.to_thread(apply_watermark_to_image, cover_url, wm_path, wm_pos, wm_opacity)
            else:
                resp = await asyncio.to_thread(requests.get, cover_url, timeout=12)
                if resp.status_code == 200:
                    photo_bytes = io.BytesIO(resp.content)
        except Exception as e:
            logger.warning(f"[PosterBot] Failed to process image '{cover_url}': {e}")

    # Fallback to default cover if photo_bytes is None
    if not photo_bytes:
        default_cover = "default_cover.png"
        if not os.path.exists(default_cover):
            default_cover = os.path.join(DATA_DIR, "default_cover.png")
        if os.path.exists(default_cover):
            if wm_enabled:
                photo_bytes = await asyncio.to_thread(apply_watermark_to_image, default_cover, wm_path, wm_pos, wm_opacity)
            else:
                with open(default_cover, "rb") as f:
                    photo_bytes = io.BytesIO(f.read())

    # Send message to channel
    target_chat = int(target_channel) if (target_channel.startswith("-") or target_channel.isdigit()) else target_channel
    try:
        if photo_bytes:
            sent_msg = await app.bot.send_photo(
                chat_id=target_chat,
                photo=photo_bytes,
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            sent_msg = await app.bot.send_message(
                chat_id=target_chat,
                text=caption,
                parse_mode="HTML",
                reply_markup=markup
            )

        # Advance Rotation Index & Save Last Post Time
        next_rot = (settings.get("rot_idx", 0) + 1) % max(1, total)
        settings["rot_idx"] = next_rot
        settings["last_post_time"] = time.time()

        # Handle Auto-Delete Delay
        delete_delay_hours = float(settings.get("delete_delay_hours", 0))
        if delete_delay_hours > 0:
            delete_at = time.time() + (delete_delay_hours * 3600)
            pending = settings.get("pending_deletions", [])
            pending.append({
                "chat_id": sent_msg.chat_id,
                "message_id": sent_msg.message_id,
                "delete_at": delete_at
            })
            settings["pending_deletions"] = pending

        save_poster_settings(settings)
        story_name = story.get("name") or story.get("story_name") or "Story"
        return True, f"Story '{story_name}' posted to {target_channel}!"

    except Exception as e:
        logger.error(f"[PosterBot] Failed to post message to {target_channel}: {e}")
        return False, f"Telegram API error: {e}"


async def process_pending_deletions(app, settings: Dict[str, Any]) -> None:
    """Check pending post deletions and delete expired channel posts."""
    pending = settings.get("pending_deletions", [])
    if not pending:
        return

    now = time.time()
    remaining = []
    changed = False

    for item in pending:
        if now >= item.get("delete_at", 0):
            try:
                await app.bot.delete_message(chat_id=item["chat_id"], message_id=item["message_id"])
                logger.info(f"[PosterBot] Auto-deleted channel message {item['message_id']} in chat {item['chat_id']}")
            except Exception as e:
                logger.warning(f"[PosterBot] Could not delete channel message {item.get('message_id')}: {e}")
            changed = True
        else:
            remaining.append(item)

    if changed:
        settings["pending_deletions"] = remaining
        save_poster_settings(settings)


async def poster_bot_worker_loop(app):
    """24/7 Async task loop for Poster Bot background posting and auto-deletion."""
    logger.info("[PosterBot] Worker loop started.")
    while True:
        try:
            settings = load_poster_settings()
            # 1. Auto-deletion processing
            await process_pending_deletions(app, settings)

            # 2. Auto-posting processing
            if settings.get("active", False) and settings.get("target_channel"):
                interval_min = float(settings.get("interval_minutes", 30))
                interval_sec = interval_min * 60

                last_post = float(settings.get("last_post_time", 0))
                now = time.time()

                if (now - last_post) >= interval_sec:
                    logger.info("[PosterBot] Post interval reached. Publishing next story...")
                    success, msg = await publish_next_story(app)
                    if success:
                        logger.info(f"[PosterBot] Auto-post success: {msg}")
                    else:
                        logger.warning(f"[PosterBot] Auto-post skipped: {msg}")
        except Exception as e:
            logger.error(f"[PosterBot] Error in background worker loop: {e}")

        await asyncio.sleep(15)  # Check every 15 seconds


# ------------------------------------------------------------------------------
# 5. ADMIN PANEL & CALLBACK HANDLERS
# ------------------------------------------------------------------------------

def _is_authorized_admin(user_id: int) -> bool:
    """Check if user is bot owner or admin."""
    from stories_bot import is_admin
    return is_admin(user_id) or user_id == OWNER_ID or user_id == ADMIN_ID


def build_poster_panel_markup(settings: Dict[str, Any], caller_id: int) -> tuple:
    """Build Poster Bot Admin Panel UI markup & summary text."""
    active = settings.get("active", False)
    status_str = "🟢 Active" if active else "🔴 Paused"
    target_channel = settings.get("target_channel") or "Not Set"
    interval_min = settings.get("interval_minutes", 30)

    # Interval representation
    if interval_min >= 60:
        interval_str = f"{interval_min // 60} hour(s)" if interval_min % 60 == 0 else f"{interval_min} mins"
    else:
        interval_str = f"{interval_min} mins"

    # Delete delay representation
    del_hours = settings.get("delete_delay_hours", 0)
    if del_hours == 0:
        delete_str = "Never (Disabled)"
    elif del_hours >= 24:
        delete_str = f"{del_hours // 24} day(s)"
    else:
        delete_str = f"{del_hours} hour(s)"

    # Watermark info
    wm = settings.get("watermark", {})
    wm_active = "🟢 ON" if wm.get("enabled", True) else "🔴 OFF"
    wm_pos = wm.get("position", "bottom_right")
    wm_op = wm.get("opacity", 0.8)

    # Mongo info
    mongo_uri = settings.get("mongo_uri") or os.getenv("MONGO_URI", "")
    db_name = settings.get("database_name") or os.getenv("DATABASE_NAME", "arya_premium")
    mongo_status = "🟢 Connected (Read-Only)" if mongo_uri else "🔴 Not Configured"

    rot_idx = settings.get("rot_idx", 0)
    last_post = float(settings.get("last_post_time", 0))

    next_post_str = "Paused"
    if active:
        next_due = last_post + (float(interval_min) * 60)
        remaining = int(next_due - time.time())
        if remaining <= 0:
            next_post_str = "Due now"
        else:
            m, s = divmod(remaining, 60)
            h, m = divmod(m, 60)
            if h > 0:
                next_post_str = f"In {h}h {m}m"
            else:
                next_post_str = f"In {m}m {s}s"

    text = (
        "🤖 <b>Poster Bot Control Panel</b>\n"
        "<i>Standalone 24/7 Automated Story Publisher (MongoDB Read-Only)</i>\n"
        "───────────────────────────────\n\n"
        f"▸ <b>Status:</b> <b>{status_str}</b>\n"
        f"▸ <b>Target Channel:</b> <code>{html.escape(str(target_channel))}</code>\n"
        f"▸ <b>Post Interval:</b> <code>{interval_str}</code>\n"
        f"▸ <b>Auto-Delete Delay:</b> <code>{delete_str}</code>\n"
        f"▸ <b>Watermark:</b> <code>{wm_active}</code> (Pos: <code>{wm_pos}</code>, Op: <code>{wm_op}</code>)\n"
        f"▸ <b>MongoDB (Read-Only):</b> <code>{mongo_status}</code>\n"
        f"▸ <b>Rotation Index:</b> <code>{rot_idx}</code> | <b>Next Post:</b> <code>{next_post_str}</code>\n"
    )

    toggle_btn_text = "🔴 Pause Auto-Posting" if active else "🟢 Activate Auto-Posting"

    keyboard = [
        [
            InlineKeyboardButton(toggle_btn_text, callback_data=f"cfg|poster|toggle||{caller_id}"),
            InlineKeyboardButton("⚡ POST NOW", callback_data=f"cfg|poster|postnow||{caller_id}"),
        ],
        [
            InlineKeyboardButton("📢 Target Channel", callback_data=f"cfg|poster|ch_prompt||{caller_id}"),
            InlineKeyboardButton("⏱ Post Interval", callback_data=f"cfg|poster|int_menu||{caller_id}"),
        ],
        [
            InlineKeyboardButton("🗑 Auto-Delete Delay", callback_data=f"cfg|poster|del_menu||{caller_id}"),
            InlineKeyboardButton("🎨 Watermark Manager", callback_data=f"cfg|poster|wm_menu||{caller_id}"),
        ],
        [
            InlineKeyboardButton("🍃 MongoDB Settings", callback_data=f"cfg|poster|mongo_menu||{caller_id}"),
            InlineKeyboardButton("🔗 MiniApp / Link", callback_data=f"cfg|poster|link_prompt||{caller_id}"),
        ],
        [
            InlineKeyboardButton("📊 Summary", callback_data=f"cfg|poster|summary||{caller_id}"),
            InlineKeyboardButton("« Main Menu", callback_data=f"cfg|main||{caller_id}"),
        ],
    ]

    return text, InlineKeyboardMarkup(keyboard)


async def _safe_edit_message_text(query, text: str, parse_mode: str = "HTML", reply_markup=None):
    """Safely edit message text ignoring BadRequest 'Message is not modified' error."""
    try:
        await query.edit_message_text(text=text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"[PosterBot] Edit message error: {e}")


async def handle_poster_callbacks(query, context: ContextTypes.DEFAULT_TYPE, parts: list, caller_id: int):
    """Handle all Poster Bot callback query interactions."""
    user = query.from_user
    if not _is_authorized_admin(user.id):
        await query.answer("⛔ Admin only.", show_alert=True)
        return

    action = parts[2] if len(parts) > 2 else "main"
    sub_action = parts[3] if len(parts) > 3 else ""

    settings = load_poster_settings()

    # 1. Main Menu
    if action in ("main", "back"):
        text, markup = build_poster_panel_markup(settings, caller_id)
        await _safe_edit_message_text(query, text=text, parse_mode="HTML", reply_markup=markup)
        return

    # 2. Toggle Status (ON/OFF)
    if action == "toggle":
        curr = settings.get("active", False)
        settings["active"] = not curr
        save_poster_settings(settings)
        status_word = "Activated 🟢" if settings["active"] else "Paused 🔴"
        await query.answer(f"Poster Bot is now {status_word}!")
        text, markup = build_poster_panel_markup(settings, caller_id)
        await _safe_edit_message_text(query, text=text, parse_mode="HTML", reply_markup=markup)
        return

    # 3. Post Now (Manual Trigger)
    if action == "postnow":
        await query.answer("⏳ Publishing story to channel...")
        success, msg = await publish_next_story(context.application)
        if success:
            await query.answer(f"✅ {msg}", show_alert=True)
        else:
            await query.answer(f"❌ Post failed: {msg}", show_alert=True)
        text, markup = build_poster_panel_markup(settings, caller_id)
        await _safe_edit_message_text(query, text=text, parse_mode="HTML", reply_markup=markup)
        return

    # 4. Target Channel Prompt
    if action == "ch_prompt":
        context.user_data["poster_waiting"] = "target_channel"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"cfg|poster|main||{caller_id}")]])
        await _safe_edit_message_text(
            query,
            "📢 <b>Set Target Channel</b>\n\n"
            "Please send the Telegram Channel ID or Username in chat.\n"
            "<i>Examples: <code>@my_stories_channel</code> or <code>-1001234567890</code></i>\n\n"
            "<i>(Make sure the bot is added as an Administrator in your channel!)</i>",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    # 5. Post Interval Menu & Setters
    if action == "int_menu":
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("2 mins", callback_data=f"cfg|poster|set_int|2|{caller_id}"),
                InlineKeyboardButton("5 mins", callback_data=f"cfg|poster|set_int|5|{caller_id}"),
                InlineKeyboardButton("10 mins", callback_data=f"cfg|poster|set_int|10|{caller_id}"),
            ],
            [
                InlineKeyboardButton("30 mins", callback_data=f"cfg|poster|set_int|30|{caller_id}"),
                InlineKeyboardButton("1 Hour", callback_data=f"cfg|poster|set_int|60|{caller_id}"),
                InlineKeyboardButton("2 Hours", callback_data=f"cfg|poster|set_int|120|{caller_id}"),
            ],
            [
                InlineKeyboardButton("6 Hours", callback_data=f"cfg|poster|set_int|360|{caller_id}"),
                InlineKeyboardButton("12 Hours", callback_data=f"cfg|poster|set_int|720|{caller_id}"),
                InlineKeyboardButton("24 Hours", callback_data=f"cfg|poster|set_int|1440|{caller_id}"),
            ],
            [
                InlineKeyboardButton("✏ Custom Interval (mins)", callback_data=f"cfg|poster|custom_int||{caller_id}"),
            ],
            [
                InlineKeyboardButton("« Back", callback_data=f"cfg|poster|main||{caller_id}"),
            ],
        ])
        cur = settings.get("interval_minutes", 30)
        await _safe_edit_message_text(
            query,
            f"⏱ <b>Set Post Interval</b>\n"
            f"Current interval: <b>{cur} minutes</b>\n\n"
            f"Select a preset post interval or set custom minutes:",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    if action == "set_int":
        try:
            mins = int(sub_action)
            settings["interval_minutes"] = mins
            save_poster_settings(settings)
            await query.answer(f"✅ Post interval set to {mins} minutes!")
        except Exception as e:
            await query.answer(f"Invalid interval: {e}", show_alert=True)
        text, markup = build_poster_panel_markup(settings, caller_id)
        await _safe_edit_message_text(query, text=text, parse_mode="HTML", reply_markup=markup)
        return

    if action == "custom_int":
        context.user_data["poster_waiting"] = "custom_interval"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"cfg|poster|main||{caller_id}")]])
        await _safe_edit_message_text(
            query,
            "✏ <b>Set Custom Post Interval</b>\n\n"
            "Please send the post interval in <b>minutes</b> (e.g. <code>45</code> or <code>180</code>).",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    # 6. Auto-Delete Delay Menu & Setters
    if action == "del_menu":
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Never (Disabled)", callback_data=f"cfg|poster|set_del|0|{caller_id}"),
                InlineKeyboardButton("12 Hours", callback_data=f"cfg|poster|set_del|12|{caller_id}"),
            ],
            [
                InlineKeyboardButton("24 Hours (1d)", callback_data=f"cfg|poster|set_del|24|{caller_id}"),
                InlineKeyboardButton("48 Hours (2d)", callback_data=f"cfg|poster|set_del|48|{caller_id}"),
            ],
            [
                InlineKeyboardButton("72 Hours (3d)", callback_data=f"cfg|poster|set_del|72|{caller_id}"),
                InlineKeyboardButton("7 Days (168h)", callback_data=f"cfg|poster|set_del|168|{caller_id}"),
            ],
            [
                InlineKeyboardButton("« Back", callback_data=f"cfg|poster|main||{caller_id}"),
            ],
        ])
        cur_h = settings.get("delete_delay_hours", 0)
        await _safe_edit_message_text(
            query,
            f"🗑 <b>Set Auto-Delete Delay</b>\n"
            f"Current delete delay: <b>{cur_h} hours</b>\n\n"
            f"Select when published story posts should be automatically deleted from the channel:",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    if action == "set_del":
        try:
            hours = float(sub_action)
            settings["delete_delay_hours"] = hours
            save_poster_settings(settings)
            txt = "Disabled (Never)" if hours == 0 else f"{hours} hours"
            await query.answer(f"✅ Auto-delete delay set to {txt}!")
        except Exception as e:
            await query.answer(f"Invalid delay: {e}", show_alert=True)
        text, markup = build_poster_panel_markup(settings, caller_id)
        await _safe_edit_message_text(query, text=text, parse_mode="HTML", reply_markup=markup)
        return

    # 7. Watermark Manager Panel
    if action == "wm_menu":
        wm = settings.get("watermark", {})
        wm_enabled = wm.get("enabled", True)
        pos = wm.get("position", "bottom_right")
        op = wm.get("opacity", 0.8)

        toggle_txt = "🔴 Disable Watermark" if wm_enabled else "🟢 Enable Watermark"

        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(toggle_txt, callback_data=f"cfg|poster|wm_toggle||{caller_id}"),
                InlineKeyboardButton("🖼 Upload New PNG", callback_data=f"cfg|poster|wm_upload||{caller_id}"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if pos=='bottom_right' else ''}↘ Bottom Right", callback_data=f"cfg|poster|wm_pos|bottom_right|{caller_id}"),
                InlineKeyboardButton(f"{'✅ ' if pos=='bottom_left' else ''}↙ Bottom Left", callback_data=f"cfg|poster|wm_pos|bottom_left|{caller_id}"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if pos=='top_right' else ''}↗ Top Right", callback_data=f"cfg|poster|wm_pos|top_right|{caller_id}"),
                InlineKeyboardButton(f"{'✅ ' if pos=='top_left' else ''}↖ Top Left", callback_data=f"cfg|poster|wm_pos|top_left|{caller_id}"),
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if pos=='center' else ''}⏺ Center", callback_data=f"cfg|poster|wm_pos|center|{caller_id}"),
            ],
            [
                InlineKeyboardButton("Opacity: 0.2", callback_data=f"cfg|poster|wm_op|0.2|{caller_id}"),
                InlineKeyboardButton("0.4", callback_data=f"cfg|poster|wm_op|0.4|{caller_id}"),
                InlineKeyboardButton("0.6", callback_data=f"cfg|poster|wm_op|0.6|{caller_id}"),
                InlineKeyboardButton("0.8", callback_data=f"cfg|poster|wm_op|0.8|{caller_id}"),
                InlineKeyboardButton("1.0", callback_data=f"cfg|poster|wm_op|1.0|{caller_id}"),
            ],
            [
                InlineKeyboardButton("« Back", callback_data=f"cfg|poster|main||{caller_id}"),
            ],
        ])
        await _safe_edit_message_text(
            query,
            "🎨 <b>Watermark Manager</b>\n\n"
            f"▸ <b>Status:</b> <code>{'🟢 Enabled' if wm_enabled else '🔴 Disabled'}</code>\n"
            f"▸ <b>Position:</b> <code>{pos}</code>\n"
            f"▸ <b>Opacity:</b> <code>{op}</code>\n"
            f"▸ <b>Path:</b> <code>{html.escape(str(wm.get('path', 'watermark.png')))}</code>\n\n"
            "<i>Choose position, opacity, toggle state or upload a PNG file below:</i>",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    if action == "wm_toggle":
        wm = settings.get("watermark", {})
        wm["enabled"] = not wm.get("enabled", True)
        settings["watermark"] = wm
        save_poster_settings(settings)
        await query.answer(f"Watermark {'Enabled 🟢' if wm['enabled'] else 'Disabled 🔴'}")
        parts[2] = "wm_menu"
        await handle_poster_callbacks(query, context, parts, caller_id)
        return

    if action == "wm_pos":
        wm = settings.get("watermark", {})
        wm["position"] = sub_action
        settings["watermark"] = wm
        save_poster_settings(settings)
        await query.answer(f"Watermark position set to: {sub_action}")
        parts[2] = "wm_menu"
        await handle_poster_callbacks(query, context, parts, caller_id)
        return

    if action == "wm_op":
        wm = settings.get("watermark", {})
        wm["opacity"] = float(sub_action)
        settings["watermark"] = wm
        save_poster_settings(settings)
        await query.answer(f"Watermark opacity set to: {sub_action}")
        parts[2] = "wm_menu"
        await handle_poster_callbacks(query, context, parts, caller_id)
        return

    if action == "wm_upload":
        context.user_data["poster_waiting"] = "watermark_png"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"cfg|poster|wm_menu||{caller_id}")]])
        await _safe_edit_message_text(
            query,
            "🖼 <b>Upload Watermark PNG File</b>\n\n"
            "Please send/upload the <b>PNG watermark image</b> in DM.\n"
            "<i>Transparent PNG images work best for watermarking.</i>",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    # 8. MongoDB Connection Settings Menu
    if action == "mongo_menu":
        m_uri = settings.get("mongo_uri") or os.getenv("MONGO_URI", "")
        db_n = settings.get("database_name") or os.getenv("DATABASE_NAME", "arya_premium")
        col_n = settings.get("collection_name", "premium_stories")

        masked_uri = (m_uri[:15] + "..." + m_uri[-10:]) if len(m_uri) > 25 else (m_uri or "Not Set")

        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✏ Edit MONGO_URI", callback_data=f"cfg|poster|mongo_uri_prompt||{caller_id}"),
            ],
            [
                InlineKeyboardButton("✏ Edit DATABASE_NAME", callback_data=f"cfg|poster|mongo_db_prompt||{caller_id}"),
            ],
            [
                InlineKeyboardButton("« Back", callback_data=f"cfg|poster|main||{caller_id}"),
            ],
        ])
        await _safe_edit_message_text(
            query,
            "🍃 <b>MongoDB Connection Settings (READ-ONLY)</b>\n\n"
            f"▸ <b>Mongo URI:</b> <code>{html.escape(masked_uri)}</code>\n"
            f"▸ <b>Database Name:</b> <code>{html.escape(db_n)}</code>\n"
            f"▸ <b>Collection:</b> <code>{html.escape(col_n)}</code>\n\n"
            "🔒 <i>Strict Read-Only Mode: Only count_documents and find operations are executed. No writes will ever occur on MongoDB.</i>",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    if action == "mongo_uri_prompt":
        context.user_data["poster_waiting"] = "mongo_uri"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"cfg|poster|mongo_menu||{caller_id}")]])
        await _safe_edit_message_text(
            query,
            "🍃 <b>Set MongoDB Connection URI</b>\n\n"
            "Please send your MongoDB connection string (<code>MONGO_URI</code>) in chat.\n"
            "<i>Example: <code>mongodb+srv://user:password@cluster.mongodb.net</code></i>",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    if action == "mongo_db_prompt":
        context.user_data["poster_waiting"] = "database_name"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"cfg|poster|mongo_menu||{caller_id}")]])
        await _safe_edit_message_text(
            query,
            "🍃 <b>Set Database Name</b>\n\n"
            "Please send your MongoDB database name (e.g. <code>arya_premium</code>).",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    # 9. MiniApp / Bot Link Prompt
    if action == "link_prompt":
        context.user_data["poster_waiting"] = "miniapp_url"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"cfg|poster|main||{caller_id}")]])
        cur_url = settings.get("miniapp_url") or "Auto / Default"
        await _safe_edit_message_text(
            query,
            "🔗 <b>Set MiniApp / Deep-Link URL</b>\n\n"
            f"Current Link: <code>{html.escape(str(cur_url))}</code>\n\n"
            "Send the custom Telegram MiniApp URL or Bot link for the inline button.\n"
            "<i>(Or send <code>auto</code> to auto-generate link per story).</i>",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    # 10. Summary Alert
    if action == "summary":
        _, total_stories = await get_next_story_from_mongo(settings)
        ch = settings.get("target_channel") or "Not Set"
        interval = settings.get("interval_minutes", 30)
        del_h = settings.get("delete_delay_hours", 0)
        rot_idx = settings.get("rot_idx", 0)
        active_str = "🟢 Active" if settings.get("active") else "🔴 Paused"

        summary_text = (
            f"📊 Poster Bot Summary:\n\n"
            f"• Status: {active_str}\n"
            f"• Channel: {ch}\n"
            f"• Interval: {interval} mins\n"
            f"• Auto-Delete: {del_h}h\n"
            f"• MongoDB Total Stories: {total_stories}\n"
            f"• Rotation Index: {rot_idx}\n"
            f"• Pending Deletions: {len(settings.get('pending_deletions', []))}"
        )
        await query.answer(summary_text, show_alert=True)
        return


# ------------------------------------------------------------------------------
# 6. TEXT / DOCUMENT INPUT HANDLER FOR ADMIN CONFIGS
# ------------------------------------------------------------------------------

async def poster_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles text or document messages when waiting for admin poster inputs.
    Returns True if handled, False otherwise.
    """
    user = update.effective_user
    if not user or not _is_authorized_admin(user.id):
        return False

    waiting_for = context.user_data.get("poster_waiting")
    if not waiting_for:
        return False

    settings = load_poster_settings()
    message = update.effective_message

    # Handle Target Channel input
    if waiting_for == "target_channel":
        val = message.text.strip() if message.text else ""
        if val:
            settings["target_channel"] = val
            save_poster_settings(settings)
            context.user_data.pop("poster_waiting", None)
            await message.reply_text(f"✅ Target channel set to: <code>{html.escape(val)}</code>", parse_mode="HTML")
            text, markup = build_poster_panel_markup(settings, user.id)
            await message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)
            return True

    # Handle Custom Interval input
    if waiting_for == "custom_interval":
        val = message.text.strip() if message.text else ""
        if val.isdigit() and int(val) > 0:
            mins = int(val)
            settings["interval_minutes"] = mins
            save_poster_settings(settings)
            context.user_data.pop("poster_waiting", None)
            await message.reply_text(f"✅ Post interval set to: <b>{mins} minutes</b>", parse_mode="HTML")
            text, markup = build_poster_panel_markup(settings, user.id)
            await message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)
            return True
        else:
            await message.reply_text("❌ Please send a valid positive number of minutes (e.g. 45).")
            return True

    # Handle Watermark PNG Upload
    if waiting_for == "watermark_png":
        file_obj = None
        if message.photo:
            file_obj = message.photo[-1]
        elif message.document and (message.document.mime_type or "").startswith("image/"):
            file_obj = message.document

        if file_obj:
            tg_file = await file_obj.get_file()
            save_path = os.path.join(DATA_DIR, "poster_watermark.png")
            await tg_file.download_to_drive(save_path)

            wm = settings.get("watermark", {})
            wm["path"] = save_path
            wm["enabled"] = True
            settings["watermark"] = wm
            save_poster_settings(settings)

            context.user_data.pop("poster_waiting", None)
            await message.reply_text("✅ Watermark PNG image updated & enabled successfully!")
            text, markup = build_poster_panel_markup(settings, user.id)
            await message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)
            return True
        else:
            await message.reply_text("❌ Please send a valid image/photo or PNG document.")
            return True

    # Handle MongoDB URI input
    if waiting_for == "mongo_uri":
        val = message.text.strip() if message.text else ""
        if val:
            settings["mongo_uri"] = val
            save_poster_settings(settings)
            context.user_data.pop("poster_waiting", None)
            await message.reply_text("✅ MongoDB URI updated!")
            text, markup = build_poster_panel_markup(settings, user.id)
            await message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)
            return True

    # Handle Database Name input
    if waiting_for == "database_name":
        val = message.text.strip() if message.text else ""
        if val:
            settings["database_name"] = val
            save_poster_settings(settings)
            context.user_data.pop("poster_waiting", None)
            await message.reply_text(f"✅ Database Name set to: <code>{html.escape(val)}</code>", parse_mode="HTML")
            text, markup = build_poster_panel_markup(settings, user.id)
            await message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)
            return True

    # Handle MiniApp URL input
    if waiting_for == "miniapp_url":
        val = message.text.strip() if message.text else ""
        if val:
            settings["miniapp_url"] = "" if val.lower() == "auto" else val
            save_poster_settings(settings)
            context.user_data.pop("poster_waiting", None)
            await message.reply_text("✅ MiniApp / Deep-Link URL updated!")
            text, markup = build_poster_panel_markup(settings, user.id)
            await message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)
            return True

    return False


# ------------------------------------------------------------------------------
# 7. COMMAND HANDLER (/poster)
# ------------------------------------------------------------------------------

async def poster_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /poster or /posterbot: Opens Poster Bot control panel."""
    user = update.effective_user
    if not _is_authorized_admin(user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    settings = load_poster_settings()
    text, markup = build_poster_panel_markup(settings, user.id)
    await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=markup)
