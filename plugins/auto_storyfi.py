"""
plugins/auto_storyfi.py
-----------------------
Auto Storyfi Engine:
- Discovers channels where Riya Bot is Administrator.
- Cleans channel titles (removes "Pocket FM", "Pvt", "Story TV", etc.) and matches with MongoDB premium stories.
- Posts HD story banner with 'storyfiwatermark.png' (top_right) + Buy Via App / Bot + clean Tutorial buttons.
- Sends engaging auto-reply tagged to the main post.
- Automatically replaces/deletes old post and old reply when new post arrives.
- Staggered posting with randomized minutes gap between channels.
- Full Admin Control Panel integrated into /config.
"""

import os
import io
import re
import time
import random
import asyncio
import logging
import requests
from typing import Dict, Any, Tuple, Optional, List

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from config import (
    DATA_DIR,
    ADMIN_ID,
    OWNER_ID,
)

logger = logging.getLogger("AutoStoryfi")
logger.setLevel(logging.INFO)

CONFIG_FILE = os.path.join(DATA_DIR, "auto_storyfi_config.json")

# ------------------------------------------------------------------------------
# 1. STORAGE & SETTINGS MANAGEMENT
# ------------------------------------------------------------------------------

DEFAULT_STORYFI_CONFIG = {
    "enabled": False,
    "interval_hours": 12.0,            # 12 hours = 2 posts per day
    "stagger_min_minutes": 2,          # Min gap in minutes between channels
    "stagger_max_minutes": 5,          # Max gap in minutes between channels
    "reply_enabled": True,
    "reply_tone": "mixed",             # mixed, savage, humour, marketing, mysterious
    "watermark_enabled": True,
    "watermark_position": "top_right",
    "watermark_opacity": 0.85,
    "watermark_path": "storyfiwatermark.png",
    "notify_owner_on_missing": True,
    "channels": {},                    # str(chat_id) -> channel info dict
    "blacklisted_channels": [],        # list of int/str chat_ids
}


def load_storyfi_config() -> Dict[str, Any]:
    """Load Auto Storyfi configuration from JSON file."""
    if not os.path.exists(CONFIG_FILE):
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        save_storyfi_config(DEFAULT_STORYFI_CONFIG)
        return DEFAULT_STORYFI_CONFIG.copy()
    try:
        import json
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Merge defaults for any missing keys
            for k, v in DEFAULT_STORYFI_CONFIG.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception as e:
        logger.error(f"[AutoStoryfi] Error loading config: {e}")
        return DEFAULT_STORYFI_CONFIG.copy()


def save_storyfi_config(config: Dict[str, Any]):
    """Save Auto Storyfi configuration to JSON file."""
    try:
        import json
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[AutoStoryfi] Error saving config: {e}")


# ------------------------------------------------------------------------------
# 2. CHANNEL TITLE CLEANER & MONGODB STORY MATCHER
# ------------------------------------------------------------------------------

def clean_channel_title_for_story(channel_title: str) -> str:
    """
    Strips noise, buzzwords, and platforms from channel titles to extract the core story name.
    Example: 'K for King - Pocket FM (Pvt)' -> 'K for King'
             'Dhruvash Story TV [Official]' -> 'Dhruvash'
             'Detective Ajay - Window TV' -> 'Detective Ajay'
             'Vashikaran | Channel Shifted' -> 'Vashikaran'
    """
    if not channel_title:
        return ""

    t = str(channel_title).strip()

    # Remove bracketed content: [Official], (Pvt), {Hindi}, etc.
    t = re.sub(r"\[.*?\]|\(.*?\)|<.*?>|\{.*?\}", " ", t)

    # Noise keywords to strip (case-insensitive word boundaries)
    buzzwords = [
        r"pocket\s*fm", r"kuku\s*fm", r"story\s*tv", r"pvt", r"private",
        r"official", r"audiobook", r"audio\s*story", r"all\s*episodes",
        r"episodes?", r"hindi", r"season\s*\d+", r"part\s*\d+", r"4k", r"hd",
        r"channel\s*shifted", r"channel\s*shift", r"shifted", r"channel",
        r"series", r"stories", r"story", r"full\s*story", r"window\s*tv",
        r"window", r"audio", r"vip", r"premium", r"exclusive", r"backup",
        r"discussion", r"group"
    ]
    for bw in buzzwords:
        t = re.sub(rf"\b{bw}\b", " ", t, flags=re.IGNORECASE)

    # Replace separators with spaces
    t = re.sub(r"[|\-_:•~*+/#\\—–]", " ", t)
    # Remove emojis and extra spaces
    t = re.sub(r"\s+", " ", t).strip()

    # Remove leading numbers/symbols if any
    t = re.sub(r"^[\d\W_]+", "", t).strip()
    return t


