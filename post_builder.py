import html
import asyncio
import io
import logging
import urllib.request

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
    try:
        pytesseract.get_tesseract_version()
        OCR_AVAILABLE = True
    except Exception:
        OCR_AVAILABLE = False
except ImportError:
    OCR_AVAILABLE = False

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InputMediaDocument,
    InputMediaPhoto,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

_log = logging.getLogger(__name__)

# ── States ─────────────────────────────────────────────────────────────────────
(
    STATE_MODE,
    STATE_EDIT_LINK,
    STATE_FORMAT,
    STATE_NAME,
    STATE_DESC_MODE,
    STATE_DESC_ENTER,
    STATE_DESC_OCR,
    STATE_DESC_CHOICE,
    STATE_IMG_UPLOAD,
    STATE_PLATFORM,
    STATE_GENRE,
    STATE_LINK,
    STATE_BACKUP_LINK,
    STATE_EPISODES,
    STATE_STATUS,
    STATE_USERNAME,
    STATE_DEST_TYPE,
    STATE_DEST_INPUT,
    STATE_DEST_TOPIC,
    STATE_CONFIRM,
    STATE_IS_PAID,
) = range(21)

_LIGHT_GENRES = ["Fantasy", "Suspense", "Romance", "Thriller", "Action", "Horror", "Mystery"]

# ── Constants ──────────────────────────────────────────────────────────────────
DEFAULT_JOIN_USERNAME = "@StoriesByJeetXNew"
DEFAULT_STORY_LINK    = "https://t.me/StoriesByJeetXNew"
DEFAULT_PLATFORM      = "Pocket FM"
DEFAULT_FORMAT_1_EMOJI     = "🫠"
DEFAULT_FORMAT_1_JOIN_EMOJI = "🦊"

# ── Helpers ────────────────────────────────────────────────────────────────────
def _kb(rows, one_time=True):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=one_time)

def to_small_caps(text):
    mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ',
        'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
        'q': 'Q', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
        'y': 'ʏ', 'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ',
        'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ',
        'Q': 'Q', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x',
        'Y': 'ʏ', 'Z': 'ᴢ'
    }
    return "".join(mapping.get(c, c) for c in text)

def to_bold_unicode(text):
    """Convert ASCII letters to Unicode Mathematical Bold equivalents."""
    result = []
    for c in text:
        if 'A' <= c <= 'Z':
            result.append(chr(0x1D400 + ord(c) - ord('A')))
        elif 'a' <= c <= 'z':
            result.append(chr(0x1D41A + ord(c) - ord('a')))
        elif '0' <= c <= '9':
            result.append(chr(0x1D7CE + ord(c) - ord('0')))
        else:
            result.append(c)
    return "".join(result)

def _is_admin(user_id):
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

def _load_destinations():
    try:
        from database import load_config
        import config
        cfg = load_config()
        channels = [str(c) for c in cfg.get("post_channels", []) if c]
        groups = list(cfg.get("post_groups", []))

        # Default fallback channel
        ch_id = getattr(config, "CHANNEL_ID", None)
        if ch_id and str(ch_id) not in channels:
            channels.insert(0, str(ch_id))

        # Default fallback group
        grp_id = getattr(config, "GROUP_ID", None)
        if grp_id:
            g_str = str(grp_id)
            if not any(isinstance(g, dict) and g.get("id") == g_str for g in groups):
                groups.insert(0, {"id": g_str, "topic_id": None, "name": "Main Group"})

        return channels, groups
    except Exception:
        return [], []

def _save_channel(dest):
    try:
        from database import load_config, save_config
        cfg = load_config()
        channels = cfg.setdefault("post_channels", [])
        dest_str = str(dest).strip()
        if dest_str and dest_str not in channels:
            channels.append(dest_str)
            save_config(cfg)
    except Exception as e:
        _log.warning(f"[PB] _save_channel error: {e}")

def _save_group(group_id, topic_id=None, name=None):
    try:
        from database import load_config, save_config
        cfg = load_config()
        groups = cfg.setdefault("post_groups", [])
        entry = {"id": str(group_id), "topic_id": topic_id, "name": name or str(group_id)}
        existing = [g for g in groups if isinstance(g, dict) and g.get("id") == str(group_id)]
        if not existing:
            groups.append(entry)
            save_config(cfg)
    except Exception as e:
        _log.warning(f"[PB] _save_group error: {e}")

# ── Format builders ────────────────────────────────────────────────────────────
def build_format_1(data):
    name     = data.get("name", "Unknown")
    status   = data.get("status", "Ongoing")
    genre    = data.get("genre", "Unknown")
    link     = data.get("link", "")
    username = data.get("username", DEFAULT_JOIN_USERNAME)
    t  = f"{DEFAULT_FORMAT_1_EMOJI} <b>Name :-</b>  <b>{html.escape(name)}</b> ( <b>{status}</b> )\n\n"
    t += f"<b>Story Type :-</b> {html.escape(genre)}\n\n"
    t += f"<b>Link :-</b> {link}\n\n"
    t += f"{DEFAULT_FORMAT_1_JOIN_EMOJI}<b>JOIN FOR ALL EPISODES.</b>\n\nJoin - {username}"
    try:
        from stories_bot import inject_premium_emojis
        return inject_premium_emojis(t)
    except Exception:
        return t

def build_format_2(data):
    name     = data.get("name", "Unknown")
    platform = data.get("platform", DEFAULT_PLATFORM)
    desc     = data.get("desc", "")
    episodes = data.get("episodes", "")
    status   = data.get("status", "Ongoing")
    link     = data.get("link", "")
    avail    = "All Stories Available on Stories🫶."
    desc_block = f"<blockquote expandable><i>{html.escape(desc)}</i></blockquote>\n\n" if desc else ""
    t  = f"<b>{html.escape(name)} • {html.escape(platform)}</b>\n\n"
    t += desc_block
    if episodes:
        t += f"<b>Episodes -</b> <b>{episodes}</b>\n"
    t += f"<b>Status -</b> <b>{status}</b>\n\n"
    t += f"{html.escape(avail)}\n\n{link}\n{link}"
    try:
        from stories_bot import inject_premium_emojis
        return inject_premium_emojis(t)
    except Exception:
        return t

def build_light_format(data):
    name        = data.get("name", "Unknown")
    status      = data.get("status", "Ongoing")
    platform    = data.get("platform", DEFAULT_PLATFORM)
    genre       = data.get("genre", "Unknown")
    desc        = data.get("desc", "")

    t  = f'<tg-emoji emoji-id="5465432711218863135">♨️</tg-emoji><b>Story</b> : <b>{html.escape(name)}</b>\n'
    t += f'<tg-emoji emoji-id="6019118553326689234">🔰</tg-emoji><b>Status</b> : <b>{html.escape(status)}</b>\n'
    t += f'<tg-emoji emoji-id="6019455905827920171">🖥</tg-emoji><b>Platform</b> : <b>{html.escape(platform)}</b>\n'
    t += f'<tg-emoji emoji-id="6024065724291488135">🧩</tg-emoji><b>Genre</b> : <b>{html.escape(genre)}</b>'

    if desc:
        bold_desc = to_bold_unicode(desc)
        # No blank line between genre and description header
        t += f'\n<tg-emoji emoji-id="6021487472603568286">📝</tg-emoji><b>Story Description :-</b>\n<blockquote expandable>{html.escape(bold_desc)}</blockquote>'

    try:
        from stories_bot import inject_premium_emojis
        return inject_premium_emojis(t)
    except Exception:
        return t

