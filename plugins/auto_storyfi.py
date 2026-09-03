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
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
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
    "watermark_position": "bottom_right",
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


_TITLE_CATALOG: Optional[List[Dict[str, Any]]] = None
_TITLE_CATALOG_TIME: float = 0.0


def _get_cached_story_catalog(force_refresh: bool = False) -> List[Dict[str, Any]]:
    global _TITLE_CATALOG, _TITLE_CATALOG_TIME
    now = time.time()
    if not force_refresh and _TITLE_CATALOG is not None and (now - _TITLE_CATALOG_TIME) < 60:
        return _TITLE_CATALOG

    catalog: List[Dict[str, Any]] = []
    try:
        from mongo_search import _get_mongo_client
        collection, client, _ = _get_mongo_client()
        if collection is not None:
            projection = {
                "_id": 1,
                "story_name_en": 1,
                "story_name_hi": 1,
                "story_name": 1,
                "title": 1,
                "name": 1,
                "aliases": 1
            }
            cursor = collection.find({}, projection).limit(3000)
            for d in cursor:
                sid = str(d.get("_id"))
                titles = []
                for f in ["story_name_en", "story_name_hi", "story_name", "title", "name"]:
                    val = d.get(f)
                    if val and isinstance(val, str) and val.strip():
                        titles.append(val.strip())
                aliases = d.get("aliases")
                if isinstance(aliases, list):
                    for a in aliases:
                        if a and isinstance(a, str):
                            titles.append(a.strip())
                elif isinstance(aliases, str):
                    titles.append(aliases.strip())
                
                primary_name = titles[0] if titles else "Unknown"
                catalog.append({
                    "_id": sid,
                    "primary_name": primary_name,
                    "titles": titles,
                    "titles_lower": [t.lower() for t in titles]
                })
            _TITLE_CATALOG = catalog
            _TITLE_CATALOG_TIME = now
            logger.info(f"[AutoStoryfi] Cached {len(catalog)} story titles from MongoDB.")
    except Exception as e:
        logger.debug(f"[AutoStoryfi] Title catalog fetch error: {e}")
    return catalog or []