def extract_candidate_titles(channel_title: str) -> List[str]:
    """
    Extracts high-probability story title variations from a channel name.
    Handles pipes (MTAH | Married to a...), dashes, brackets, and full clean names.
    """
    if not channel_title:
        return []

    raw = str(channel_title).strip()
    candidates: List[str] = []

    # 1. Clean full title
    cleaned_full = clean_channel_title_for_story(raw)
    if cleaned_full and len(cleaned_full) >= 2:
        candidates.append(cleaned_full)

    # 2. Segments split by |, -, •, :, /
    parts = re.split(r"[|\-•:/\\—–]", raw)
    for p in parts:
        c_p = clean_channel_title_for_story(p)
        if c_p and len(c_p) >= 2 and c_p not in candidates:
            # Multi-word segments get priority over 1-word acronyms
            if len(c_p.split()) > 1:
                candidates.insert(0, c_p)
            else:
                candidates.append(c_p)

    # 3. Inside brackets
    inside_brackets = re.findall(r"\((.*?)\)|\[(.*?)\]|\{(.*?)\}", raw)
    for tup in inside_brackets:
        for item in tup:
            if item:
                c_item = clean_channel_title_for_story(item)
                if c_item and len(c_item) >= 2 and c_item not in candidates:
                    candidates.append(c_item)

    return candidates