def get_light_kb(data, is_alt=False):
    link        = data.get("link", "")
    backup_link = data.get("backup_link") or link   # fallback to main link
    if not link:
        return None
    
    is_paid = data.get("is_paid", False)
    if is_paid:
        second_btn_text = "ᴛᴜᴛᴏʀɪᴀʟ"
        try:
            from database import load_config
            cfg = load_config()
            tut_cfg = cfg.get("tutorial_guide", {})
            ext_url = tut_cfg.get("external_url", "").strip()
            bot_uname = data.get("bot_username", "") or cfg.get("bot_username", "StoriesFinderBot")
            second_btn_url = ext_url if ext_url else f"https://t.me/{bot_uname}?start=guide"
        except Exception:
            second_btn_url = backup_link or "https://t.me/StoriesFinderBot?start=guide"
    else:
        second_btn_text = "ʙᴀᴄᴋᴜᴘ"
        second_btn_url = backup_link or "https://t.me/StoriesFinderBot?start=guide"
    
    if is_alt:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Mᴏʀᴇ ‽", url="https://t.me/StoriesLightX"),
            InlineKeyboardButton(second_btn_text,   url=second_btn_url),
        ]])
    
    play_text = "ʙᴜʏ ɴᴏᴡ" if is_paid else "ᴘʟᴀʏ ɴᴏᴡ"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(play_text, url=link),
        InlineKeyboardButton(second_btn_text,   url=second_btn_url),
    ]])

# ── Light Pro helpers ──────────────────────────────────────────────────────────
def build_episode_line(episodes_raw, status):
    """Build episode display string with optional progress bar for Ongoing."""
    try:
        current = int(str(episodes_raw).split("/")[0].strip())
    except Exception:
        current = 0

    if status == "Completed":
        total = current if current else "?"
        return f"{total} / {total}"
    else:  # Ongoing
        blocks = 10
        filled = min(blocks, max(0, round((current % 50) / 50 * blocks))) if current else 0
        bar = "▰" * filled + "▱" * (blocks - filled)
        return f"{current} / ∞  {bar}"


def apply_watermark(image_bytes: bytes) -> bytes:
    """Overlay watermark (top-right corner) on image bytes. Optionally upscale first. Returns new bytes."""
    try:
        from PIL import Image
        import os
        from database import load_config
        cfg = load_config()
        rep_api = getattr(cfg, "replicate_api_key", cfg.get("replicate_api_key")) if cfg else None

        # Replicate Upscale check
        if rep_api:
            try:
                import replicate
                _log.info("Triggering Replicate API Upscale...")
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as t:
                    t.write(image_bytes)
                    t.flush()
                    
                    rep_client = replicate.Client(api_token=rep_api)
                    output = rep_client.run(
                        "nightmareai/real-esrgan:42fed1c4974146d4d2414e2be2c5277c7fcf05fcc3a73abf41610695738c1d7b",
                        input={"image": open(t.name, "rb"), "scale": 2, "face_enhance": True}
                    )
                    
                    import urllib.request
                    req = urllib.request.urlopen(output)
                    image_bytes = req.read()
            except Exception as re_err:
                _log.warning(f"[REPLICATE] Failed to upscale: {re_err}")
                
        base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        bw, bh = base.size
        # Load local watermark
        wm_path = os.path.join(os.path.dirname(__file__), "watermark.png")
        if not os.path.exists(wm_path):
            _log.warning("watermark.png missing from bot directory")
            return image_bytes
            
        wm = Image.open(wm_path).convert("RGBA")
        
        # Target size matching reference exactly: ~20% of base image width
        target_w = int(bw * 0.20)
        ratio = target_w / float(wm.width)
        target_h = int(wm.height * ratio)
        wm = wm.resize((target_w, target_h), Image.LANCZOS)
        
        # Paste top-left with 4% left padding and 3% top padding
        x = int(bw * 0.04)
        y = int(bh * 0.03)
        result = base.copy()
        result.paste(wm, (x, y), wm)
        out = io.BytesIO()
        result.convert("RGB").save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception as e:
        import traceback
        _log.warning(f"[WATERMARK] Failed: {e}")
        return image_bytes  # Return original if watermark fails



# Telegram photo caption hard limit is 1024 characters.
# Text messages allow 4096, but since Light Pro posts always include an image,
# we must stay within 1024.
_TG_CAPTION_LIMIT = 1024

# The paid note (both languages), placed in its own collapsible blockquote.
_PAID_NOTE_RAW = (
    "❏ Note: This is a paid story. Access will be available after purchase.\n"
    "❏ नोट: यह स्टोरी फ्री नहीं है, इसे देखने के लिए खरीदना होगा।"
)
_PAID_NOTE_BLOCK = f"\n<blockquote expandable>{html.escape(_PAID_NOTE_RAW)}</blockquote>"


def build_light_pro_format(data):
    name         = data.get("name", "Unknown")
    status       = data.get("status", "Ongoing")
    platform     = data.get("platform", DEFAULT_PLATFORM)
    genre        = data.get("genre", "Unknown")
    desc         = data.get("desc", "")
    episodes_raw = data.get("episodes", "0")
    is_paid      = data.get("is_paid", False)

    episode_line = build_episode_line(episodes_raw, status)

    # ── Fixed story-info block ─────────────────────────────────────────────────
    fixed  = f'<tg-emoji emoji-id="5465432711218863135">♨️</tg-emoji><b>Story</b> : <b>{html.escape(name)}</b>\n'
    fixed += f'<tg-emoji emoji-id="6019118553326689234">🔰</tg-emoji><b>Status</b> : <b>{html.escape(status)}</b>\n'
    fixed += f'<tg-emoji emoji-id="6019455905827920171">🖥</tg-emoji><b>Platform</b> : <b>{html.escape(platform)}</b>\n'
    fixed += f'<tg-emoji emoji-id="6024065724291488135">🧩</tg-emoji><b>Genre</b> : <b>{html.escape(genre)}</b>\n'
    fixed += f'<tg-emoji emoji-id="5937999673510858217">🎬</tg-emoji><b>Episodes</b> : <b>{html.escape(episode_line)}</b>'

    # ── Paid note block (only for paid posts) ──────────────────────────────────
    paid_block = _PAID_NOTE_BLOCK if is_paid else ""

    # ── Description block ─────────────────────────────────────────────────────
    desc_block = ""
    if desc:
        desc_header = "\n<b>📝 Story Description :-</b>\n"
        BQ_OPEN  = "<blockquote expandable>"
        BQ_CLOSE = "</blockquote>"

        # How many characters are left for the description content?
        used = len(fixed) + len(paid_block) + len(desc_header) + len(BQ_OPEN) + len(BQ_CLOSE)
        budget = _TG_CAPTION_LIMIT - used - 5  # 5-char safety margin

        bold_desc = to_bold_unicode(desc)
        safe_desc = html.escape(bold_desc)

        if len(safe_desc) > budget:
            # Trim and append ellipsis (…) so the reader knows it's truncated
            safe_desc = safe_desc[:max(0, budget - 1)] + "…"

        desc_block = f"{desc_header}{BQ_OPEN}{safe_desc}{BQ_CLOSE}"

    res = fixed + desc_block + paid_block
    try:
        from stories_bot import inject_premium_emojis
        return inject_premium_emojis(res)
    except Exception:
        return res