def suggest_stories_for_channel(channel_title: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Returns top N story candidates from MongoDB catalog for a given channel title."""
    catalog = _get_cached_story_catalog()
    if not catalog or not channel_title:
        return []

    candidates = extract_candidate_titles(channel_title)
    if not candidates:
        candidates = [clean_channel_title_for_story(channel_title)]

    scored: List[Tuple[float, Dict[str, Any]]] = []
    from difflib import SequenceMatcher

    for item in catalog:
        best_item_score = 0.0
        for cand in candidates:
            cand_low = cand.lower().strip()
            cand_tokens = set(re.findall(r"\w+", cand_low))
            for t_low in item["titles_lower"]:
                t_tokens = set(re.findall(r"\w+", t_low))

                if cand_low == t_low:
                    best_item_score = max(best_item_score, 1.0)
                elif cand_low in t_low or t_low in cand_low:
                    best_item_score = max(best_item_score, 0.90)
                else:
                    if cand_tokens and t_tokens:
                        common = cand_tokens.intersection(t_tokens)
                        if common:
                            overlap = len(common) / max(len(cand_tokens), 1)
                            best_item_score = max(best_item_score, overlap * 0.85)

                    ratio = SequenceMatcher(None, cand_low, t_low).ratio()
                    best_item_score = max(best_item_score, ratio * 0.75)

        if best_item_score >= 0.35:
            scored.append((best_item_score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def find_story_for_channel_sync(channel_title: str, config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Finds matching story in MongoDB 'premium_stories' by title or fuzzy match.
    Uses ultra-fast in-memory title catalog with token-subset and fuzzy fallback.
    """
    if not channel_title or len(str(channel_title).strip()) < 2:
        return None

    candidates = extract_candidate_titles(channel_title)
    if not candidates:
        candidates = [clean_channel_title_for_story(channel_title)]

    # 1. Fast match against cached MongoDB title catalog in memory
    catalog = _get_cached_story_catalog()
    if catalog:
        from difflib import SequenceMatcher

        # Step 1A: Exact case-insensitive match
        for cand in candidates:
            cand_low = cand.lower().strip()
            if not cand_low:
                continue
            for item in catalog:
                for t_low in item["titles_lower"]:
                    if cand_low == t_low:
                        return _fetch_full_story_by_id_cached(item["_id"], item["primary_name"])

        # Step 1B: Token Subset Match (e.g. ['hukum', 'ikka'] in ['hukum', 'ka', 'ikka'])
        for cand in candidates:
            cand_low = cand.lower().strip()
            cand_tokens = [w for w in re.findall(r"\w+", cand_low) if len(w) >= 3]
            if not cand_tokens:
                continue
            for item in catalog:
                for t_low in item["titles_lower"]:
                    t_tokens = set(re.findall(r"\w+", t_low))
                    if all(ct in t_tokens for ct in cand_tokens) or (len(cand_tokens) >= 2 and sum(1 for ct in cand_tokens if ct in t_tokens) >= len(cand_tokens) - 1):
                        return _fetch_full_story_by_id_cached(item["_id"], item["primary_name"])

        # Step 1C: Word-boundary / Substring match (e.g. "Vashikaran" in "Vashikaran: Season 1")
        for cand in candidates:
            cand_low = cand.lower().strip()
            if len(cand_low) < 3:
                continue
            for item in catalog:
                for t_low in item["titles_lower"]:
                    if cand_low in t_low or t_low in cand_low:
                        return _fetch_full_story_by_id_cached(item["_id"], item["primary_name"])

        # Step 1D: Fuzzy ratio match (relaxed threshold >= 0.55 for slight typos/variations)
        for cand in candidates:
            cand_low = cand.lower().strip()
            if len(cand_low) < 4:
                continue
            best_item = None
            best_ratio = 0.0
            for item in catalog:
                for t_low in item["titles_lower"]:
                    ratio = SequenceMatcher(None, cand_low, t_low).ratio()
                    if ratio > best_ratio and ratio >= 0.55:
                        best_ratio = ratio
                        best_item = item
            if best_item:
                return _fetch_full_story_by_id_cached(best_item["_id"], best_item["primary_name"])

    # 2. Fallback to search_story_from_mongo_sync
    try:
        from mongo_search import search_story_from_mongo_sync
        for cand in candidates:
            res = search_story_from_mongo_sync(cand)
            if res:
                return res
    except Exception:
        pass

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


def _fetch_full_story_by_id_cached(story_id: str, fallback_title: str) -> Dict[str, Any]:
    """Helper to fetch full story doc or return structured minimal doc."""
    try:
        from mongo_search import _get_mongo_client
        from bson import ObjectId
        collection, client, settings = _get_mongo_client()
        if collection is not None:
            doc = collection.find_one({"_id": ObjectId(story_id)})
            if doc:
                doc["_id"] = str(doc["_id"])
                return doc
    except Exception:
        pass
    return {
        "_id": str(story_id),
        "story_name_en": fallback_title,
        "story_name": fallback_title,
        "title": fallback_title
    }


# ------------------------------------------------------------------------------
# 3. ENGAGING AUTO-REPLY MESSAGES LIBRARY (Hindi, Devanagari, Hinglish, English)
# ------------------------------------------------------------------------------

SAVAGE_REPLIES = [
    # Hinglish
    "🔥 '{story_name}' ka cliffhanger dekh kar hosh udd jayenge! Pure suspense ka baap hai 🤯 Must listen!",
    "⚠️ Warning: '{story_name}' ek baar sunna shuru kiya to bina complete kiye chain nahi aayega! Super addictive 🔥",
    "Plot twist aisa ki dimaag hil jaye 🤯 '{story_name}' nahi suni to asli maza miss kar rahe ho!",
    "Har episode me ek naya banger twist 🎧 Popcorn ready rakho, direct goosebumps aane wale hain!",
    "Is story ka villain aur storyline to alag hi level par hai 💀 Sabhi episodes abhi suno aur rate karo!",
    # Devanagari Hindi
    "🔥 '{story_name}' का क्लाइमेक्स और सस्पेंस होश उड़ा देने वाला है! 🤯 अगर नहीं सुना तो बहुत कुछ मिस कर रहे हो!",
    "⚠️ चेतावनी: '{story_name}' एक बार सुनना शुरू किया तो पूरी सुने बिना सो नहीं पाओगे! 🎧💥",
    "हर एपिसोड में इतना तगड़ा ट्विस्ट कि रोंगटे खड़े हो जाएं 🎧🍿 अभी सुनें सारे एपिसोड्स!",
    # Natural English
    "🔥 The suspense and insane plot twists in '{story_name}' are unmatched! An absolute masterpiece 🤯",
    "⚠️ High-octane drama! Once you start listening to '{story_name}', there's no going back 🎧💥",
]

HUMOUR_REPLIES = [
    # Hinglish
    "Doston popcorn ready rakho 🍿 '{story_name}' me aisa drama hai jo kahi aur nahi milega, guaranteed full entertainment!",
    "'{story_name}' itni spicy hai ki headphones pighal jayenge 🎧🌶️ Aaj ka quota complete karo dosto!",
    "Is character ke decisions dekh kar lagta hai meri life fir bhi sorted hai 😂 Super fun & thrilling audio!",
    "Bore ho rahe ho? Bas earphones lagao aur '{story_name}' shuru karo, time kab beet jayega pata bhi nahi chalega 🚀",
    # Devanagari Hindi
    "पॉपकॉर्न तैयार रखो दोस्तों 🍿 '{story_name}' में ड्रामा और ट्विस्ट का अलग ही लेवल है! 😂🎧",
    "ये कहानी इतनी एंटरटेनिंग है कि समय का पता ही नहीं चलेगा 🎧✨ ज़रूर सुनें!",
    # Natural English
    "Grab your headphones and snacks 🍿 '{story_name}' is pure non-stop entertainment from start to finish! 😂🎧",
    "The most hilarious yet thrilling audio series you'll listen to today! 🚀🎧",
]

MARKETING_REPLIES = [
    # Hinglish
    "🌟 Trending #1 Audio Series on Pocket FM! '{story_name}' ke sabhi episodes unlock ho chuke hain 🎧",
    "🚀 Instant Access Available! Neeche 'Buy Via App' ya 'Buy Via Bot' par tap karein aur abhi enjoy karein ✨",
    "🎧 Premium HD Sound Quality ke sath '{story_name}' suniye, complete story available hai!",
    "🔥 Don't miss out on today's highest-rated story series '{story_name}'! Tap buttons below to get started 🚀",
    # Devanagari Hindi
    "🌟 पॉकेट एफएम की नंबर 1 ट्रेंडिंग स्टोरी '{story_name}'! सभी लेटेस्ट एपिसोड्स अनलॉक हो चुके हैं 🎧✨",
    "🚀 नीचे दिए गए बटन्स पर टैप करें और '{story_name}' का पूरा ऑडियो तुरंत सुनें!",
    # Natural English
    "🌟 Ranked #1 Trending Story! All latest episodes of '{story_name}' are now ready for you 🎧",
    "🚀 Tap 'Buy Via App' or 'Buy Via Bot' below for instant listening access! ✨",
]

MYSTERIOUS_REPLIES = [
    # Hinglish
    "'{story_name}' ke peeche ek aisa gehra raaz chupa hai jiska koi andaza bhi nahi laga sakta... 🤫 Ek baar sunkar dekho!",
    "Har mod par ek naya mystery khulta hai 🔮 '{story_name}' ka ending dekh kar hairan reh jaoge!",
    "Kya sach me wahi sach tha jo dikh raha tha? 🕵️‍♂️ '{story_name}' suniye aur khud pata lagaiye!",
    # Devanagari Hindi
    "'{story_name}' के पीछे एक ऐसा गहरा रहस्य छिपा है जिसका कोई अंदाज़ा नहीं लगा सकता... 🤫 ज़रूर सुनें!",
    "हर मोड़ पर एक नया रहस्य 🔮 '{story_name}' का अंत देखकर आप हैरान रह जाएंगे!",
    # Natural English
    "Behind '{story_name}' lies a secret no one could have anticipated... 🤫 Unravel the mystery now!",
    "A dark, gripping mystery that will keep you guessing until the very last second 🔮🎧",
]


def get_random_engaging_reply(story_name: str, tone: str = "mixed") -> str:
    """Generates an engaging reply message tailored to the chosen tone with Hindi, Hinglish, Devanagari and English."""
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

    template = random.choice(pool)
    return template.format(story_name=story_name or "Story")


# ------------------------------------------------------------------------------
# 4. POST GENERATION & AUTO-REPLACEMENT PUBLISHER
# ------------------------------------------------------------------------------

async def publish_story_to_channel(bot, chat_id: int, channel_info: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Builds poster card with storyfiwatermark.png (top_right), sends to channel,
    sends engaging reply, and deletes previous post + previous reply.
    Supports multi-story assignment with round-robin rotation.
    """
    matched_list = channel_info.get("matched_stories")
    if not matched_list:
        s_id = channel_info.get("matched_story_id")
        s_name = channel_info.get("matched_story_name")
        if s_id or s_name:
            matched_list = [{"id": str(s_id or ""), "name": s_name or ""}]
        else:
            clean_title = channel_info.get("cleaned_title") or clean_channel_title_for_story(channel_info.get("title", ""))
            auto_s = find_story_for_channel_sync(channel_info.get("title", ""), config)
            if auto_s:
                s_name_found = auto_s.get("story_name_en") or auto_s.get("story_name") or auto_s.get("title") or clean_title
                matched_list = [{"id": str(auto_s.get("_id", "")), "name": s_name_found}]

    story = None
    if matched_list:
        rot_idx = int(channel_info.get("story_rotation_idx", 0))
        selected_entry = matched_list[rot_idx % len(matched_list)]
        channel_info["story_rotation_idx"] = (rot_idx + 1) % max(1, len(matched_list))
        channel_info["matched_stories"] = matched_list
        channel_info["matched_story_name"] = selected_entry.get("name")
        channel_info["matched_story_id"] = selected_entry.get("id")
        
        # Save updated rotation index into config
        config["channels"][str(chat_id)] = channel_info
        save_storyfi_config(config)
        
        story = _fetch_full_story_by_id_cached(selected_entry.get("id", ""), selected_entry.get("name", ""))

    if not story:
        clean_title = channel_info.get("cleaned_title") or clean_channel_title_for_story(channel_info.get("title", ""))
        logger.warning(f"[AutoStoryfi] No matching story found for channel '{channel_info.get('title')}' (clean='{clean_title}')")
        if config.get("notify_owner_on_missing", True):
            try:
                alert_text = (
                    f"⚠️ <b>[Auto Storyfi Alert]</b>\n\n"
                    f"Channel: <b>{channel_info.get('title')}</b> (<code>{chat_id}</code>)\n"
                    f"Extracted Title: <code>{clean_title}</code>\n"
                    f"❌ No matching story found in MongoDB or Local Database. Skipping post."
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
        "Story"
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
        logger.error(f"[AutoStoryfi] Error sending post to channel {chat_id}: {e}")
        return False, str(e)

    if not sent_post:
        return False, "Failed to send main post"

    # 7. Send Engaging Auto-Reply (Tagging / Replying to Post)
    sent_reply = None
    try:
        reply_tone = config.get("reply_tone", "mixed")
        reply_text = get_random_engaging_reply(story_name, reply_tone)

        sent_reply = await bot.send_message(
            chat_id=chat_id,
            text=reply_text,
            parse_mode="HTML",
            reply_to_message_id=sent_post.message_id
        )
    except Exception as _e_rep:
        logger.warning(f"[AutoStoryfi] Auto-reply send error in {chat_id}: {_e_rep}")

    # 8. Update channel stats in config
    channel_info["last_post_msg_id"] = sent_post.message_id
    channel_info["last_reply_msg_id"] = sent_reply.message_id if sent_reply else None
    channel_info["last_post_time"] = time.time()
    config["channels"][str(chat_id)] = channel_info
    save_storyfi_config(config)

    logger.info(f"[AutoStoryfi] ✅ Successfully published '{story_name}' to channel {chat_id} (msg_id={sent_post.message_id})")
    return True, f"Posted '{story_name}' successfully!"


# ------------------------------------------------------------------------------
# 5. ADMIN CHANNEL SCANNER & AUTO-MAPPER & UNLINKED STORIES
# ------------------------------------------------------------------------------

def get_unlinked_mongodb_stories() -> List[Dict[str, Any]]:
    """
    Returns all stories from MongoDB catalog that are NOT yet linked
    to any channel in Auto Storyfi config.
    """
    catalog = _get_cached_story_catalog()
    config = load_storyfi_config()
    channels = config.get("channels", {})

    assigned_story_ids = set()
    assigned_story_names = set()

    for c in channels.values():
        for s in c.get("matched_stories") or []:
            if s.get("id"):
                assigned_story_ids.add(str(s["id"]))
            if s.get("name"):
                assigned_story_names.add(str(s["name"]).lower().strip())
        if c.get("matched_story_id"):
            assigned_story_ids.add(str(c["matched_story_id"]))
        if c.get("matched_story_name"):
            assigned_story_names.add(str(c["matched_story_name"]).lower().strip())

    unlinked = []
    for item in catalog:
        sid = str(item.get("_id", ""))
        p_name = item.get("primary_name", "").strip()
        p_name_low = p_name.lower()

        if sid in assigned_story_ids:
            continue
        if p_name_low in assigned_story_names:
            continue
        # Also check aliases/titles
        if any(t in assigned_story_names for t in item.get("titles_lower", [])):
            continue

        unlinked.append(item)

    return unlinked


def get_unlinked_stories_panel(page: int, caller_id: int, page_size: int = 15) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Renders clean paginated view of MongoDB stories that have not been connected to any Auto Storyfi channel yet.
    """
    unlinked = get_unlinked_mongodb_stories()
    total_unlinked = len(unlinked)
    
    tg_book = '<tg-emoji emoji-id="6021435576513730578">📚</tg-emoji>'
    tg_check = '<tg-emoji emoji-id="5454074580010295588">✅</tg-emoji>'
    tg_tip = '<tg-emoji emoji-id="5805406692984232989">📌</tg-emoji>'

    if total_unlinked == 0:
        text = (
            f"{tg_check} <b>All MongoDB Stories Are Linked!</b>\n"
            "──────────────────────\n"
            "Every single story in your MongoDB database is currently assigned to an Auto Storyfi channel."
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Panel", callback_data=f"cfg|storyfi|main||{caller_id}")]]
        return text, InlineKeyboardMarkup(keyboard)

    total_pages = max(1, (total_unlinked + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_unlinked)
    current_batch = unlinked[start_idx:end_idx]

    lines = [
        f"{tg_book} <b>Unlinked Stories Catalog</b>",
        "──────────────────────",
        f"• <b>Pending Stories :</b> <code>{total_unlinked}</code>",
        f"• <b>Page :</b> <code>{page + 1}/{total_pages}</code>",
        "──────────────────────",
    ]

    for i, s in enumerate(current_batch, start=start_idx + 1):
        s_name = s.get("primary_name", "Unknown")
        lines.append(f"<b>{i}.</b> <code>{s_name}</code>")

    lines.append("──────────────────────")
    lines.append(
        f"{tg_tip} <b>How to link channels for these stories:</b>\n"
        "• Send: <code>/addchannels @channel1 @channel2 -100xxxx</code>\n"
        "• Or <b>forward any post</b> from your channel directly to Riya Bot in PM!"
    )

    text = "\n".join(lines)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cfg|storyfi|unlinked|{page - 1}||{caller_id}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data=f"cfg|storyfi|noop||{caller_id}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cfg|storyfi|unlinked|{page + 1}||{caller_id}"))

    keyboard = [
        nav_row,
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"cfg|storyfi|unlinked|{page}||{caller_id}"),
            InlineKeyboardButton("🔙 Back to Panel", callback_data=f"cfg|storyfi|main||{caller_id}")
        ]
    ]

    return text, InlineKeyboardMarkup(keyboard)