def find_story_for_channel_sync(channel_title: str, config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Finds matching story in MongoDB 'premium_stories' by title or fuzzy match.
    Uses mongo_search connection pool with fallback to local database.
    """
    if not channel_title or len(str(channel_title).strip()) < 2:
        return None

    candidates = extract_candidate_titles(channel_title)
    if not candidates:
        candidates = [clean_channel_title_for_story(channel_title)]

    # 1. Try search_story_from_mongo_sync for each candidate
    try:
        from mongo_search import search_story_from_mongo_sync
        for cand in candidates:
            res = search_story_from_mongo_sync(cand)
            if res:
                return res
    except Exception as _ms_e:
        logger.debug(f"[AutoStoryfi Matcher] search_story_from_mongo_sync note: {_ms_e}")

    # 2. Direct MongoDB collection scan with candidate regex & fuzzy matching
    try:
        from mongo_search import _get_mongo_client
        collection, client, p_settings = _get_mongo_client()
        if collection is not None:
            for cand in candidates:
                if not cand or len(cand) < 2:
                    continue
                escaped = re.escape(cand)
                # Exact or case-insensitive match
                doc = collection.find_one({
                    "$or": [
                        {"story_name_en": {"$regex": f"^{escaped}$", "$options": "i"}},
                        {"story_name_hi": {"$regex": f"^{escaped}$", "$options": "i"}},
                        {"story_name": {"$regex": f"^{escaped}$", "$options": "i"}},
                        {"title": {"$regex": f"^{escaped}$", "$options": "i"}},
                        {"name": {"$regex": f"^{escaped}$", "$options": "i"}},
                    ]
                })
                if doc:
                    doc["_id"] = str(doc["_id"])
                    return doc

                # Substring match
                doc = collection.find_one({
                    "$or": [
                        {"story_name_en": {"$regex": escaped, "$options": "i"}},
                        {"story_name_hi": {"$regex": escaped, "$options": "i"}},
                        {"story_name": {"$regex": escaped, "$options": "i"}},
                        {"title": {"$regex": escaped, "$options": "i"}},
                        {"name": {"$regex": escaped, "$options": "i"}},
                    ]
                })
                if doc:
                    doc["_id"] = str(doc["_id"])
                    return doc

            # Fuzzy match across collection documents
            all_docs = list(collection.find({}, limit=500))
            from difflib import SequenceMatcher
            for cand in candidates:
                cand_lower = cand.lower()
                best_doc = None
                best_ratio = 0.0
                for d in all_docs:
                    d_name = (
                        d.get("story_name_en") or d.get("story_name_hi") or
                        d.get("story_name") or d.get("title") or d.get("name") or ""
                    ).strip().lower()
                    if not d_name:
                        continue
                    if cand_lower == d_name or cand_lower in d_name or d_name in cand_lower:
                        d["_id"] = str(d["_id"])
                        return d
                    ratio = SequenceMatcher(None, cand_lower, d_name).ratio()
                    if ratio > best_ratio and ratio >= 0.65:
                        best_ratio = ratio
                        best_doc = d
                if best_doc:
                    best_doc["_id"] = str(best_doc["_id"])
                    return best_doc
    except Exception as e:
        logger.warning(f"[AutoStoryfi Matcher] MongoDB query error: {e}")

    # 3. Fallback to Local Scanned Database
    try:
        from database import load_db
        from search_engine import search_story_exact_or_alias, search_stories_by_keyword
        for cand in candidates:
            res = search_story_exact_or_alias(cand)
            if res:
                return res
            res_kw = search_stories_by_keyword(cand)
            if res_kw:
                return res_kw[0]
        db = load_db()
        for cand in candidates:
            c_low = cand.lower()
            for k, v in db.items():
                if c_low == k.lower() or c_low in k.lower() or k.lower() in c_low:
                    return v
    except Exception:
        pass

    return None


# ------------------------------------------------------------------------------
# 3. ENGAGING AUTO-REPLY MESSAGES LIBRARY
# ------------------------------------------------------------------------------

SAVAGE_REPLIES = [
    "🔥 Bhai is story ka cliffhanger dekh kar hosh udd jayenge! Pure suspense ka baap hai 🤯 Must listen!",
    "⚠️ Warning: Ek baar sunna shuru kiya to bina complete kiye so nahi paoge! Super addictive 🔥",
    "Plot twist aisa ki dimaag hil jaye 🤯 Agar ye nahi suni to pocket fm chalana bekar hai!",
    "Har episode me ek naya banger twist 🎧 Popcorn ready rakho, direct goosebumps aane wale hain!",
    "Is story ka villain to alag hi level par hai 💀 Sabhi episodes abhi suno aur rate karo!",
]

HUMOUR_REPLIES = [
    "Doston popcorn ready rakho 🍿 Aisa drama kahi nahi milega, guaranteed full entertainment!",
    "Kahani itni spicy hai ki headphones pighal jayenge 🎧🌶️ Aaj ka quota complete karo!",
    "Is character ki kismat dekh kar lagta hai meri life fir bhi theek chal rahi hai 😂 Super funny & thrilling!",
    "Free time me bore hone se achha ye sun lo, time kab nikal jayega pata bhi nahi chalega 🚀",
]

MARKETING_REPLIES = [
    "🌟 Trending #1 Story on Pocket FM! All latest episodes are now unlocked & ready.",
    "🚀 Instant Access Unlocked! Click 'Buy Via App' or 'Buy Via Bot' to listen right away.",
    "🎧 Premium Audio Story available with full HD sound & complete episodes!",
    "🔥 Don't miss out on today's most loved story series! Tap buttons below to get started.",
]

MYSTERIOUS_REPLIES = [
    "Is kahani ke peeche ek aisa raaz chupa hai jiska koi andaza bhi nahi laga sakta... 🤫 Ek baar zaroor suno!",
    "Har mod par ek naya mystery khulta hai 🔮 Ending dekh kar hairan reh jaoge!",
    "Kya sach me wahi tha jo dikh raha tha? 🕵️‍♂️ Suniye aur khud pata lagaiye!",
]


def get_random_engaging_reply(story_name: str, tone: str = "mixed") -> str:
    """Generates an engaging reply message tailored to the chosen tone."""
    tone = tone.lower()
    if tone == "savage":
        pool = SAVAGE_REPLIES
    elif tone == "humour":
        pool = HUMOUR_REPLIES
    elif tone == "marketing":
        pool = MARKETING_REPLIES
    elif tone == "mysterious":
        pool = MYSTERIOUS_REPLIES
    else:  # mixed
        pool = SAVAGE_REPLIES + HUMOUR_REPLIES + MARKETING_REPLIES + MYSTERIOUS_REPLIES

    selected = random.choice(pool)
    return selected


# ------------------------------------------------------------------------------
# 4. POST GENERATION & AUTO-REPLACEMENT PUBLISHER
# ------------------------------------------------------------------------------

async def publish_story_to_channel(bot, chat_id: int, channel_info: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Builds poster card with storyfiwatermark.png (top_right), sends to channel,
    sends engaging reply, and deletes previous post + previous reply.
    """
    clean_title = channel_info.get("cleaned_title") or clean_channel_title_for_story(channel_info.get("title", ""))
    story = find_story_for_channel_sync(clean_title, config)

    if not story:
        logger.warning(f"[AutoStoryfi] No matching story found for channel '{channel_info.get('title')}' (clean='{clean_title}')")
        if config.get("notify_owner_on_missing", True):
            try:
                alert_text = (
                    f"⚠️ <b>[Auto Storyfi Alert]</b>\n\n"
                    f"Channel: <b>{channel_info.get('title')}</b> (<code>{chat_id}</code>)\n"
                    f"Extracted Title: <code>{clean_title}</code>\n"
                    f"❌ <i>No matching story found in MongoDB or Local Database. Skipping post.</i>"
                )
                for admin in [OWNER_ID, ADMIN_ID]:
                    if admin:
                        try: await bot.send_message(chat_id=admin, text=alert_text, parse_mode="HTML")
                        except Exception: pass
            except Exception:
                pass
        return False, f"No story found for '{clean_title}'"

    story_name = (
        story.get("story_name_en") or
        story.get("story_name_hi") or
        story.get("story_name") or
        story.get("title") or
        clean_title
    )

    # 1. Image Resolution
    img_url = ""
    for field in ["poster_url", "poster", "square_poster", "image_url", "cover", "image", "cover_url", "photo_url", "photo"]:
        val = str(story.get(field) or "").strip()
        if val.startswith("http://") or val.startswith("https://"):
            img_url = val
            break

    # 2. Build Caption
    from plugins.poster_bot import build_post_caption
    caption = build_post_caption(story)

    # 3. Build Buttons (Poster Bot Style, NO book emoji on Tutorial, identical tutorial link)
    story_id = str(story.get("_id", ""))
    buy_app_url = f"https://t.me/UseAryaBot/apminibyarya?startapp=story_{story_id}"
    buy_bot_url = f"https://t.me/StoryfiBot?start=buy_{story_id}"

    # Get standard tutorial URL from stories_bot
    from stories_bot import get_tutorial_url
    tutorial_url = get_tutorial_url()

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Buy Via App", url=buy_app_url, api_kwargs={"icon_custom_emoji_id": "5456343263340405032"})],
        [InlineKeyboardButton("🛍️ Buy Via Bot", url=buy_bot_url, api_kwargs={"icon_custom_emoji_id": "5456343263340405032"})],
        [InlineKeyboardButton("Tutorial", url=tutorial_url, api_kwargs={"icon_custom_emoji_id": "6266794310671275367"})]
    ])

    # 4. Watermarking with storyfiwatermark.png (top_right)
    photo_payload = None
    wm_enabled = config.get("watermark_enabled", True)
    wm_pos = config.get("watermark_position", "top_right")
    wm_opacity = float(config.get("watermark_opacity", 0.85))

    wm_candidates = [
        "storyfiwatermark.png",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "storyfiwatermark.png"),
        r"C:\Users\User\Downloads\new\storyfiwatermark.png",
    ]
    wm_path = next((p for p in wm_candidates if os.path.exists(p)), "storyfiwatermark.png")

    if img_url:
        try:
            from plugins.poster_bot import apply_watermark_to_image
            if wm_enabled and os.path.exists(wm_path):
                photo_payload = await asyncio.to_thread(apply_watermark_to_image, img_url, wm_path, wm_pos, wm_opacity)
            else:
                resp = await asyncio.to_thread(requests.get, img_url, timeout=12)
                if resp.status_code == 200:
                    photo_payload = io.BytesIO(resp.content)
        except Exception as e:
            logger.warning(f"[AutoStoryfi] Image process error for '{img_url}': {e}")

    # Fallback to local default cover
    if not photo_payload:
        default_cover = "default_cover.png"
        if not os.path.exists(default_cover):
            default_cover = os.path.join(DATA_DIR, "default_cover.png")
        if os.path.exists(default_cover):
            with open(default_cover, "rb") as f:
                photo_payload = io.BytesIO(f.read())

    # 5. Delete Previous Post & Previous Reply in this Channel (Auto-Replacement)
    old_post_id = channel_info.get("last_post_msg_id")
    old_reply_id = channel_info.get("last_reply_msg_id")

    if old_reply_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_reply_id)
            logger.info(f"[AutoStoryfi] Deleted old reply msg {old_reply_id} in channel {chat_id}")
        except Exception as _e_del_r:
            logger.debug(f"[AutoStoryfi] Old reply delete err: {_e_del_r}")

    if old_post_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_post_id)
            logger.info(f"[AutoStoryfi] Deleted old main post msg {old_post_id} in channel {chat_id}")
        except Exception as _e_del_p:
            logger.debug(f"[AutoStoryfi] Old main post delete err: {_e_del_p}")

    # 6. Send Main Story Post
    sent_post = None
    try:
        if photo_payload:
            photo_payload.seek(0)
            sent_post = await bot.send_photo(
                chat_id=chat_id,
                photo=photo_payload,
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            sent_post = await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=False
            )
    except Exception as e:
        logger.error(f"[AutoStoryfi] Failed to send post to channel {chat_id}: {e}")
        return False, str(e)

    # 7. Send Engaging Reply tagged to the Main Post
    sent_reply = None
    if config.get("reply_enabled", True) and sent_post:
        try:
            await asyncio.sleep(1.5)  # small pause before reply
            reply_text = get_random_engaging_reply(story_name, config.get("reply_tone", "mixed"))
            sent_reply = await bot.send_message(
                chat_id=chat_id,
                text=reply_text,
                reply_to_message_id=sent_post.message_id
            )
            logger.info(f"[AutoStoryfi] Sent auto-reply to msg {sent_post.message_id} in {chat_id}")
        except Exception as e_reply:
            logger.warning(f"[AutoStoryfi] Reply send error in {chat_id}: {e_reply}")

    # 8. Update Channel Stats in Configuration
    channel_info["last_post_msg_id"] = sent_post.message_id if sent_post else None
    channel_info["last_reply_msg_id"] = sent_reply.message_id if sent_reply else None
    channel_info["last_post_time"] = time.time()
    channel_info["matched_story_name"] = story_name
    channel_info["matched_story_id"] = story_id

    config["channels"][str(chat_id)] = channel_info
    save_storyfi_config(config)

    return True, f"Posted '{story_name}' successfully!"