def get_light_pro_kb(data, is_alt=False):
    link        = data.get("link", "")
    backup_link = data.get("backup_link") or link
    if not link:
        return None
    
    is_paid = data.get("is_paid", False)
    if is_paid:
        second_btn_text = "ᴛᴜᴛᴏʀɪᴀʟ"
        try:
            from database import load_config
            cfg = load_config()
            tut_cfg = cfg.get("tutorial_guide", {})
            ext_url = tut_cfg.get("external_url", "").strip()
            bot_uname = data.get("bot_username", "") or cfg.get("bot_username", "StoriesFinderBot")
            second_btn_url = ext_url if ext_url else f"https://t.me/{bot_uname}?start=guide"
        except Exception:
            second_btn_url = backup_link or "https://t.me/StoriesFinderBot?start=guide"
    else:
        second_btn_text = "ʙᴀᴄᴋᴜᴘ"
        second_btn_url = backup_link or "https://t.me/StoriesFinderBot?start=guide"
    
    if is_alt:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Mᴏʀᴇ ‽", url="https://t.me/StoriesLightX"),
            InlineKeyboardButton(second_btn_text,   url=second_btn_url),
        ]])
    
    play_text = "ʙᴜʏ ɴᴏᴡ" if is_paid else "ᴘʟᴀʏ ɴᴏᴡ"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(play_text, url=link),
        InlineKeyboardButton(second_btn_text,   url=second_btn_url),
    ]])


def _build_previews(data):
    fmt = data.get("format", "1")
    if fmt == "light":
        return [build_light_format(data)]
    if fmt == "light_pro":
        return [build_light_pro_format(data)]
    if fmt in ("1", "post"):
        return [build_format_1(data)]
    if fmt in ("2", "intro"):
        return [build_format_2(data)]
    if fmt == "both":
        return [build_format_1(data), build_format_2(data)]
    return [build_format_1(data)]

# ── OCR cleaning ───────────────────────────────────────────────────────────────
def _ocr_clean(raw: str) -> str:
    import re
    text = re.sub(r"[€॥\[\]/|\\<>{}]", "", raw)
    lines = text.split("\n")
    paras, cur = [], []
    for ln in lines:
        s = re.sub(r" +", " ", ln.strip())
        if not s:
            if cur:
                paras.append(" ".join(cur))
                cur = []
        else:
            cur.append(s)
    if cur:
        paras.append(" ".join(cur))
    result = "\n\n".join(paras)
    result = re.sub(r"([।?!]) (?=[A-Za-z0-9\u0900-\u097f])", r"\1\n", result)
    return result.strip()

# ── Background image prefetch (DISABLED: platform APIs no longer functional) ──
async def _bg_prefetch_img(context, name, platform):
    # Pocket FM and Kuku FM APIs return 404/400 as of March 2026.
    # Auto-fetch has been disabled to prevent random/incorrect images.
    pass

# ── Send helper (supports thread/topic) ───────────────────────────────────────
async def _send_post(bot, chat_id, text, photo_ids, thread_id=None, reply_markup=None):
    try:
        from stories_bot import inject_premium_emojis
        text = inject_premium_emojis(text)
    except Exception:
        pass
    kwargs = {"message_thread_id": thread_id} if thread_id else {}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    if photo_ids and len(photo_ids) == 1:
        item = photo_ids[0]
        if item["type"] == "doc":
            return await asyncio.wait_for(bot.send_document(chat_id=chat_id, document=item["id"], caption=text, parse_mode="HTML", **kwargs), timeout=15)
        else:
            return await asyncio.wait_for(bot.send_photo(chat_id=chat_id, photo=item["id"], caption=text, parse_mode="HTML", **kwargs), timeout=15)
    elif photo_ids:
        docs, photos = [], []
        for i, item in enumerate(photo_ids):
            cap, pm = (text, "HTML") if i == 0 else ("", None)
            if item["type"] == "doc": docs.append(InputMediaDocument(media=item["id"], caption=cap, parse_mode=pm))
            else: photos.append(InputMediaPhoto(media=item["id"], caption=cap, parse_mode=pm))
        sent = None
        if docs: sent = await asyncio.wait_for(bot.send_media_group(chat_id=chat_id, media=docs, **kwargs), timeout=15)
        if photos: sent = await asyncio.wait_for(bot.send_media_group(chat_id=chat_id, media=photos, **kwargs), timeout=15)
        return sent
    else:
        return await asyncio.wait_for(bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True, **kwargs), timeout=15)

# ── Route helper: decides next step after name based on format ─────────────────
async def _route_after_name(update, context):
    fmt = context.user_data["pb_data"].get("format", "1")
    name = context.user_data["pb_data"].get("name", "")
    # Start image prefetch for all formats
    asyncio.create_task(_bg_prefetch_img(context, name, DEFAULT_PLATFORM))
    if fmt == "post":
        # Post: Name → Image → Link → Status → Dest
        return await _go_to_img(update, context)
    elif fmt == "intro":
        # Intro: Name → Desc → Image → Genre → Link → Episodes → Status → Dest
        return await _go_to_desc(update, context)
    elif fmt == "light":
        # Light: Name → Status
        return await _go_to_status(update, context)
    elif fmt == "light_pro":
        # Light Pro: Name → Status
        return await _go_to_status(update, context)
    else:
        # Format 1/2: Name → Platform → Desc → Image → Genre → Link → Episodes → Status → Username → Dest
        kb = _kb([["Pocket FM", "Kuku FM"], ["Headfone", "+ Custom"], ["/cancel"]])
        await update.effective_message.reply_text("¤ Platform:", reply_markup=kb)
        return STATE_PLATFORM

# ── Entry ──────────────────────────────────────────────────────────────────────
async def start_builder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_admin(user.id):
        msg = "⛔ Admin only. / केवल एडमिन ही इसे उपयोग कर सकते हैं।"
        if update.callback_query:
            try: await update.callback_query.answer(msg, show_alert=True)
            except: pass
        else:
            await (update.message or update.effective_message).reply_text(msg)
        return ConversationHandler.END

    # Cancel any active session
    if context.user_data.get("pb_data"):
        try:
            m = update.effective_message
            if m: await m.reply_text("· Previous session cancelled.", reply_markup=ReplyKeyboardRemove())
        except: pass

    context.user_data["pb_data"] = {}

    if update.callback_query:
        # Handle success-screen buttons
        cb = update.callback_query.data
        try: await update.callback_query.answer()
        except: pass
        if cb == "pb_success_new":
            context.user_data["pb_data"] = {}
        elif cb == "pb_retry_failed":
            if not context.user_data.get("pb_data"):
                await update.callback_query.message.reply_text("❌ Session expired.")
                return ConversationHandler.END
            return await _show_preview(update.callback_query.message, context)
        elif cb == "pb_success_another":
            old_data = context.user_data.get("last_pb_data", {})
            if not old_data:
                await update.callback_query.message.reply_text("❌ No previous post data found.")
                return ConversationHandler.END
            import copy
            context.user_data["pb_data"] = copy.deepcopy(old_data)
            context.user_data["pb_data"]["destinations"] = []
            context.user_data["pb_data"]["post_mode"] = "new"
            context.user_data["pb_data"].pop("_retry_failed", None)
            kb = _kb([["Channel", "Group"], ["/cancel"]])
            await update.callback_query.message.reply_text("¤ Destination type:", reply_markup=kb)
            return STATE_DEST_TYPE
        elif cb.startswith("pb_success_edit|"):
            parts = cb.split("|")
            context.user_data["pb_data"] = {"post_mode": "edit",
                                             "edit_chat_id": parts[1],
                                             "edit_msg_id": int(parts[2])}
            kb = _kb([["Format 1", "Format 2"], ["Post", "Intro"], ["/cancel"]])
            await update.callback_query.message.reply_text("¤ Select format:", reply_markup=kb)
            return STATE_FORMAT
        elif cb.startswith("pb_mongo_prefill|"):
            story_id = cb.split("|")[1]
            try:
                from plugins.poster_bot import get_story_by_id_from_mongo, load_poster_settings
                settings = load_poster_settings()
                story = await get_story_by_id_from_mongo(story_id)
                if not story:
                    await update.callback_query.message.reply_text("❌ Could not fetch story from MongoDB.")
                    return ConversationHandler.END

                story_name = (
                    story.get("story_name_en") or story.get("story_name_hi") or
                    story.get("story_name") or story.get("title") or "Story"
                )
                story_desc = story.get("story_description") or story.get("description") or ""
                platform = story.get("platform") or "Pocket FM"
                genre = story.get("genre") or "Drama"
                status = story.get("status") or "Ongoing"
                episodes = str(story.get("episodes") or story.get("total_episodes") or "1")
                buy_url = f"https://t.me/UseAryaBot/apminibyarya?startapp=story_{story_id}"

                img_url = ""
                for field in ["poster_url", "poster", "image_url", "cover", "image", "cover_url", "photo_url", "photo", "banner_url", "banner"]:
                    val = str(story.get(field) or "").strip()
                    if val.startswith("http://") or val.startswith("https://"):
                        img_url = val
                        break
                if not img_url:
                    img_url = "https://images.unsplash.com/photo-1614729939124-032f0b56c9ce?w=400"

                target_channel = settings.get("target_channel") or ""

                context.user_data["pb_data"] = {
                    "post_mode": "new",
                    "format": "light_pro",
                    "is_paid": True,
                    "name": story_name,
                    "desc": story_desc,
                    "platform": platform,
                    "genre": genre,
                    "status": status,
                    "episodes": episodes,
                    "link": buy_url,
                    "destination": target_channel,
                    "destinations": [{"chat": target_channel, "thread": None}] if target_channel else [],
                    "photo_ids": [{"type": "photo", "id": img_url}] if img_url else [],
                }

                return await _show_preview(update.callback_query.message, context)
            except Exception as pe:
                _log.error(f"[POST_BUILDER] Error pre-filling MongoDB story: {pe}")
                await update.callback_query.message.reply_text(f"❌ Error loading MongoDB story: {pe}")
                return ConversationHandler.END

    kb = _kb([["[ New ]", "[ Edit ]"], ["/cancel"]])
    await (update.effective_message).reply_text("★ <b>Post Builder</b>\n\nSelect mode:", reply_markup=kb, parse_mode="HTML")
    return STATE_MODE