async def register_single_channel_storyfi(bot, chat_target: Any, chat_title: Optional[str] = None, username: Optional[str] = None) -> Tuple[bool, str]:
    """
    Registers or updates a single channel in Auto Storyfi.
    Accepts @username, t.me link, or channel ID.
    Verifies bot admin status & permissions, auto-matches MongoDB story, and saves config.
    """
    config = load_storyfi_config()
    discovered = config.get("channels", {})

    target_str = str(chat_target).strip()
    # Normalize t.me links
    if "t.me/" in target_str:
        target_str = target_str.split("t.me/")[-1].split("/")[0].strip()
        if not target_str.startswith("@") and not target_str.startswith("+"):
            target_str = f"@{target_str}"

    # Normalize pure numbers
    if target_str.lstrip("-").isdigit():
        raw_int = int(target_str)
        if raw_int > 0 and not target_str.startswith("100"):
            target_int = int(f"-100{raw_int}")
        elif raw_int > 0 and target_str.startswith("100"):
            target_int = int(f"-{raw_int}")
        else:
            target_int = raw_int
        target_resolved = target_int
    else:
        target_resolved = target_str if target_str.startswith("@") else f"@{target_str}"

    chat = None
    # 1. Try directly with Bot API
    try:
        chat = await bot.get_chat(target_resolved)
    except Exception as e_bot:
        logger.debug(f"[AutoStoryfi] Bot API get_chat({target_resolved}) error: {e_bot}")
        # 2. Try resolving with Telethon Userbot if available
        tele_client, should_disc = await _get_connected_telethon_client()
        if tele_client:
            try:
                from telethon.utils import get_peer_id
                entity = await tele_client.get_entity(target_resolved)
                peer_id = get_peer_id(entity)
                chat = await bot.get_chat(peer_id)
            except Exception as e_tele:
                logger.debug(f"[AutoStoryfi] Telethon get_entity({target_resolved}) error: {e_tele}")
            finally:
                if should_disc:
                    try: await tele_client.disconnect()
                    except Exception: pass

    if not chat:
        return False, f"Could not find or access channel <code>{chat_target}</code>. Make sure the username/ID is valid and Riya Bot is added to the channel."

    if chat.type not in ("channel", "supergroup"):
        return False, f"<code>{chat.title or chat_target}</code> is not a broadcast channel."

    str_cid = str(chat.id)
    c_title = chat.title or chat_title or str_cid
    c_user = chat.username or username or ""

    try:
        member = await bot.get_chat_member(chat.id, bot.id)
    except Exception as e_mem:
        return False, f"Bot is not in <b>{c_title}</b> (Error: {e_mem}). Please add <b>@Riya_Stories_Bot</b> as Admin."

    if member.status not in ("administrator", "creator"):
        return False, f"Bot is not an Admin in <b>{c_title}</b>. Please promote Riya Bot to Admin."

    can_post = getattr(member, "can_post_messages", True)
    if not can_post:
        return False, f"Bot does not have 'Post Messages' permission in <b>{c_title}</b>."

    existing = discovered.get(str_cid)
    if existing:
        # Preserve existing assigned stories
        existing["title"] = c_title
        existing["username"] = c_user
        config["channels"][str_cid] = existing
        save_storyfi_config(config)
        m_count = len(existing.get("matched_stories") or [])
        return True, f"<b>{c_title}</b> (Already in database, {m_count} stories preserved)"

    # Match MongoDB story
    clean_title = clean_channel_title_for_story(c_title)
    matched_story = find_story_for_channel_sync(c_title, config)

    m_stories = []
    if matched_story:
        s_name = matched_story.get("story_name_en") or matched_story.get("story_name") or matched_story.get("title") or clean_title
        m_stories = [{
            "id": str(matched_story.get("_id", "")),
            "name": s_name
        }]

    discovered[str_cid] = {
        "id": chat.id,
        "title": c_title,
        "username": c_user,
        "cleaned_title": clean_title,
        "matched_stories": m_stories,
        "matched_story_name": m_stories[0]["name"] if m_stories else None,
        "matched_story_id": m_stories[0]["id"] if m_stories else None,
        "story_rotation_idx": 0,
        "last_post_msg_id": None,
        "last_reply_msg_id": None,
        "last_post_time": 0,
        "enabled": True,
    }

    config["channels"] = discovered
    save_storyfi_config(config)
    m_info = f"Matched: <b>{m_stories[0]['name']}</b>" if m_stories else "❌ No story auto-matched (can add via panel)"
    logger.info(f"[AutoStoryfi] Successfully added channel '{c_title}' ({chat.id}) | {m_info}")
    return True, f"<b>{c_title}</b> ➔ {m_info}"