# ------------------------------------------------------------------------------
# 5. CHANNEL SCANNER & DISCOVERY
# ------------------------------------------------------------------------------

async def scan_and_sync_admin_channels(bot, context=None) -> Dict[str, Any]:
    """
    Discovers all channels where Riya Bot is Admin, extracts story names,
    and updates auto_storyfi_config.json.
    """
    config = load_storyfi_config()
    discovered: Dict[str, Any] = config.get("channels", {})
    blacklisted = set(config.get("blacklisted_channels", []))

    known_channel_ids = set()

    # 1. From database.py story_channels.json & channels_db.json
    try:
        from database import load_story_channels, load_config as _load_bot_cfg
        sc = load_story_channels()
        for c in sc.get("ongoing", []) + sc.get("completed", []):
            known_channel_ids.add(c)
        bc = _load_bot_cfg()
        for c in bc.get("sources", []):
            known_channel_ids.add(c)
    except Exception:
        pass

    # 2. From Telethon link_checker dialogs if available
    try:
        from link_checker import link_checker
        if link_checker and link_checker.client and link_checker.client.is_connected():
            async for dialog in link_checker.client.iter_dialogs():
                if dialog.is_channel:
                    known_channel_ids.add(dialog.id)
    except Exception as e:
        logger.debug(f"[AutoStoryfi Scan] Telethon scan note: {e}")

    # 3. Verify each channel with Bot API
    for cid in list(known_channel_ids):
        str_cid = str(cid)
        if cid in blacklisted or str_cid in blacklisted:
            continue
        try:
            chat = await bot.get_chat(cid)
            if chat.type in ("channel", "supergroup"):
                member = await bot.get_chat_member(cid, bot.id)
                if member.status in ("administrator", "creator"):
                    # Check post permissions
                    can_post = getattr(member, "can_post_messages", True)
                    if can_post:
                        title = chat.title or str_cid
                        username = chat.username or ""
                        clean_title = clean_channel_title_for_story(title)
                        matched_story = find_story_for_channel_sync(title, config)

                        existing = discovered.get(str_cid, {})
                        discovered[str_cid] = {
                            "id": chat.id,
                            "title": title,
                            "username": username,
                            "cleaned_title": clean_title,
                            "matched_story_name": (
                                matched_story.get("story_name_en") or
                                matched_story.get("story_name") or
                                matched_story.get("title") or "None"
                            ) if matched_story else None,
                            "matched_story_id": str(matched_story.get("_id", "")) if matched_story else None,
                            "last_post_msg_id": existing.get("last_post_msg_id"),
                            "last_reply_msg_id": existing.get("last_reply_msg_id"),
                            "last_post_time": existing.get("last_post_time", 0),
                            "enabled": existing.get("enabled", True),
                        }
        except Exception as _e_chk:
            logger.debug(f"[AutoStoryfi Scan] Channel {cid} check error: {_e_chk}")

    # Re-match any existing channel that was missing match
    for str_cid, c_data in discovered.items():
        if not c_data.get("matched_story_name") or c_data.get("matched_story_name") == "None":
            m_story = find_story_for_channel_sync(c_data.get("title", ""), config)
            if m_story:
                c_data["matched_story_name"] = (
                    m_story.get("story_name_en") or
                    m_story.get("story_name") or
                    m_story.get("title") or "None"
                )
                c_data["matched_story_id"] = str(m_story.get("_id", ""))

    config["channels"] = discovered
    save_storyfi_config(config)
    return config