# ── Mode ───────────────────────────────────────────────────────────────────────
async def handle_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "[ Edit ]":
        context.user_data["pb_data"]["post_mode"] = "edit"
        await update.effective_message.reply_text("Paste the Telegram post link:", reply_markup=ReplyKeyboardRemove())
        return STATE_EDIT_LINK
    elif text == "[ New ]":
        context.user_data["pb_data"]["post_mode"] = "new"
    else:
        await update.effective_message.reply_text("❌ Use the buttons.", reply_markup=_kb([["[ New ]", "[ Edit ]"], ["/cancel"]]))
        return STATE_MODE
    kb = _kb([["Format 1", "Format 2"], ["Post", "Intro"], ["Light", "Light Pro"], ["/cancel"]])
    await update.effective_message.reply_text("¤ Select format:", reply_markup=kb)
    return STATE_FORMAT

# ── Edit link ──────────────────────────────────────────────────────────────────
async def handle_edit_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "t.me/" not in text:
        await update.effective_message.reply_text("❌ Invalid link. Send a valid Telegram post link:")
        return STATE_EDIT_LINK
    try:
        parts = text.split("t.me/")[1].split("/")
        if parts[0] == "c":
            chat_id = "-100" + parts[1]
            msg_id = int(parts[2].split("?")[0])
        else:
            chat_id = "@" + parts[0].lstrip("@")
            msg_id = int(parts[1].split("?")[0])
        context.user_data["pb_data"]["edit_chat_id"] = chat_id
        context.user_data["pb_data"]["edit_msg_id"] = msg_id
    except Exception:
        await update.effective_message.reply_text("❌ Could not parse link. Try again:")
        return STATE_EDIT_LINK
    kb = _kb([["Format 1", "Format 2"], ["Post", "Intro"], ["Light", "Light Pro"], ["/cancel"]])
    await update.effective_message.reply_text("¤ Select format:", reply_markup=kb)
    return STATE_FORMAT

# ── Format ─────────────────────────────────────────────────────────────────────
_FORMAT_MAP = {"Format 1": "1", "Format 2": "2", "Post": "post", "Intro": "intro", "Light": "light", "Light Pro": "light_pro"}

async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    fmt = _FORMAT_MAP.get(text)
    if not fmt:
        await update.effective_message.reply_text("❌ Select from buttons.")
        return STATE_FORMAT
    context.user_data["pb_data"]["format"] = fmt
    prompt = "» Story name:"
    if fmt in ("light", "light_pro"):
        prompt = (
            "➖ Exαmρle: ʟᴏʀᴇᴍ ɪᴘsᴜᴍ ᴅᴏʟᴏʀ sɪᴛ ᴀᴍᴇᴛ, ᴄᴏɴsᴇᴄᴛᴇᴛᴜʀ ᴀᴅɪᴘɪsᴄɪɴɢ ᴇʟɪᴛ.\n\n"
            "➖ Seηd yσur text 👇"
        )
    await update.effective_message.reply_text(prompt, reply_markup=ReplyKeyboardRemove())
    return STATE_NAME

# ── Name ───────────────────────────────────────────────────────────────────────
async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["name"] = update.message.text.strip()
    return await _route_after_name(update, context)

# ── Platform (only for Format 1 / Format 2 full flows) ────────────────────────
_PLATFORM_MAP = {"Pocket FM": "Pocket FM", "Kuku FM": "Kuku FM", "Headfone": "Headfone"}

async def handle_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "+ Custom":
        await update.effective_message.reply_text("Type platform name:", reply_markup=ReplyKeyboardRemove())
        context.user_data["pb_data"]["_await_cust_plat"] = True
        return STATE_PLATFORM
    plat = _PLATFORM_MAP.get(text) or (text if context.user_data["pb_data"].pop("_await_cust_plat", False) else None)
    if not plat:
        await update.effective_message.reply_text("❌ Use buttons or type name.", reply_markup=_kb([["Pocket FM", "Kuku FM"], ["Headfone", "+ Custom"], ["/cancel"]]))
        return STATE_PLATFORM
    context.user_data["pb_data"]["platform"] = plat
    if context.user_data["pb_data"].get("format") in ("light", "light_pro"):
        return await _go_to_genre(update, context)
    return await _go_to_desc(update, context)

# ── Description ────────────────────────────────────────────────────────────────
async def _go_to_desc(update, context):
    kb = _kb([["[ Manual ]", "[ OCR ]"], ["/cancel"]])
    await update.effective_message.reply_text("¤ Description:", reply_markup=kb)
    return STATE_DESC_MODE

async def handle_desc_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "[ Manual ]":
        await update.effective_message.reply_text("» Type description:", reply_markup=ReplyKeyboardRemove())
        return STATE_DESC_ENTER
    elif text == "[ OCR ]":
        if not OCR_AVAILABLE:
            await update.effective_message.reply_text(
                "❌ OCR unavailable.\n<code>sudo apt install tesseract-ocr tesseract-ocr-hin -y</code>\n"
                "Type description manually:", parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
            )
            return STATE_DESC_ENTER
        await update.effective_message.reply_text("Send screenshot:", reply_markup=ReplyKeyboardRemove())
        return STATE_DESC_OCR
    else:
        await update.effective_message.reply_text("❌ Use buttons.", reply_markup=_kb([["[ Manual ]", "[ OCR ]"], ["/cancel"]]))
        return STATE_DESC_MODE

async def handle_desc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["desc"] = update.message.text.strip()
    return await _route_after_desc(update, context)