async def bulk_add_channels_storyfi(bot, channel_identifiers: List[str]) -> str:
    """
    Accepts a list of @usernames, links, or chat IDs, checks bot permissions,
    auto-matches MongoDB stories, and saves them to Auto Storyfi config.
    """
    results = []
    added_count = 0
    # Flatten items if space or newline separated
    flat_items = []
    for raw in channel_identifiers:
        for chunk in re.split(r"[\s,\n]+", raw):
            c_clean = chunk.strip().rstrip(",")
            if c_clean and c_clean not in flat_items:
                flat_items.append(c_clean)

    for item in flat_items:
        try:
            ok, msg = await register_single_channel_storyfi(bot, item)
            if ok:
                added_count += 1
                results.append(f"✅ {msg}")
            else:
                results.append(f"❌ <code>{item}</code>: {msg}")
        except Exception as e:
            results.append(f"❌ <code>{item}</code>: {e}")

    summary = (
        "<b>📋 Auto Storyfi Batch Import Results</b>\n"
        "──────────────────────\n"
        f"• <b>Total Channels Processed:</b> {len(flat_items)}\n"
        f"• <b>Successfully Added:</b> {added_count}\n"
        "──────────────────────\n"
    )
    summary += "\n".join(results[:30])
    if len(results) > 30:
        summary += f"\n<i>...and {len(results) - 30} more channels</i>"
    return summary


async def _get_connected_telethon_client():
    """
    Returns a connected Telethon client using the existing link_checker instance
    or instantiating a new client with SESSION_STRING.
    """
    try:
        from link_checker import link_checker
        if link_checker.client and link_checker.client.is_connected() and await link_checker.client.is_user_authorized():
            return link_checker.client, False
    except Exception:
        pass

    try:
        from config import API_ID, API_HASH, SESSION_STRING
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        if SESSION_STRING and API_ID and API_HASH:
            client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                return client, True
    except Exception as e:
        logger.error(f"[AutoStoryfi] Telethon client connection failed: {e}")
    return None, False