# ------------------------------------------------------------------------------
# 6. ASYNCHRONOUS WORKER LOOP (MINUTE-BASED STAGGERING)
# ------------------------------------------------------------------------------

async def auto_storyfi_worker_loop(application):
    """
    Background worker that runs continuously:
    - Checks scheduled intervals (e.g. 12h for 2 posts/day).
    - Staggers channel posts with randomized gap in MINUTES (e.g. 2-5 minutes) between channels.
    """
    logger.info("[AutoStoryfi] Worker loop initialized & active ✅")
    await asyncio.sleep(15)  # initial startup delay

    while True:
        try:
            config = load_storyfi_config()
            if not config.get("enabled", False):
                await asyncio.sleep(60)
                continue

            interval_seconds = float(config.get("interval_hours", 12.0)) * 3600
            channels = config.get("channels", {})
            blacklisted = set(config.get("blacklisted_channels", []))

            stagger_min_m = max(1, int(config.get("stagger_min_minutes", 2)))
            stagger_max_m = max(stagger_min_m, int(config.get("stagger_max_minutes", 5)))

            now = time.time()
            due_channels = []

            for str_cid, c_data in channels.items():
                if not c_data.get("enabled", True):
                    continue
                cid = c_data.get("id")
                if cid in blacklisted or str_cid in blacklisted:
                    continue

                last_time = float(c_data.get("last_post_time", 0))
                if (now - last_time) >= interval_seconds:
                    due_channels.append((cid, c_data))

            if due_channels:
                logger.info(f"[AutoStoryfi] Found {len(due_channels)} channels due for scheduled posting.")
                # Shuffle slightly so order varies naturally
                random.shuffle(due_channels)

                for cid, c_data in due_channels:
                    # Re-check enabled state in case changed during loop
                    current_cfg = load_storyfi_config()
                    if not current_cfg.get("enabled", False):
                        break

                    logger.info(f"[AutoStoryfi] Publishing story to channel '{c_data.get('title')}' ({cid})...")
                    success, msg = await publish_story_to_channel(application.bot, cid, c_data, current_cfg)
                    logger.info(f"[AutoStoryfi] Result for {cid}: success={success} | msg={msg}")

                    # Randomized Stagger Gap in MINUTES between channels (Anti-Flood Protection)
                    gap_seconds = random.randint(stagger_min_m * 60, stagger_max_m * 60)
                    logger.info(f"[AutoStoryfi] Waiting {gap_seconds // 60}m {gap_seconds % 60}s before next channel post...")
                    await asyncio.sleep(gap_seconds)

        except Exception as e:
            logger.error(f"[AutoStoryfi Worker Loop Error] {e}")

        await asyncio.sleep(60)