async def handle_desc_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.document:
        await update.effective_message.reply_text("❌ Send an image.")
        return STATE_DESC_OCR
    wait = await update.effective_message.reply_text("· Scanning...")
    try:
        tg_file = (await update.message.photo[-1].get_file() if update.message.photo
                   else await update.message.document.get_file())
        raw_bytes = await tg_file.download_as_bytearray()
        img = Image.open(io.BytesIO(raw_bytes))
        def _run_ocr(image):
            image = image.convert("L")
            image = ImageEnhance.Contrast(image).enhance(2.0)
            image = image.filter(ImageFilter.SHARPEN)
            w, h = image.size
            image = image.resize((int(w * 1.5), int(h * 1.5)), Image.LANCZOS)
            return pytesseract.image_to_string(image, lang="eng+hin", config="--oem 3 --psm 6")
        raw_text = await asyncio.to_thread(_run_ocr, img)
        cleaned = _ocr_clean(raw_text)
        await wait.delete()
        if len(cleaned) < 10:
            await update.effective_message.reply_text("❌ Try clearer image.\n\n» Enter manually:")
            return STATE_DESC_ENTER
        context.user_data["pb_data"]["temp_found_desc"] = cleaned
        preview = html.escape(cleaned)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("[ Use ]", callback_data="pb_dc|use"),
            InlineKeyboardButton("[ Edit ]", callback_data="pb_dc|manual"),
            InlineKeyboardButton("[ Retry ]", callback_data="pb_dc|retry"),
        ]])
        await update.effective_message.reply_text(f"\u22c6 <b>Extracted:</b>\n\n<blockquote>{preview}</blockquote>",
                                        reply_markup=kb, parse_mode="HTML")
        return STATE_DESC_CHOICE
    except Exception as e:
        _log.error(f"[OCR] {e}", exc_info=True)
        try: await wait.edit_text("❌ OCR error. Enter manually:")
        except: pass
        return STATE_DESC_ENTER

async def handle_desc_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    choice = query.data.split("|")[1]
    try: await query.message.delete()
    except: pass
    if choice == "use":
        context.user_data["pb_data"]["desc"] = context.user_data["pb_data"].get("temp_found_desc", "")
        return await _route_after_desc_query(update, context, query)
    elif choice == "retry":
        await query.message.reply_text("Send another screenshot:")
        return STATE_DESC_OCR
    else:
        await query.message.reply_text("» Type description:")
        return STATE_DESC_ENTER

def _route_after_desc_sync(fmt):
    """Returns next state after description based on format."""
    # All formats go to image next
    return None  # caller handles it

async def _route_after_desc(update, context):
    fmt = context.user_data["pb_data"].get("format", "1")
    if fmt == "post":
        # Post: Image → Desc → Genre
        return await _go_to_genre(update, context)
    return await _go_to_img(update, context)

async def _route_after_desc_query(update, context, query):
    fmt = context.user_data["pb_data"].get("format", "1")
    if fmt == "post":
        return await _go_to_genre_msg(query.message, context)
    return await _go_to_img_msg(query.message, context)