async def scan_and_sync_admin_channels(bot, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """
    Scans all dialogs where the bot or userbot is present.
    CRITICAL RULE:
    - Already added channels in config are NEVER modified, re-scanned, or deleted.
    - Only newly discovered channels that are NOT yet in config are scanned and added.
    """
    config = load_storyfi_config()
    discovered = config.get("channels", {})
    blacklisted = set(config.get("blacklisted_channels", []))

    # Pre-warm catalog
    _get_cached_story_catalog()

    known_channel_ids = set()

    # 1. Collect from story_channels_db
    try:
        from database import story_channels_db
        for cat_list in story_channels_db.values():
            if isinstance(cat_list, list):
                for cid in cat_list:
                    if isinstance(cid, int) or (isinstance(cid, str) and cid.lstrip("-").isdigit()):
                        known_channel_ids.add(int(cid))
    except Exception as e:
        logger.debug(f"[AutoStoryfi Scan] Local DB scan note: {e}")

    # 2. Collect from channels.json (channel_manager)
    try:
        from channel_manager import load_channels
        c_list = load_channels()
        for cid in c_list:
            if isinstance(cid, int) or (isinstance(cid, str) and str(cid).lstrip("-").isdigit()):
                known_channel_ids.add(int(cid))
    except Exception as e:
        logger.debug(f"[AutoStoryfi Scan] channels.json scan note: {e}")

    # 3. Collect from poster_settings.json
    try:
        from plugins.poster_bot import load_poster_settings
        p_sets = load_poster_settings()
        for p_cid in p_sets.get("channels", []):
            if isinstance(p_cid, int) or (isinstance(p_cid, str) and str(p_cid).lstrip("-").isdigit()):
                known_channel_ids.add(int(p_cid))
        sel_cid = p_sets.get("selected_channel_id")
        if sel_cid and (isinstance(sel_cid, int) or (isinstance(sel_cid, str) and str(sel_cid).lstrip("-").isdigit())):
            known_channel_ids.add(int(sel_cid))
    except Exception as e:
        logger.debug(f"[AutoStoryfi Scan] poster_settings scan note: {e}")

    # 4. Collect from Telethon across ALL dialogs (limit=None, no is_admin restriction!)
    tele_client, should_disconnect = await _get_connected_telethon_client()
    if tele_client:
        try:
            from telethon.utils import get_peer_id
            dialogs = await tele_client.get_dialogs(limit=None)
            logger.info(f"[AutoStoryfi Scan] Fetched {len(dialogs)} dialogs from Telethon Userbot account.")
            for d in dialogs:
                if d.is_channel or d.is_group:
                    try:
                        peer_id = get_peer_id(d.input_entity)
                        known_channel_ids.add(peer_id)
                    except Exception:
                        entity = d.entity
                        raw_id = getattr(entity, "id", None)
                        if raw_id:
                            cid = raw_id if str(raw_id).startswith("-100") else (-1000000000000 - raw_id if raw_id > 0 else raw_id)
                            known_channel_ids.add(cid)
        except Exception as e:
            logger.error(f"[AutoStoryfi Scan] Telethon scan error: {e}")
        finally:
            if should_disconnect:
                try:
                    await tele_client.disconnect()
                except Exception:
                    pass
    else:
        logger.warning("[AutoStoryfi Scan] No active Telethon client available for dialog scanning.")

    # Filter strictly NEW channels that are not already in discovered and not blacklisted
    new_channel_ids = [
        cid for cid in known_channel_ids
        if str(cid) not in discovered and cid not in blacklisted and str(cid) not in blacklisted
    ]

    logger.info(f"[AutoStoryfi Scan] {len(discovered)} existing channels preserved untouched. Scanning {len(new_channel_ids)} newly discovered candidate channels...")

    # 5. Verify Bot API permissions and add NEW channels only
    newly_added = 0
    for cid in new_channel_ids:
        str_cid = str(cid)
        try:
            chat = await bot.get_chat(cid)
            if chat.type in ("channel", "supergroup"):
                member = await bot.get_chat_member(cid, bot.id)
                if member.status in ("administrator", "creator"):
                    can_post = getattr(member, "can_post_messages", True)
                    if can_post:
                        title = chat.title or str_cid
                        username = chat.username or ""
                        clean_title = clean_channel_title_for_story(title)
                        matched_story = find_story_for_channel_sync(title, config)

                        m_stories = []
                        if matched_story:
                            m_stories = [{
                                "id": str(matched_story.get("_id", "")),
                                "name": matched_story.get("story_name_en") or matched_story.get("story_name") or matched_story.get("title") or clean_title
                            }]

                        discovered[str_cid] = {
                            "id": chat.id,
                            "title": title,
                            "username": username,
                            "cleaned_title": clean_title,
                            "matched_stories": m_stories,
                            "matched_story_name": m_stories[0]["name"] if m_stories else None,
                            "matched_story_id": m_stories[0]["id"] if m_stories else None,
                            "story_rotation_idx": 0,
                            "last_post_msg_id": None,
                            "last_reply_msg_id": None,
                            "last_post_time": 0,
                            "enabled": True,
                        }
                        newly_added += 1
        except Exception as _e_chk:
            logger.debug(f"[AutoStoryfi Scan] Channel {cid} check error: {_e_chk}")

    config["channels"] = discovered
    save_storyfi_config(config)
    logger.info(f"[AutoStoryfi Scan] Scan complete: {newly_added} new channels added, {len(discovered)} total channels active.")
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

    last_auto_sync_time = 0.0
    while True:
        try:
            now = time.time()
            config = load_storyfi_config()
            if not config.get("enabled", False):
                await asyncio.sleep(60)
                continue

            # Auto-sync admin channels and newly added stories every 30 minutes in background
            if (now - last_auto_sync_time) > 1800:
                try:
                    logger.info("[AutoStoryfi] Running automatic background channel & new story sync...")
                    await scan_and_sync_admin_channels(application.bot, None)
                    last_auto_sync_time = now
                except Exception as _e_sc_bg:
                    logger.debug(f"[AutoStoryfi Auto-Sync] note: {_e_sc_bg}")

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

async def handle_storyfi_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles admin text input when searching for a story to add to an Auto Storyfi channel.
    Uses cached MongoDB catalog + fuzzy search and returns interactive inline keyboard.
    """
    if not update.message or not update.message.text:
        return False

    text = update.message.text.strip()
    if not context.user_data or not context.user_data.get("storyfi_search_waiting"):
        return False

    context.user_data.pop("storyfi_search_waiting", None)
    cid_str = str(context.user_data.pop("storyfi_search_cid", ""))
    caller_id = update.effective_user.id if update.effective_user else 0

    config = load_storyfi_config()
    c_data = config.get("channels", {}).get(cid_str, {})
    c_title = c_data.get("title", "Channel")

    if text.lower() in ["❌ cancel", "cancel", "/cancel"]:
        await update.message.reply_text(
            f"❌ Story search cancelled for <b>{c_title}</b>.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        return True

    catalog = _get_cached_story_catalog()
    query_low = text.lower().strip()

    scored_results = []
    from difflib import SequenceMatcher
    for s_idx, item in enumerate(catalog):
        s_score = 0.0
        for t_low in item["titles_lower"]:
            if query_low == t_low:
                s_score = max(s_score, 1.0)
            elif query_low in t_low:
                s_score = max(s_score, 0.90)
            elif t_low in query_low:
                s_score = max(s_score, 0.85)
            else:
                q_tokens = set(re.findall(r"\w+", query_low))
                t_tokens = set(re.findall(r"\w+", t_low))
                if q_tokens and t_tokens:
                    common = q_tokens.intersection(t_tokens)
                    if common:
                        s_score = max(s_score, (len(common) / len(q_tokens)) * 0.80)
                ratio = SequenceMatcher(None, query_low, t_low).ratio()
                if ratio >= 0.50:
                    s_score = max(s_score, ratio * 0.70)

        if s_score >= 0.35:
            scored_results.append((s_score, s_idx, item))

    scored_results.sort(key=lambda x: x[0], reverse=True)
    top_matches = scored_results[:15]

    if not top_matches:
        btn_rows = [
            [InlineKeyboardButton("🔍 Try Another Search", callback_data=f"cfg|storyfi|promptsearch|{cid_str}||{caller_id}")],
            [InlineKeyboardButton("📚 Browse All 298 Stories", callback_data=f"cfg|storyfi|chbrowse|{cid_str}|0|{caller_id}")],
            [InlineKeyboardButton("➦ Back to Channel Settings", callback_data=f"cfg|storyfi|chdetail|{cid_str}|0|{caller_id}")]
        ]
        await update.message.reply_text(
            f"❌ <b>No stories found for '<code>{text}</code>'</b>\n\nTarget Channel: <b>{c_title}</b>\nTry a different keyword or browse all stories:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text(
            "Choose an option below:",
            reply_markup=InlineKeyboardMarkup(btn_rows)
        )
        return True

    btn_rows = []
    for _, s_idx, item in top_matches:
        s_name = item.get("primary_name", "Unknown")[:26]
        btn_rows.append([
            InlineKeyboardButton(f"➕ {s_name}", callback_data=f"cfg|storyfi|doadd|{cid_str}|{s_idx}|{caller_id}")
        ])

    btn_rows.append([
        InlineKeyboardButton("🔍 Search Again", callback_data=f"cfg|storyfi|promptsearch|{cid_str}||{caller_id}"),
        InlineKeyboardButton("➦ Channel Settings", callback_data=f"cfg|storyfi|chdetail|{cid_str}|0|{caller_id}")
    ])

    await update.message.reply_text(
        f"✅ <b>Search Results for '<code>{text}</code>'</b> ({len(top_matches)} stories found)\n"
        f"Target Channel: <b>{c_title}</b>\n"
        f"──────────────────────\n"
        f"Tap any story below to add it to this channel:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(btn_rows)
    )
    # Clean remove reply keyboard silently
    try:
        dummy_msg = await update.message.reply_text("🔎 Select from the list above.", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await asyncio.sleep(2)
        await dummy_msg.delete()
    except Exception:
        pass
    return True


def get_storyfi_main_panel(caller_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    """Builds the main Auto Storyfi admin panel text & keyboard with custom emojis and clean styling."""
    config = load_storyfi_config()
    is_on = config.get("enabled", False)
    interval_h = config.get("interval_hours", 12.0)
    posts_per_day = round(24.0 / interval_h, 1) if interval_h > 0 else 0

    stagger_min = config.get("stagger_min_minutes", 2)
    stagger_max = config.get("stagger_max_minutes", 5)
    tone = config.get("reply_tone", "mixed").capitalize()

    channels = config.get("channels", {})
    total_ch = len(channels)
    active_ch = sum(1 for c in channels.values() if c.get("enabled", True))
    matched_ch = sum(1 for c in channels.values() if c.get("matched_story_name"))
    unlinked_stories = get_unlinked_mongodb_stories()
    unlinked_count = len(unlinked_stories)

    tg_engine = '<tg-emoji emoji-id="6021607757457660145">⚙️</tg-emoji>'
    tg_active = '<tg-emoji emoji-id="5454074580010295588">✅</tg-emoji>'
    tg_stopped = '<tg-emoji emoji-id="5893192487324880883">⛔</tg-emoji>'
    tg_freq = '<tg-emoji emoji-id="5807879906951960923">⏳</tg-emoji>'
    tg_stagger = '<tg-emoji emoji-id="5258113901106580375">⏱️</tg-emoji>'
    tg_tone = '<tg-emoji emoji-id="5296790785981718487">🎙</tg-emoji>'
    tg_channel = '<tg-emoji emoji-id="6019455905827920171">📱</tg-emoji>'
    tg_matched = '<tg-emoji emoji-id="5465432711218863135">♨️</tg-emoji>'
    tg_unlinked = '<tg-emoji emoji-id="6021435576513730578">📚</tg-emoji>'

    status_str = f"{tg_active} <b>Activated</b>" if is_on else f"{tg_stopped} <b>Deactivated</b>"

    text = (
        f"{tg_engine} <b>Auto Storyfi Engine</b>\n"
        "──────────────────────\n"
        f"• <b>Status :</b> {status_str}\n"
        f"• <b>Frequency :</b> <code>{interval_h}h</code> (<b>{posts_per_day} Posts / Day</b>)\n"
        f"• <b>Stagger Gap :</b> <code>{stagger_min}–{stagger_max} Minutes</code>\n"
        f"• <b>Reply Tone :</b> <code>{tone}</code>\n"
        f"• <b>Connected Channels :</b> <code>{active_ch}/{total_ch} Active</code>\n"
        f"• <b>Matched Channels :</b> <code>{matched_ch}/{total_ch} Ready</code>\n"
        f"• <b>Unlinked Stories :</b> <code>{unlinked_count} Available</code>\n"
        "──────────────────────"
    )

    toggle_btn_text = "🔴 Deactivate" if is_on else "🟢 Activate"

    keyboard = [
        [
            InlineKeyboardButton(toggle_btn_text, callback_data=f"cfg|storyfi|toggle||{caller_id}"),
            InlineKeyboardButton("🔍 Auto Scan", callback_data=f"cfg|storyfi|scan||{caller_id}")
        ],
        [
            InlineKeyboardButton("📋 Channels List", callback_data=f"cfg|storyfi|channels|0||{caller_id}"),
            InlineKeyboardButton("📖 Unlinked Stories", callback_data=f"cfg|storyfi|unlinked|0||{caller_id}")
        ],
        [
            InlineKeyboardButton(f"⏱️ Frequency : {interval_h}h ({posts_per_day}x / Day)", callback_data=f"cfg|storyfi|freq||{caller_id}")
        ],
        [
            InlineKeyboardButton(f"⏳ Stagger Gap : {stagger_min}–{stagger_max} Min", callback_data=f"cfg|storyfi|stagger||{caller_id}")
        ],
        [
            InlineKeyboardButton(f"💬 Reply Tone : {tone}", callback_data=f"cfg|storyfi|tone||{caller_id}")
        ],
        [
            InlineKeyboardButton("🚀 Test It", callback_data=f"cfg|storyfi|test_select||{caller_id}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Config", callback_data=f"cfg|main||{caller_id}")
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

    if action == "unlinked":
        page = int(param) if param.isdigit() else 0
        text, markup = get_unlinked_stories_panel(page, caller_id)
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
        per_page = 15
        total_pages = max(1, (len(channels) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))

        chunk = channels[page * per_page : (page + 1) * per_page]

        lines = [f"<b>📋 Connected Channels (Page {page + 1}/{total_pages})</b>\n──────────────────────\nTap any channel below to manage its assigned stories & settings:\n"]
        btn_rows = []

        if not chunk:
            lines.append("No channels found. Tap 'Auto-Scan Channels' first.")
        else:
            for idx, ch in enumerate(chunk, page * per_page + 1):
                c_title = ch.get("title", "Unknown")[:24]
                m_stories = ch.get("matched_stories")
                if not m_stories:
                    s_name = ch.get("matched_story_name")
                    if s_name and s_name != "None":
                        m_stories = [{"name": s_name}]
                    else:
                        m_stories = []

                story_names = [s.get("name") for s in m_stories if s.get("name")]
                if story_names:
                    s_summary = ", ".join(story_names[:2]) + (f" (+{len(story_names)-2})" if len(story_names) > 2 else "")
                else:
                    s_summary = "❌ No Story Match"

                is_en = "🟢" if ch.get("enabled", True) else "🔴"
                cid = ch.get("id")
                lines.append(f"<b>{idx}.</b> {c_title} [{is_en}]\n   ↳ Stories ({len(story_names)}): <b>{s_summary}</b>")

                # Channel Detail button (2 per row if short or 1 per row)
                toggle_cb = f"cfg|storyfi|chdetail|{cid}|{page}|{caller_id}"
                btn_rows.append([
                    InlineKeyboardButton(f"{is_en} {idx}. {c_title} ({len(story_names)} 📚)", callback_data=toggle_cb)
                ])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◄ Prev", callback_data=f"cfg|storyfi|channels|{page - 1}||{caller_id}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"cfg|storyfi|noop||{caller_id}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ►", callback_data=f"cfg|storyfi|channels|{page + 1}||{caller_id}"))

        if nav_row:
            btn_rows.append(nav_row)
        btn_rows.append([InlineKeyboardButton("🔙 Back to Panel", callback_data=f"cfg|storyfi|main||{caller_id}")])

        try:
            await query.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btn_rows))
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug(f"[AutoStoryfi] Channels view edit error: {e}")
        return

    if action == "chdetail":
        cid_str = str(param)
        return_page = parts[4] if len(parts) > 4 and parts[4].isdigit() else "0"
        channels = config.get("channels", {})
        c_data = channels.get(cid_str)
        if not c_data:
            try: await query.answer("Channel not found.", show_alert=True)
            except Exception: pass
            parts[2] = "channels"
            parts[3] = return_page
            await handle_storyfi_callbacks(query, context, parts, caller_id)
            return

        c_title = c_data.get("title", "Unknown")
        is_en = c_data.get("enabled", True)
        st_txt = "🟢 Active (Enabled)" if is_en else "🔴 Inactive (Disabled)"

        m_stories = c_data.get("matched_stories")
        if not m_stories:
            s_name = c_data.get("matched_story_name")
            s_id = c_data.get("matched_story_id")
            if s_name and s_name != "None":
                m_stories = [{"id": str(s_id or ""), "name": s_name}]
            else:
                m_stories = []
            c_data["matched_stories"] = m_stories

        rot_idx = int(c_data.get("story_rotation_idx", 0))

        lines = [
            f"<b>📢 Channel Settings</b>",
            f"──────────────────────",
            f"• <b>Title:</b> <code>{c_title}</code>",
            f"• <b>Channel ID:</b> <code>{cid_str}</code>",
            f"• <b>Status:</b> <b>{st_txt}</b>",
            f"• <b>Assigned Stories:</b> <code>{len(m_stories)} Assigned</code>",
            f"──────────────────────"
        ]

        if not m_stories:
            lines.append("❌ No stories assigned yet.\n")
            suggestions = suggest_stories_for_channel(c_title, limit=3)
            if suggestions:
                lines.append("<b>💡 Top Database Suggestions:</b>")
                for sug_i, sug_obj in enumerate(suggestions, 1):
                    lines.append(f"  <b>{sug_i}.</b> 📖 <code>{sug_obj.get('primary_name')}</code>")
        else:
            lines.append("<b>📚 Assigned Stories (Rotating):</b>")
            for s_i, s_obj in enumerate(m_stories):
                is_next = " <b>[NEXT 🚀]</b>" if s_i == (rot_idx % len(m_stories)) else ""
                lines.append(f"  <b>{s_i + 1}.</b> 📖 <b>{s_obj.get('name')}</b>{is_next}")

        btn_rows = []
        toggle_label = "🔴 Disable Channel" if is_en else "🟢 Enable Channel"
        btn_rows.append([
            InlineKeyboardButton(toggle_label, callback_data=f"cfg|storyfi|chtoggle|{cid_str}|{return_page}|{caller_id}"),
            InlineKeyboardButton("🔍 Search Story", callback_data=f"cfg|storyfi|promptsearch|{cid_str}||{caller_id}")
        ])

        # Quick assign suggestions if 0 stories
        if not m_stories:
            suggestions = suggest_stories_for_channel(c_title, limit=3)
            catalog = _get_cached_story_catalog()
            for sug in suggestions:
                try:
                    s_cat_idx = catalog.index(sug)
                except Exception:
                    s_cat_idx = next((i for i, c_it in enumerate(catalog) if c_it.get("_id") == sug.get("_id")), 0)
                btn_rows.append([
                    InlineKeyboardButton(f"💡 Quick Add: {sug.get('primary_name')[:22]}", callback_data=f"cfg|storyfi|doadd|{cid_str}|{s_cat_idx}|{caller_id}")
                ])

        btn_rows.append([
            InlineKeyboardButton("📚 Browse All 298 Stories", callback_data=f"cfg|storyfi|chbrowse|{cid_str}|0|{caller_id}")
        ])

        # Story remove buttons
        if m_stories:
            for s_i, s_obj in enumerate(m_stories):
                btn_rows.append([
                    InlineKeyboardButton(f"🗑️ Remove: {s_obj.get('name')[:24]}", callback_data=f"cfg|storyfi|chdel|{cid_str}|{s_i}|{caller_id}")
                ])

        btn_rows.append([
            InlineKeyboardButton("🚀 Test It", callback_data=f"cfg|storyfi|test_do|{cid_str}||{caller_id}")
        ])
        btn_rows.append([
            InlineKeyboardButton("🔙 Back to Channels", callback_data=f"cfg|storyfi|channels|{return_page}||{caller_id}")
        ])

        try:
            await query.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btn_rows))
        except Exception as _e_det:
            if "Message is not modified" not in str(_e_det):
                logger.error(f"[AutoStoryfi] chdetail edit error: {_e_det}")
        return

    if action in ("promptsearch", "chadd"):
        cid_str = str(param)
        channels = config.get("channels", {})
        c_data = channels.get(cid_str)
        c_title = c_data.get("title", "Channel") if c_data else "Channel"

        context.user_data["storyfi_search_waiting"] = True
        context.user_data["storyfi_search_cid"] = cid_str

        reply_kb = ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True, one_time_keyboard=True)

        prompt_text = (
            f"🔍 <b>Search & Add Story to Channel</b>\n"
            f"<b>Target:</b> <code>{c_title}</code>\n"
            f"──────────────────────\n"
            f"Please type the name or keyword of the story you want to add:\n\n"
            f"💡 Examples: <code>Detective Ajay</code>, <code>Hukum Ka Ikka</code>, <code>Malang</code>, <code>Rishta</code>\n\n"
            f"(Type name directly in chat, or tap '❌ Cancel' below)"
        )
        try:
            await query.message.reply_text(prompt_text, parse_mode="HTML", reply_markup=reply_kb)
        except Exception as _e_pr:
            logger.error(f"[AutoStoryfi] promptsearch error: {_e_pr}")
        return

    if action == "chbrowse":
        cid_str = str(param)
        st_page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        channels = config.get("channels", {})
        c_data = channels.get(cid_str)
        c_title = c_data.get("title", "Channel")[:22] if c_data else "Channel"

        catalog = _get_cached_story_catalog()
        per_page = 15
        total_pages = max(1, (len(catalog) + per_page - 1) // per_page)
        st_page = max(0, min(st_page, total_pages - 1))

        chunk = catalog[st_page * per_page : (st_page + 1) * per_page]

        text = (
            f"<b>📚 Browse MongoDB Stories</b>\n"
            f"<b>Target:</b> <code>{c_title}</code>\n"
            f"──────────────────────\n"
            f"Tap any story below to add it (Page {st_page + 1}/{total_pages}):"
        )

        btn_rows = []
        for s_rel_idx, item in enumerate(chunk):
            s_abs_idx = st_page * per_page + s_rel_idx
            s_name = item.get("primary_name", "Unknown")[:26]
            btn_rows.append([
                InlineKeyboardButton(f"📖 {s_name}", callback_data=f"cfg|storyfi|doadd|{cid_str}|{s_abs_idx}|{caller_id}")
            ])

        nav_row = []
        if st_page > 0:
            nav_row.append(InlineKeyboardButton("◄ Prev", callback_data=f"cfg|storyfi|chbrowse|{cid_str}|{st_page - 1}|{caller_id}"))
        nav_row.append(InlineKeyboardButton(f"{st_page + 1}/{total_pages}", callback_data=f"cfg|storyfi|noop||{caller_id}"))
        if st_page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ►", callback_data=f"cfg|storyfi|chbrowse|{cid_str}|{st_page + 1}|{caller_id}"))

        if nav_row:
            btn_rows.append(nav_row)
        btn_rows.append([
            InlineKeyboardButton("🔍 Search by Name", callback_data=f"cfg|storyfi|promptsearch|{cid_str}||{caller_id}"),
            InlineKeyboardButton("➦ Back", callback_data=f"cfg|storyfi|chdetail|{cid_str}|0|{caller_id}")
        ])

        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btn_rows))
        except Exception as _e_add:
            if "Message is not modified" not in str(_e_add):
                logger.error(f"[AutoStoryfi] chbrowse edit error: {_e_add}")
        return

    if action == "doadd":
        cid_str = str(param)
        s_idx_str = parts[4] if len(parts) > 4 else ""
        channels = config.get("channels", {})
        c_data = channels.get(cid_str)
        if not c_data:
            try: await query.answer("Channel not found.", show_alert=True)
            except Exception: pass
            return

        catalog = _get_cached_story_catalog()
        matched_item = None
        if s_idx_str.isdigit() and int(s_idx_str) < len(catalog):
            matched_item = catalog[int(s_idx_str)]
        else:
            matched_item = next((i for i in catalog if str(i.get("_id")) == str(s_idx_str)), None)

        if not matched_item:
            try: await query.answer("Story not found in catalog.", show_alert=True)
            except Exception: pass
            return

        story_id = str(matched_item.get("_id"))
        story_name = matched_item.get("primary_name", "Story")

        m_stories = c_data.get("matched_stories") or []
        # Check duplicate
        if any(str(s.get("id")) == str(story_id) for s in m_stories):
            try: await query.answer(f"⚠️ Story '{story_name}' is already assigned!", show_alert=True)
            except Exception: pass
        else:
            m_stories.append({"id": str(story_id), "name": story_name})
            c_data["matched_stories"] = m_stories
            c_data["matched_story_name"] = m_stories[0]["name"]
            c_data["matched_story_id"] = m_stories[0]["id"]
            save_storyfi_config(config)
            try: await query.answer(f"✅ Added '{story_name}' to {c_data.get('title')}!", show_alert=True)
            except Exception: pass

        # Return to chdetail
        parts[2] = "chdetail"
        parts[3] = cid_str
        parts[4] = "0"
        await handle_storyfi_callbacks(query, context, parts, caller_id)
        return

    if action == "chdel":
        cid_str = str(param)
        s_idx = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else -1
        channels = config.get("channels", {})
        c_data = channels.get(cid_str)
        if c_data and s_idx >= 0:
            m_stories = c_data.get("matched_stories") or []
            if 0 <= s_idx < len(m_stories):
                removed = m_stories.pop(s_idx)
                c_data["matched_stories"] = m_stories
                c_data["matched_story_name"] = m_stories[0]["name"] if m_stories else None
                c_data["matched_story_id"] = m_stories[0]["id"] if m_stories else None
                save_storyfi_config(config)
                try: await query.answer(f"🗑️ Removed '{removed.get('name')}'!", show_alert=True)
                except Exception: pass

        # Return to chdetail
        parts[2] = "chdetail"
        parts[3] = cid_str
        parts[4] = "0"
        await handle_storyfi_callbacks(query, context, parts, caller_id)
        return

    if action == "chtoggle":
        cid_str = str(param)
        return_page = parts[4] if len(parts) > 4 and parts[4].isdigit() else "0"
        channels = config.get("channels", {})
        if cid_str in channels:
            channels[cid_str]["enabled"] = not channels[cid_str].get("enabled", True)
            save_storyfi_config(config)
            st = "Enabled" if channels[cid_str]["enabled"] else "Disabled"
            try: await query.answer(f"Channel {channels[cid_str].get('title')} {st}!", show_alert=True)
            except Exception: pass
        # return to channel detail or list
        parts[2] = "chdetail"
        parts[3] = cid_str
        parts[4] = return_page
        await handle_storyfi_callbacks(query, context, parts, caller_id)
        return

    if action == "freq":
        # Cycle through 24h (1x), 12h (2x), 8h (3x), 6h (4x), 4h (6x)
        current = float(config.get("interval_hours", 12.0))
        options = [24.0, 12.0, 8.0, 6.0, 4.0]
        next_val = options[(options.index(current) + 1) % len(options)] if current in options else 12.0
        config["interval_hours"] = next_val
        save_storyfi_config(config)
        p_day = round(24.0 / next_val, 1)
        try: await query.answer(f"⏱️ Frequency: {next_val}h ({p_day} posts/day)", show_alert=True)
        except Exception: pass
        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return

    if action == "stagger":
        # Cycle stagger minutes: 1-3 min -> 2-5 min -> 3-7 min -> 5-10 min
        current_pair = (config.get("stagger_min_minutes", 2), config.get("stagger_max_minutes", 5))
        presets = [(1, 3), (2, 5), (3, 7), (5, 10)]
        next_pair = presets[(presets.index(current_pair) + 1) % len(presets)] if current_pair in presets else (2, 5)
        config["stagger_min_minutes"], config["stagger_max_minutes"] = next_pair
        save_storyfi_config(config)
        try: await query.answer(f"⏳ Stagger Gap: {next_pair[0]}–{next_pair[1]} Minutes between channel posts", show_alert=True)
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
        try: await query.answer(f"💬 Reply Tone: {next_tone.capitalize()}", show_alert=True)
        except Exception: pass
        text, markup = get_storyfi_main_panel(caller_id)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return

    if action == "test_select":
        page = int(param) if param.isdigit() else 0
        channels = list(config.get("channels", {}).values())
        matched = [c for c in channels if (c.get("matched_stories") or c.get("matched_story_name"))]
        if not matched:
            try: await query.answer("No matched channels available. Run Auto-Scan first.", show_alert=True)
            except Exception: pass
            return

        per_page = 15
        total_pages = max(1, (len(matched) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        chunk = matched[page * per_page : (page + 1) * per_page]

        text = f"<tg-emoji emoji-id=\"5219943216781995020\">🚀</tg-emoji> <b>Select Channel for Test Post (Page {page + 1}/{total_pages})</b>\n──────────────────────\nTap any channel below to send an instant test post + reply:"
        btn_rows = []
        for c in chunk:
            m_sts = c.get("matched_stories") or []
            cnt_str = f" ({len(m_sts)} 📚)" if m_sts else ""
            btn_rows.append([
                InlineKeyboardButton(f"🚀 {c.get('title')[:26]}{cnt_str}", callback_data=f"cfg|storyfi|test_do|{c.get('id')}||{caller_id}")
            ])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◄ Prev", callback_data=f"cfg|storyfi|test_select|{page - 1}||{caller_id}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"cfg|storyfi|noop||{caller_id}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ►", callback_data=f"cfg|storyfi|test_select|{page + 1}||{caller_id}"))

        if nav_row:
            btn_rows.append(nav_row)
        btn_rows.append([InlineKeyboardButton("🔙 Back to Panel", callback_data=f"cfg|storyfi|main||{caller_id}")])

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