# ------------------------------------------------------------------------------
# 7. INLINE CONFIGURATION UI & CALLBACK HANDLERS
# ------------------------------------------------------------------------------

def get_storyfi_main_panel(caller_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    """Builds the main Auto Storyfi admin panel text & keyboard."""
    config = load_storyfi_config()
    is_on = config.get("enabled", False)
    status_str = "🟢 Active (ON)" if is_on else "🔴 Stopped (OFF)"
    interval_h = config.get("interval_hours", 12.0)
    posts_per_day = round(24.0 / interval_h, 1) if interval_h > 0 else 0

    stagger_min = config.get("stagger_min_minutes", 2)
    stagger_max = config.get("stagger_max_minutes", 5)
    tone = config.get("reply_tone", "mixed").capitalize()

    channels = config.get("channels", {})
    total_ch = len(channels)
    active_ch = sum(1 for c in channels.values() if c.get("enabled", True))
    matched_ch = sum(1 for c in channels.values() if c.get("matched_story_name"))

    text = (
        "<b>🤖 Auto Storyfi Control Panel</b>\n"
        "──────────────────────\n"
        f"• <b>Status:</b> {status_str}\n"
        f"• <b>Frequency:</b> <code>{interval_h}h</code> (~{posts_per_day} Posts / Day)\n"
        f"• <b>Stagger Gap:</b> <code>{stagger_min}–{stagger_max} Minutes</code>\n"
        f"• <b>Reply Tone:</b> <code>{tone}</code>\n"
        f"• <b>Connected Channels:</b> <code>{active_ch}/{total_ch} Active</code>\n"
        f"• <b>Matched Stories:</b> <code>{matched_ch}/{total_ch} Ready</code>\n"
        "──────────────────────\n"
        "<i>Auto Storyfi automatically posts HD story cards + replies into channel-specific story channels, replacing old posts.</i>"
    )

    toggle_btn_text = "🔴 Stop Engine" if is_on else "🟢 Start Engine"

    keyboard = [
        [
            InlineKeyboardButton(toggle_btn_text, callback_data=f"cfg|storyfi|toggle||{caller_id}"),
            InlineKeyboardButton("🔍 Auto-Scan Channels", callback_data=f"cfg|storyfi|scan||{caller_id}")
        ],
        [
            InlineKeyboardButton("📋 Channels List", callback_data=f"cfg|storyfi|channels|0||{caller_id}"),
            InlineKeyboardButton("⏱️ Frequency (2x/1x/3x)", callback_data=f"cfg|storyfi|freq||{caller_id}")
        ],
        [
            InlineKeyboardButton("⏳ Stagger Minutes Gap", callback_data=f"cfg|storyfi|stagger||{caller_id}"),
            InlineKeyboardButton(f"💬 Reply Tone ({tone})", callback_data=f"cfg|storyfi|tone||{caller_id}")
        ],
        [
            InlineKeyboardButton("🚀 Send Instant Test Post", callback_data=f"cfg|storyfi|test_select||{caller_id}")
        ],
        [
            InlineKeyboardButton("➦ Back to Config", callback_data=f"cfg|main||{caller_id}")
        ]
    ]

    return text, InlineKeyboardMarkup(keyboard)


async def handle_storyfi_callbacks(query, context: ContextTypes.DEFAULT_TYPE, parts: List[str], caller_id: int):
    """
    Routes all cfg|storyfi|... callbacks.
    parts format: ['cfg', 'storyfi', action, param1, caller_id]
    """
    action = parts[2] if len(parts) > 2 else "main"
    param = parts[3] if len(parts) > 3 else ""

    # Instant response to Telegram so button never freezes
    try:
        await query.answer()
    except Exception:
        pass

    config = load_storyfi_config()

    if action == "noop":
        return

    if action == "main":
        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            if "Message is not modified" not in str(e):
                try: await query.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
                except Exception: pass
        return

    if action == "toggle":
        config["enabled"] = not config.get("enabled", False)
        save_storyfi_config(config)
        status_txt = "🟢 Auto Storyfi Started!" if config["enabled"] else "🔴 Auto Storyfi Stopped."
        try: await query.answer(status_txt, show_alert=True)
        except Exception: pass
        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return

    if action == "scan":
        try: await query.answer("🔍 Scanning admin channels & matching stories in background...", show_alert=True)
        except Exception: pass
        
        async def _do_scan_bg():
            try:
                updated_cfg = await scan_and_sync_admin_channels(context.bot, context)
                tot = len(updated_cfg.get("channels", {}))
                mat = sum(1 for c in updated_cfg.get("channels", {}).values() if c.get("matched_story_name"))
                # Notify admin in chat
                try:
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text=f"✅ <b>Auto Storyfi Scan Complete!</b>\n\nFound <b>{tot}</b> channels (<b>{mat}</b> matched with stories).",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            except Exception as _e_sc:
                logger.error(f"[AutoStoryfi] Background scan error: {_e_sc}")

        asyncio.create_task(_do_scan_bg())
        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return

    if action == "channels":
        page = int(param) if param.isdigit() else 0
        channels_dict = config.get("channels", {})
        channels = list(channels_dict.values())
        per_page = 6
        total_pages = max(1, (len(channels) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))

        chunk = channels[page * per_page : (page + 1) * per_page]

        lines = [f"<b>📋 Connected Channels (Page {page + 1}/{total_pages})</b>\n──────────────────────"]
        btn_rows = []

        if not chunk:
            lines.append("<i>No channels found. Tap 'Auto-Scan Channels' first.</i>")
        else:
            for idx, ch in enumerate(chunk, 1):
                c_title = ch.get("title", "Unknown")[:25]
                s_name = ch.get("matched_story_name")
                display_s_name = s_name if s_name and s_name != "None" else "❌ No Story Match"
                is_en = "✅" if ch.get("enabled", True) else "❌"
                cid = ch.get("id")
                lines.append(f"<b>{idx}.</b> {c_title}\n   ↳ <i>Story:</i> <b>{display_s_name}</b> [{is_en}]")

                # Toggle button per channel
                toggle_cb = f"cfg|storyfi|chtoggle|{cid}|{caller_id}"
                btn_rows.append([
                    InlineKeyboardButton(f"{is_en} {c_title}", callback_data=toggle_cb)
                ])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◄ Prev", callback_data=f"cfg|storyfi|channels|{page - 1}||{caller_id}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"cfg|storyfi|noop||{caller_id}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ►", callback_data=f"cfg|storyfi|channels|{page + 1}||{caller_id}"))

        if nav_row:
            btn_rows.append(nav_row)
        btn_rows.append([InlineKeyboardButton("➦ Back", callback_data=f"cfg|storyfi|main||{caller_id}")])

        try:
            await query.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btn_rows))
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug(f"[AutoStoryfi] Channels view edit error: {e}")
        return

    if action == "chtoggle":
        cid_str = str(param)
        channels = config.get("channels", {})
        if cid_str in channels:
            channels[cid_str]["enabled"] = not channels[cid_str].get("enabled", True)
            save_storyfi_config(config)
            st = "Enabled" if channels[cid_str]["enabled"] else "Disabled"
            try: await query.answer(f"Channel {channels[cid_str].get('title')} {st}!", show_alert=True)
            except Exception: pass
        # return to channel list
        parts[2] = "channels"
        parts[3] = "0"
        await handle_storyfi_callbacks(query, context, parts, caller_id)
        return

    if action == "freq":
        # Cycle through 24h (1x), 12h (2x), 8h (3x), 6h (4x)
        current = config.get("interval_hours", 12.0)
        options = [24.0, 12.0, 8.0, 6.0]
        next_val = options[(options.index(current) + 1) % len(options)] if current in options else 12.0
        config["interval_hours"] = next_val
        save_storyfi_config(config)
        p_day = round(24.0 / next_val, 1)
        try: await query.answer(f"Frequency set to: {next_val} hours (~{p_day} posts/day)", show_alert=True)
        except Exception: pass
        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return

    if action == "stagger":
        # Cycle stagger minutes: 1-3 min -> 2-5 min -> 3-8 min -> 5-10 min
        current_pair = (config.get("stagger_min_minutes", 2), config.get("stagger_max_minutes", 5))
        presets = [(1, 3), (2, 5), (3, 8), (5, 10)]
        next_pair = presets[(presets.index(current_pair) + 1) % len(presets)] if current_pair in presets else (2, 5)
        config["stagger_min_minutes"], config["stagger_max_minutes"] = next_pair
        save_storyfi_config(config)
        try: await query.answer(f"Stagger gap set to: {next_pair[0]}–{next_pair[1]} Minutes between channels", show_alert=True)
        except Exception: pass
        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return

    if action == "tone":
        current_tone = config.get("reply_tone", "mixed")
        tones = ["mixed", "savage", "humour", "marketing", "mysterious"]
        next_tone = tones[(tones.index(current_tone) + 1) % len(tones)] if current_tone in tones else "mixed"
        config["reply_tone"] = next_tone
        save_storyfi_config(config)
        try: await query.answer(f"Reply Tone set to: {next_tone.capitalize()}", show_alert=True)
        except Exception: pass
        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return

    if action == "test_select":
        channels = list(config.get("channels", {}).values())
        matched = [c for c in channels if c.get("matched_story_name")]
        if not matched:
            try: await query.answer("No matched channels available. Run Auto-Scan first.", show_alert=True)
            except Exception: pass
            return

        text = "<b>🚀 Select a Channel for Test Post:</b>\n──────────────────────\n<i>Tap a channel below to immediately send a test post + auto-reply.</i>"
        btn_rows = []
        for c in matched[:6]:
            btn_rows.append([
                InlineKeyboardButton(f"🚀 {c.get('title')[:28]}", callback_data=f"cfg|storyfi|test_do|{c.get('id')}||{caller_id}")
            ])
        btn_rows.append([InlineKeyboardButton("➦ Back", callback_data=f"cfg|storyfi|main||{caller_id}")])

        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btn_rows))
        except Exception:
            pass
        return

    if action == "test_do":
        cid = int(param) if param.lstrip("-").isdigit() else 0
        c_data = config.get("channels", {}).get(str(cid))
        if not c_data:
            try: await query.answer("Channel not found in database.", show_alert=True)
            except Exception: pass
            return

        try: await query.answer(f"Sending test post to {c_data.get('title')}...", show_alert=True)
        except Exception: pass

        success, msg = await publish_story_to_channel(context.bot, cid, c_data, config)
        if success:
            try: await query.answer(f"✅ Test Post Sent Successfully!\n{msg}", show_alert=True)
            except Exception: pass
        else:
            try: await query.answer(f"❌ Failed: {msg}", show_alert=True)
            except Exception: pass

        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return