# ── Image ──────────────────────────────────────────────────────────────────────
async def _go_to_img(update, context):
    data = context.user_data["pb_data"]
    await update.effective_message.reply_text("¤ <b>Send image</b>\n<i>(or /skip)</i>", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    return STATE_IMG_UPLOAD

async def _go_to_genre_msg(msg, context):
    await msg.reply_text("¤ Genre:", reply_markup=_kb([["Fantasy", "Suspense"], ["Romance", "Thriller"], ["Action", "Horror"], ["Mystery", "/cancel"]]))
    return STATE_GENRE

async def _go_to_img_msg(msg, context):
    data = context.user_data["pb_data"]
    await msg.reply_text("¤ <b>Send image</b>\n<i>(or /skip)</i>", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    return STATE_IMG_UPLOAD

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["pb_data"]
    if update.message.text and update.message.text.strip() == "/skip":
        data.setdefault("photo_ids", [])
        return await _route_after_img(update, context)
    if update.message.photo:
        fid, mtype = update.message.photo[-1].file_id, "photo"
    elif update.message.document:
        fid, mtype = update.message.document.file_id, "doc"
    else:
        await update.effective_message.reply_text("❌ Send image or /skip")
        return STATE_IMG_UPLOAD
    data["photo_ids"] = [{"id": fid, "type": mtype}]
    return await _route_after_img(update, context)

async def _route_after_img(update, context):
    fmt = context.user_data["pb_data"].get("format", "1")
    if fmt == "post":
        # Post: Image → Description
        return await _go_to_desc(update, context)
    elif fmt == "intro":
        # Intro: Image → Link
        return await _go_to_link(update, context)
    elif fmt in ("light", "light_pro"):
        # Light / Light Pro: Image → Link
        return await _go_to_link(update, context)
    else:
        # Format 1/2: Image → Genre
        return await _go_to_genre(update, context)

async def _route_after_img_msg(msg, context):
    fmt = context.user_data["pb_data"].get("format", "1")
    if fmt == "post":
        await msg.reply_text("» Paste story link:", reply_markup=ReplyKeyboardRemove())
        context.user_data["pb_data"]["_link_required"] = True
        return STATE_LINK
    elif fmt == "intro":
        kb = _kb([["Romance", "Thriller"], ["Crime", "Horror"], ["Suspense", "Drama"], ["+ Custom", "/cancel"]])
        await msg.reply_text("¤ Genre:", reply_markup=kb)
        return STATE_GENRE
    else:
        kb = _kb([["Romance", "Thriller"], ["Crime", "Horror"], ["Suspense", "Drama"], ["+ Custom", "/cancel"]])
        await msg.reply_text("¤ Genre:", reply_markup=kb)
        return STATE_GENRE

# ── Genre ──────────────────────────────────────────────────────────────────────
_GENRES = {"Romance", "Thriller", "Crime", "Horror", "Suspense", "Drama"}

async def _go_to_genre(update, context):
    fmt = context.user_data["pb_data"].get("format", "1")
    if fmt in ("light", "light_pro"):
        kb = _kb([["Fantasy", "Suspense"], ["Romance", "Thriller"], ["Action", "Horror"], ["Mystery", "+ Custom"], ["/cancel"]])
    else:
        kb = _kb([["Romance", "Thriller"], ["Crime", "Horror"], ["Suspense", "Drama"], ["+ Custom", "/cancel"]])
    await update.effective_message.reply_text("¤ Genre:", reply_markup=kb)
    return STATE_GENRE

async def handle_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "+ Custom":
        await update.effective_message.reply_text("Type genre:", reply_markup=ReplyKeyboardRemove())
        context.user_data["pb_data"]["_await_cust_genre"] = True
        return STATE_GENRE
    fmt = context.user_data["pb_data"].get("format", "1")
    # Allow custom genre for light, light_pro and any format using '+ Custom'
    if context.user_data["pb_data"].pop("_await_cust_genre", False):
        genre = text
    elif fmt in ("light", "light_pro"):
        # Accept any text for light formats (all genres allowed)
        genre = text if text else None
    else:
        genre = text if text in _GENRES else None
    if not genre:
        await update.effective_message.reply_text("❌ Use buttons or type genre.")
        return STATE_GENRE
    context.user_data["pb_data"]["genre"] = genre
    if fmt in ("light", "light_pro"):
        return await _go_to_desc(update, context)
    if fmt == "post":
        return await _go_to_link(update, context)
    # Route to link
    return await _go_to_link(update, context)

# ── Link ───────────────────────────────────────────────────────────────────────
async def _go_to_link(update, context):
    fmt = context.user_data["pb_data"].get("format", "1")
    if fmt in ("intro",):
        # Intro: offer default link
        kb = _kb([["Default Link"], ["+ Manual Link", "/skip"]])
        await update.effective_message.reply_text("¤ Story link:", reply_markup=kb)
    else:
        # Format 1/2: offer default link
        kb = _kb([["Default Link"], ["+ Manual Link", "/skip"]])
        await update.effective_message.reply_text("¤ Story link:", reply_markup=kb)
    return STATE_LINK

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data["pb_data"]
    required = data.pop("_link_required", False)

    if text == "Default Link":
        data["link"] = DEFAULT_STORY_LINK
    elif text == "/skip":
        data["link"] = ""
    elif text == "+ Manual Link":
        await update.effective_message.reply_text("» Paste story link:", reply_markup=ReplyKeyboardRemove())
        data["_await_manual_link"] = True
        return STATE_LINK
    elif data.pop("_await_manual_link", False):
        data["link"] = text
    else:
        data["link"] = text

    # Route after link
    fmt = data.get("format", "1")
    if fmt in ("light", "light_pro"):
        kb = _kb([["Hindi", "English"], ["/cancel"]])
        await update.effective_message.reply_text("¤ Select Story Language:", reply_markup=kb)
        return STATE_BACKUP_LINK

    if fmt == "post":
        return await _go_to_status(update, context)
    elif fmt == "intro":
        return await _go_to_status(update, context)
    else:
        await update.effective_message.reply_text("» Total episodes:", reply_markup=ReplyKeyboardRemove())
        return STATE_EPISODES

# ── Backup Link (Light format only) ─────────────────────────────────────────────────
async def handle_backup_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data["pb_data"]
    fmt = data.get("format")

    if fmt in ("light", "light_pro"):
        if text == "English":
            data["backup_link"] = "https://t.me/+z0RjNWMAK8syNDFl"
        else:
            # Default to default backup channel link for Hindi
            data["backup_link"] = "https://t.me/SLBackupTG"
            
        # Transition after backup link
        if fmt == "light_pro":
            await update.effective_message.reply_text("» Current episode count (e.g. 12):", reply_markup=ReplyKeyboardRemove())
            return STATE_EPISODES
        return await _go_to_dest(update, context)

    # Standard non-light flow
    if text in ("Same as Play", "/skip", ""):
        data["backup_link"] = data.get("link", "")  # fall back to main link
    else:
        data["backup_link"] = text
    # Light Pro: ask for episode count (fallback just in case)
    if data.get("format") == "light_pro":
        await update.effective_message.reply_text("» Current episode count (e.g. 12):", reply_markup=ReplyKeyboardRemove())
        return STATE_EPISODES
    return await _go_to_dest(update, context)

# ── Episodes ───────────────────────────────────────────────────────────────────
async def handle_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["episodes"] = update.message.text.strip()
    fmt = context.user_data["pb_data"].get("format", "1")
    if fmt == "light_pro":
        kb = _kb([["Yes", "No"], ["/cancel"]])
        await update.effective_message.reply_text("¤ Is this a Paid post?", reply_markup=kb)
        return STATE_IS_PAID
    return await _go_to_status(update, context)

# ── Paid ───────────────────────────────────────────────────────────────────────
async def handle_is_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text in ["yes", "y", "paid"]:
        context.user_data["pb_data"]["is_paid"] = True
    else:
        context.user_data["pb_data"]["is_paid"] = False
    return await _go_to_dest(update, context)

# ── Status ─────────────────────────────────────────────────────────────────────
async def _go_to_status(update, context):
    kb = _kb([["Completed", "Ongoing"], ["RIP"], ["/cancel"]])
    await update.effective_message.reply_text("¤ Status:", reply_markup=kb)
    return STATE_STATUS

_STATUS_MAP = {"Completed": "Completed", "Ongoing": "Ongoing", "RIP": "RIP"}

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    status = _STATUS_MAP.get(text)
    if not status:
        await update.effective_message.reply_text("❌ Use buttons.", reply_markup=_kb([["Completed", "Ongoing"], ["RIP"], ["/cancel"]]))
        return STATE_STATUS
    context.user_data["pb_data"]["status"] = status
    fmt = context.user_data["pb_data"].get("format", "1")
    # Set defaults and decide username step
    if fmt in ("light", "light_pro"):
        kb = _kb([["Pocket FM", "Kuku FM"], ["Headfone", "+ Custom"], ["/cancel"]])
        await update.effective_message.reply_text("¤ Platform:", reply_markup=kb)
        return STATE_PLATFORM
    if fmt in ("post", "intro"):
        # Auto-fill platform and username
        context.user_data["pb_data"].setdefault("platform", DEFAULT_PLATFORM)
        context.user_data["pb_data"].setdefault("username", DEFAULT_JOIN_USERNAME)
        return await _go_to_dest(update, context)
    else:
        # Full flow: ask username
        kb = _kb([["Default", "+ Custom"], ["/cancel"]])
        await update.effective_message.reply_text("¤ Join username:", reply_markup=kb)
        return STATE_USERNAME

# ── Username ───────────────────────────────────────────────────────────────────
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "Default":
        context.user_data["pb_data"]["username"] = DEFAULT_JOIN_USERNAME
        return await _go_to_dest(update, context)
    elif text == "+ Custom":
        await update.effective_message.reply_text("Type username (e.g. @Channel):", reply_markup=ReplyKeyboardRemove())
        context.user_data["pb_data"]["_await_cust_user"] = True
        return STATE_USERNAME
    elif context.user_data["pb_data"].pop("_await_cust_user", False):
        context.user_data["pb_data"]["username"] = text
        return await _go_to_dest(update, context)
    else:
        await update.effective_message.reply_text("❌ Use buttons.", reply_markup=_kb([["Default", "+ Custom"], ["/cancel"]]))
        return STATE_USERNAME

# ── Destination ────────────────────────────────────────────────────────────────
async def _go_to_dest(update, context):
    if context.user_data["pb_data"].get("post_mode") == "edit":
        return await _show_preview(update.message, context)
    context.user_data["pb_data"]["destinations"] = []
    kb = _kb([["Channel", "Group"], ["/cancel"]])
    await update.effective_message.reply_text("¤ Destination type:", reply_markup=kb)
    return STATE_DEST_TYPE

async def handle_dest_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    channels, groups = _load_destinations()
    if text in ("/skip", "Skip"):
        msg = await update.effective_message.reply_text("· Proceeding with one destination.", reply_markup=ReplyKeyboardRemove())
        return await _show_preview(msg, context)
    elif text == "Channel":
        context.user_data["pb_data"]["_dest_type"] = "channel"
        rows = [[c] for c in channels[:6]]
        rows.append(["+ New"])
        rows.append(["/cancel"])
        await update.effective_message.reply_text("¤ Select channel:", reply_markup=_kb(rows))
        return STATE_DEST_INPUT
    elif text == "Group":
        context.user_data["pb_data"]["_dest_type"] = "group"
        rows = [[f"{g.get('name', g['id'])}" for g in groups[i:i+2]] for i in range(0, len(groups), 2)]
        rows.append(["+ New"])
        rows.append(["/cancel"])
        await update.effective_message.reply_text("¤ Select group:", reply_markup=_kb(rows))
        return STATE_DEST_INPUT
    else:
        await update.effective_message.reply_text("❌ Use buttons.", reply_markup=_kb([["Channel", "Group"], ["Skip"], ["/cancel"]]))
        return STATE_DEST_TYPE

async def _show_preview_from_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("· Proceeding with one destination.", reply_markup=ReplyKeyboardRemove())
    return await _show_preview(msg, context)

async def _add_dest_and_check(msg, update, context, dest, topic=None):
    data = context.user_data["pb_data"]
    data.setdefault("destinations", []).append({"chat": dest, "thread": topic})
    
    if len(data["destinations"]) < 2:
        kb = _kb([["Channel", "Group"], ["Skip"], ["/cancel"]])
        await update.effective_message.reply_text(f"· Saved {dest}.\n\n¤ Add second destination? (or Skip):", reply_markup=kb)
        return STATE_DEST_TYPE
    else:
        msg = await update.effective_message.reply_text("· Destinations set.", reply_markup=ReplyKeyboardRemove())
        return await _show_preview(msg, context)

async def handle_dest_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data["pb_data"]
    dest_type = data.get("_dest_type", "channel")
    _, groups = _load_destinations()

    if text == "+ New":
        await update.effective_message.reply_text(
            "Send the channel/group username or ID\n(e.g. @MyChannel or -1001234567):",
            reply_markup=ReplyKeyboardRemove()
        )
        data["_await_new_dest"] = True
        return STATE_DEST_INPUT

    if data.pop("_await_new_dest", False):
        if dest_type == "channel":
            _save_channel(text)
            return await _add_dest_and_check(update.message, update, context, text, None)
        else:
            _save_group(text)
            data["_temp_dest"] = text
            await update.effective_message.reply_text("» Topic ID (or /skip for no topic):", reply_markup=ReplyKeyboardRemove())
            data["_await_topic"] = True
            return STATE_DEST_TOPIC

    if dest_type == "channel":
        return await _add_dest_and_check(update.message, update, context, text, None)
    else:
        # Match group name to id
        matched = next((g for g in groups if g.get("name", g["id"]) == text or g["id"] == text), None)
        if matched:
            return await _add_dest_and_check(update.message, update, context, matched["id"], matched.get("topic_id"))
        # Otherwise treat as raw ID/username
        data["_temp_dest"] = text
        data["_await_topic"] = True
        await update.effective_message.reply_text("» Topic ID (or /skip):", reply_markup=ReplyKeyboardRemove())
        return STATE_DEST_TOPIC

async def handle_dest_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data["pb_data"]
    topic = None if (text == "/skip" or not text.isdigit()) else int(text)
    dest = data.pop("_temp_dest", "")
    return await _add_dest_and_check(update.message, update, context, dest, topic)

# ── Preview & Confirm ──────────────────────────────────────────────────────────
async def _get_keyboard_for_format(data, is_alt=False):
    """Return correct InlineKeyboardMarkup based on format."""
    fmt = data.get("format")
    if fmt == "light":
        return get_light_kb(data, is_alt=is_alt)
    if fmt == "light_pro":
        return get_light_pro_kb(data, is_alt=is_alt)
    return None


async def _apply_watermark_if_needed(data, context, chat_id):
    """For Light Pro: apply watermark on uploaded image and replace photo_ids entry."""
    if data.get("format") != "light_pro":
        return
    photo_ids = data.get("photo_ids", [])
    if not photo_ids:
        return
    try:
        item = photo_ids[0]
        if str(item.get("id", "")).startswith("http://") or str(item.get("id", "")).startswith("https://"):
            import requests
            resp = await asyncio.to_thread(requests.get, item["id"], timeout=12)
            raw = resp.content
        else:
            tg_file = await context.bot.get_file(item["id"])
            raw = await tg_file.download_as_bytearray()
        watermarked = apply_watermark(bytes(raw))
        sent = await context.bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(watermarked),
            caption="· Watermarked cover preview"
        )
        data["photo_ids"] = [{"id": sent.photo[-1].file_id, "type": "photo"}]
        data["_wm_applied"] = True
    except Exception as e:
        _log.warning(f"[WM_APPLY] {e}")


async def _show_preview(message, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["pb_data"]

    # Apply watermark for Light Pro before building preview
    if data.get("format") == "light_pro" and not data.get("_wm_applied"):
        await _apply_watermark_if_needed(data, context, message.chat_id)

    previews = _build_previews(data)
    data["cached_previews"] = previews
    photo_ids = data.get("photo_ids", [])

    await message.reply_text("· <b>Preview (Main Channel)</b>", parse_mode="HTML")
    button = await _get_keyboard_for_format(data)
    
    async def send_preview(btn):
        for p in previews:
            try:
                if photo_ids:
                    item = photo_ids[0]
                    if item["type"] == "doc":
                        await context.bot.send_document(chat_id=message.chat_id, document=item["id"], caption=p, parse_mode="HTML", reply_markup=btn)
                    else:
                        await context.bot.send_photo(chat_id=message.chat_id, photo=item["id"], caption=p, parse_mode="HTML", reply_markup=btn)
                else:
                    await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True, reply_markup=btn)
            except Exception as e:
                _log.error(f"[PREVIEW] {e}")
                try:
                    await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True, reply_markup=btn)
                except: pass

    await send_preview(button)
    
    if data.get("format") in ("light", "light_pro"):
        alt_button = await _get_keyboard_for_format(data, is_alt=True)
        if alt_button:
            await message.reply_text("· <b>Preview (For Other Channels / Groups)</b>", parse_mode="HTML")
            await send_preview(alt_button)

    kb = _kb([["[ Post ]", "[ Re-edit ]"], ["[ Cancel ]"]])
    await message.reply_text("¤ Confirm:", reply_markup=kb)
    return STATE_CONFIRM

# ── Confirm ────────────────────────────────────────────────────────────────────
async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "[ Cancel ]":
        await update.effective_message.reply_text("· Cancelled.", reply_markup=ReplyKeyboardRemove())
        context.user_data.pop("pb_data", None)
        return ConversationHandler.END
    elif text == "[ Re-edit ]":
        context.user_data.pop("pb_data", None)
        return await start_builder(update, context)
    elif text == "[ Post ]":
        return await _do_post(update, context)
    else:
        await update.effective_message.reply_text("❌ Use buttons.", reply_markup=_kb([["[ Post ]", "[ Re-edit ]"], ["[ Cancel ]"]]))
        return STATE_CONFIRM

# ── Post execution ─────────────────────────────────────────────────────────────
async def _do_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["pb_data"]
    post_mode  = data.get("post_mode", "new")
    
    # Check what kind of dest we have
    if "destinations" in data and len(data["destinations"]) > 0:
        targets = data["destinations"]
    else:
        targets = [{"chat": data.get("destination"), "thread": data.get("thread_id")}]
        
    edit_chat  = data.get("edit_chat_id")
    edit_msg   = data.get("edit_msg_id")
    previews   = data.get("cached_previews", [])
    photo_ids  = data.get("photo_ids", [])

    if post_mode == "new" and not targets[0].get("chat"):
        await update.effective_message.reply_text("❌ No destination. Cancelled.", reply_markup=ReplyKeyboardRemove())
        context.user_data.pop("pb_data", None)
        return ConversationHandler.END

    working = await update.effective_message.reply_text("· Posting...", reply_markup=ReplyKeyboardRemove())
    
    success = False
    sent_msg_id = None
    last_chat_id = None
    errors_list = []

    try:
        if post_mode == "edit":
            p = previews[0]
            button = get_light_kb(data) if data.get("format") == "light" else (
                get_light_pro_kb(data) if data.get("format") == "light_pro" else None
            )
            try:
                if photo_ids:
                    item = photo_ids[0]
                    media = (InputMediaDocument(media=item["id"], caption=p, parse_mode="HTML")
                             if item["type"] == "doc"
                             else InputMediaPhoto(media=item["id"], caption=p, parse_mode="HTML"))
                    await asyncio.wait_for(context.bot.edit_message_media(chat_id=edit_chat, message_id=edit_msg, media=media, reply_markup=button), timeout=15)
                else:
                    try:
                        await asyncio.wait_for(context.bot.edit_message_text(chat_id=edit_chat, message_id=edit_msg, text=p, parse_mode="HTML", disable_web_page_preview=True, reply_markup=button), timeout=15)
                    except Exception as ex2:
                        if "Message is not modified" not in str(ex2):
                            await asyncio.wait_for(context.bot.edit_message_caption(chat_id=edit_chat, message_id=edit_msg, caption=p, parse_mode="HTML", reply_markup=button), timeout=15)
                sent_msg_id = edit_msg
                last_chat_id = edit_chat
                success = True
            except Exception as e:
                if "Message is not modified" in str(e):
                    success = True
                    sent_msg_id = edit_msg
                    last_chat_id = edit_chat
                else:
                    _log.warning(f"[EDIT] Failed: {e}")
                    raise e

        else:
            primary_button = await _get_keyboard_for_format(data, is_alt=False)
            alt_button = await _get_keyboard_for_format(data, is_alt=True)
            
            for i, target in enumerate(targets):
                chat_id = target["chat"]
                thread_id = target["thread"]
                
                # Send primary format to first destination, alternate format to all subsequent destinations
                current_button = primary_button if i == 0 else (alt_button or primary_button)
                
                for p in previews:
                    try:
                        result = await _send_post(context.bot, chat_id, p, photo_ids, thread_id, reply_markup=current_button)
                        if result:
                            success = True
                            last_chat_id = chat_id
                            if not isinstance(result, list):
                                sent_msg_id = result.message_id
                            elif isinstance(result, list) and result:
                                sent_msg_id = result[-1].message_id
                    except Exception as e:
                        err_str = str(e)
                        _log.warning(f"[SEND] Part failed to {chat_id}: {err_str}")
                        errors_list.append(f"{chat_id}: {err_str}")

        if not success:
            if errors_list:
                raise Exception("\n".join(errors_list))
            raise Exception("Telegram API did not return a valid message result.")

        # Save to DB
        try:
            from database import save_story
            save_story(
                name=data.get("name"), genre=data.get("genre", ""),
                link=data.get("link", ""), episodes=data.get("episodes", ""),
                status=data.get("status"),
                description_original=data.get("desc", ""),
                description_short=data.get("desc", ""),
                image_url=photo_ids[0]["id"] if photo_ids else "",
                extra_images=[i["id"] for i in photo_ids[1:]] if len(photo_ids) > 1 else [],
                format_number=1,
            )
        except Exception as e:
            _log.warning(f"[POST] save_story failed: {e}")

        # --- Save Post for Repost Config Menu ---
        if post_mode == "new":
            try:
                import time
                from database import load_saved_posts, save_saved_posts
                saved_db = load_saved_posts()
                fmt_name = data.get("format", "unknown")
                if fmt_name not in saved_db:
                    saved_db[fmt_name] = []
                    
                clean_data = {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list, dict))}
                ptext = data.get("cached_previews", [""])[0] if data.get("cached_previews") else ""
                
                saved_db[fmt_name].insert(0, {
                    "id": str(int(time.time() * 1000)),
                    "name": data.get("name", "Unnamed"),
                    "preview": ptext[:150] + "..." if len(ptext) > 150 else ptext,
                    "data": clean_data,
                    "ts": time.time()
                })
                
                saved_db[fmt_name] = saved_db[fmt_name][:200]
                save_saved_posts(saved_db)
            except Exception as e:
                _log.warning(f"[POST] save_saved_posts failed: {e}")
        try:
            await working.edit_text(f"\u2605 Posted via {post_mode} mode!")
        except Exception:
            pass

        # ── Success screen with inline buttons ──
        success_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("[ New ]",  callback_data="pb_success_new"),
                InlineKeyboardButton("[ Edit ]", callback_data=f"pb_success_edit|{last_chat_id}|{sent_msg_id or 0}"),
            ],
            [
                InlineKeyboardButton("[ Post to Another ]", callback_data="pb_success_another")
            ]
        ])
        await update.effective_message.reply_text(
            "\u22c6 <b>Done!</b>\n\nCreate another or edit this post:",
            reply_markup=success_kb,
            parse_mode="HTML"
        )
        if errors_list:
            err_text = "⚠️ <b>Some destinations failed:</b>\n" + "\n".join(errors_list)
            # Add a retry button to start builder with same data
            retry_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Retry failed", callback_data="pb_retry_failed")
            ]])
            # To allow retry, keep data in context but mark it as retryable
            context.user_data["pb_data"]["_retry_failed"] = True
            await update.effective_message.reply_text(err_text, parse_mode="HTML", reply_markup=retry_kb)
            return ConversationHandler.END  # User stays in normal state until they click Retry
            
    except Exception as e:
        _log.error(f"[POST] {e}", exc_info=True)
        err = html.escape(str(e))[:300]
        try:
            await working.edit_text(f"❌ Error:\n<code>{err}</code>", parse_mode="HTML")
        except Exception:
            await update.effective_message.reply_text(f"❌ Error:\n<code>{err}</code>", parse_mode="HTML")

    if not context.user_data.get("pb_data", {}).get("_retry_failed"):
        if context.user_data.get("pb_data"):
            import copy
            context.user_data["last_pb_data"] = copy.deepcopy(context.user_data["pb_data"])
        context.user_data.pop("pb_data", None)
    return ConversationHandler.END

# ── Cancel ─────────────────────────────────────────────────────────────────────
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pb_data", None)
    try: await update.effective_message.reply_text("· Cancelled.", reply_markup=ReplyKeyboardRemove())
    except: pass
    return ConversationHandler.END

# ── ConversationHandler ────────────────────────────────────────────────────────
post_builder_handler = ConversationHandler(
    allow_reentry=True,
    entry_points=[
        CommandHandler("JeetX", start_builder),
        CallbackQueryHandler(start_builder, pattern=r"^menu\|createpost"),
        CallbackQueryHandler(start_builder, pattern=r"^pb_success_"),
        CallbackQueryHandler(start_builder, pattern=r"^pb_retry_failed"),
        CallbackQueryHandler(start_builder, pattern=r"^pb_mongo_prefill\|"),
    ],
    states={
        STATE_MODE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mode)],
        STATE_EDIT_LINK:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_link)],
        STATE_FORMAT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_format)],
        STATE_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
        STATE_DESC_MODE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_desc_mode)],
        STATE_DESC_ENTER:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_desc_text)],
        STATE_DESC_OCR:    [MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_desc_ocr)],
        STATE_DESC_CHOICE: [CallbackQueryHandler(handle_desc_choice, pattern=r"^pb_dc\|")],
        STATE_IMG_UPLOAD:  [
            MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image),
            CommandHandler("skip", handle_image),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_image),
        ],
        STATE_PLATFORM:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_platform)],
        STATE_GENRE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_genre)],
        STATE_LINK:      [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link),
            CommandHandler("skip", handle_link),
        ],
        STATE_BACKUP_LINK:[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_backup_link)],
        STATE_EPISODES:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_episodes)],
        STATE_STATUS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_status)],
        STATE_USERNAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username)],
        STATE_DEST_TYPE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dest_type),
            CommandHandler("skip", _show_preview_from_skip),
        ],
        STATE_DEST_INPUT:[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dest_input)],
        STATE_DEST_TOPIC:[
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dest_topic),
            CommandHandler("skip", handle_dest_topic),
        ],
        STATE_CONFIRM:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm)],
        STATE_IS_PAID:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_is_paid)],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_handler),
        CommandHandler("JeetX", start_builder),
    ],
    per_message=False,
)
