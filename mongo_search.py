"""
mongo_search.py
---------------
Read-Only MongoDB story search fallback for Riya Stories Bot.
When a story is not found in Riya Bot's local scanned database,
this module queries Poster Bot's MongoDB database (collection: premium_stories)
and formats the result as a Paid Story card with complete metadata.
"""

import asyncio
import html
import logging
import os
import re
from typing import Optional, Dict, Any, List

try:
    import pymongo
except ImportError:
    pymongo = None

from database import normalize_text

logger = logging.getLogger(__name__)


def _clean_query(name: str) -> str:
    if not name:
        return ""
    name = re.sub(r"\(.*?\)", "", str(name))
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def _get_mongo_client():
    if pymongo is None:
        return None, None, None

    try:
        from plugins.poster_bot import load_poster_settings
        settings = load_poster_settings()
    except Exception:
        settings = {}

    mongo_uri = settings.get("mongo_uri") or os.getenv("MONGO_URI", "")
    if not mongo_uri:
        return None, None, None

    db_name = settings.get("database_name") or os.getenv("DATABASE_NAME", "arya_premium")
    col_name = settings.get("collection_name", "premium_stories")

    try:
        client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=4000)
        col = client[db_name][col_name]
        return col, client, settings
    except Exception as e:
        logger.error(f"[MongoSearch] Failed to connect to MongoDB: {e}")
        return None, None, None




def extract_square_image_url(doc: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> str:
    """
    Intelligently extracts the Square Poster (1:1) image URL from a MongoDB document in read-only mode.
    Priority:
      1. Square / R2 Poster fields: poster_url, square_poster, poster, square_url, poster_image, square_cover, r2_url, cloudflare_url
      2. Nested objects: media.poster, images.square, images.poster, assets.poster, cloudflare.url, r2.url
      3. General cover / image fields: image_url, cover_url, cover, image, photo_url, photo
      4. Array of media / images
      5. Banner only as last fallback
    """
    if not isinstance(doc, dict):
        return ""

    square_keys = [
        "square_poster", "square_poster_url", "square_image", "square_url", "square_cover",
        "poster_url", "posterurl", "posterUrl", "poster", "poster_image", "poster_path",
        "r2_url", "r2_poster", "r2_image", "cloudflare_url", "cdn_url",
        "image_url", "imageurl", "imageUrl", "image", "img_url", "img",
        "cover_url", "coverurl", "coverUrl", "cover", "cover_image", "cover_path",
        "photo_url", "photourl", "photoUrl", "photo", "thumbnail_url", "thumbnail", "thumb"
    ]

    def _extract_url_from_val(val: Any) -> str:
        if isinstance(val, str):
            s = val.strip()
            if s.startswith(("http://", "https://")):
                return s
        elif isinstance(val, dict):
            for sub_k in ["square", "square_poster", "poster", "poster_url", "url", "src", "link", "r2_url", "cdn_url", "path", "image", "cover"]:
                if sub_k in val:
                    sub_v = _extract_url_from_val(val[sub_k])
                    if sub_v:
                        return sub_v
        elif isinstance(val, list) and val:
            for item in val:
                sub_v = _extract_url_from_val(item)
                if sub_v:
                    return sub_v
        return ""

    # 1. Check top-level square keys
    for k in square_keys:
        if k in doc:
            url = _extract_url_from_val(doc[k])
            if url:
                return url

    # 2. Check nested containers
    for container_key in ["media", "images", "assets", "cloudflare", "r2", "files"]:
        container = doc.get(container_key)
        if isinstance(container, (dict, list)):
            url = _extract_url_from_val(container)
            if url:
                return url

    # 3. Fallback: Banner keys
    for bk in ["banner_url", "bannerurl", "bannerUrl", "banner", "banner_image"]:
        if bk in doc:
            url = _extract_url_from_val(doc[bk])
            if url:
                return url

    return ""


def format_mongo_story_to_result(doc: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Convert a MongoDB story document into Riya Stories Bot search result format (LIGHT_PRO / Paid Story).
    Ensures safe data types and guaranteed HTTP(S) image URL.
    """
    story_id = str(doc.get("_id") or "")
    story_name = extract_smart_field(doc, "name", default="Unknown Story")

    status = extract_smart_field(doc, "status", default="Completed")
    platform = extract_smart_field(doc, "platform", default="Pocket FM")

    raw_genre = doc.get("genre") or doc.get("genres") or doc.get("category")
    if isinstance(raw_genre, list) and raw_genre:
        genre = str(raw_genre[0]).strip()
    elif raw_genre:
        genre = str(raw_genre).split(",")[0].strip()
    else:
        genre = "Drama"

    episodes = extract_smart_field(doc, "episodes", default="Full Story")
    # Link: prefer Buy Via App link (UseAryaBot MiniApp)
    buy_app_url = doc.get("buy_app_url") or doc.get("app_url") or ""
    if not buy_app_url and story_id:
        buy_app_url = f"https://t.me/UseAryaBot/apminibyarya?startapp=story_{story_id}"

    story_link = str(buy_app_url or doc.get("link") or doc.get("url") or "").strip()
    if not story_link.startswith(("http://", "https://", "tg://")):
        if story_id:
            story_link = f"https://t.me/UseAryaBot/apminibyarya?startapp=story_{story_id}"
        else:
            story_link = "https://t.me/UseAryaBot/apminibyarya"

    # Extract square poster / Cloudflare R2 image URL
    image_url = extract_square_image_url(doc, settings)
    if not image_url:
        image_url = "https://images.unsplash.com/photo-1614729939124-032f0b56c9ce?w=400"

    clean_key = _clean_query(story_name).lower()

    return {
        "name": clean_key,
        "text": story_name,
        "link": story_link,
        "status": status,
        "platform": platform,
        "genre": genre,
        "episodes": episodes,
        "price": "",
        "description": "",
        "is_paid": True,
        "format": "LIGHT_PRO",
        "from_mongo": True,
        "photo": image_url,
        "image": image_url,
        "story_type": "Paid Story",
        "mongo_id": story_id,
    }


def search_story_from_mongo_sync(query: str) -> Optional[Dict[str, Any]]:
    """
    Synchronously search MongoDB for a matching story title or ObjectId.
    1. Check ObjectId if valid.
    2. Exact regex match across name, title, story_name, aliases.
    3. Word-boundary / Substring match.
    """
    if not query:
        return None

    col, client, settings = _get_mongo_client()
    if col is None:
        return None

    clean_q = _clean_query(query).strip()
    if not clean_q or len(clean_q) < 2:
        if client:
            client.close()
        return None

    try:
        # Check by ObjectId directly
        try:
            from bson import ObjectId
            if ObjectId.is_valid(clean_q):
                doc = col.find_one({"_id": ObjectId(clean_q)})
                if doc:
                    return format_mongo_story_to_result(doc, settings)
        except Exception:
            pass

        base_q = clean_q
        for tag in ["pocket fm", "kuku fm", "kuku tv", "pratilipi", "headfone", "eight fm", "audiobook", "full story", "all episodes", "season 1", "season 2", "part 1", "part 2"]:
            base_q = re.sub(rf"\b{re.escape(tag)}\b", "", base_q, flags=re.IGNORECASE).strip()
        if not base_q:
            base_q = clean_q

        # Pass 1: Exact Case-Insensitive Match
        exact_filter = {
            "$or": [
                {"story_name_en": {"$regex": rf"^{re.escape(base_q)}$", "$options": "i"}},
                {"story_name_hi": {"$regex": rf"^{re.escape(base_q)}$", "$options": "i"}},
                {"story_name": {"$regex": rf"^{re.escape(base_q)}$", "$options": "i"}},
                {"title": {"$regex": rf"^{re.escape(base_q)}$", "$options": "i"}},
                {"name": {"$regex": rf"^{re.escape(base_q)}$", "$options": "i"}},
                {"aliases": {"$regex": rf"^{re.escape(base_q)}$", "$options": "i"}},
            ]
        }
        doc = col.find_one(exact_filter)
        if doc:
            return format_mongo_story_to_result(doc, settings)

        # Pass 2: Word-boundary Regex Match (e.g. "Vashikaran" in "Vashikaran: Season 1")
        if len(base_q) >= 3:
            wb_filter = {
                "$or": [
                    {"story_name_en": {"$regex": rf"\b{re.escape(base_q)}\b", "$options": "i"}},
                    {"story_name_hi": {"$regex": rf"\b{re.escape(base_q)}\b", "$options": "i"}},
                    {"story_name": {"$regex": rf"\b{re.escape(base_q)}\b", "$options": "i"}},
                    {"title": {"$regex": rf"\b{re.escape(base_q)}\b", "$options": "i"}},
                    {"name": {"$regex": rf"\b{re.escape(base_q)}\b", "$options": "i"}},
                    {"aliases": {"$regex": rf"\b{re.escape(base_q)}\b", "$options": "i"}},
                ]
            }
            doc = col.find_one(wb_filter)
            if doc:
                return format_mongo_story_to_result(doc, settings)

        # Pass 3: Substring Match for queries with 4+ characters
        if len(base_q) >= 4:
            sub_filter = {
                "$or": [
                    {"story_name_en": {"$regex": re.escape(base_q), "$options": "i"}},
                    {"story_name_hi": {"$regex": re.escape(base_q), "$options": "i"}},
                    {"story_name": {"$regex": re.escape(base_q), "$options": "i"}},
                    {"title": {"$regex": re.escape(base_q), "$options": "i"}},
                    {"name": {"$regex": re.escape(base_q), "$options": "i"}},
                    {"aliases": {"$regex": re.escape(base_q), "$options": "i"}},
                ]
            }
            doc = col.find_one(sub_filter)
            if doc:
                return format_mongo_story_to_result(doc, settings)

        return None
    except Exception as e:
        logger.error(f"[MongoSearch] Error querying MongoDB for '{query}': {e}")
        return None
    finally:
        if client:
            client.close()


async def async_search_story_from_mongo(query: str) -> Optional[Dict[str, Any]]:
    """Asynchronously search MongoDB for a story."""
    return await asyncio.to_thread(search_story_from_mongo_sync, query)


def search_mongo_stories_by_keyword_sync(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search MongoDB for multiple stories matching a keyword."""
    if not query:
        return []

    col, client, settings = _get_mongo_client()
    if col is None:
        return []

    clean_q = _clean_query(query).strip()
    if not clean_q or len(clean_q) < 3:
        if client:
            client.close()
        return []

    try:
        sub_filter = {
            "$or": [
                {"story_name_en": {"$regex": re.escape(clean_q), "$options": "i"}},
                {"story_name_hi": {"$regex": re.escape(clean_q), "$options": "i"}},
                {"story_name": {"$regex": re.escape(clean_q), "$options": "i"}},
                {"title": {"$regex": re.escape(clean_q), "$options": "i"}},
                {"name": {"$regex": re.escape(clean_q), "$options": "i"}},
                {"aliases": {"$regex": re.escape(clean_q), "$options": "i"}},
            ]
        }
        cursor = col.find(sub_filter).limit(limit)
        results = []
        for doc in cursor:
            results.append(format_mongo_story_to_result(doc, settings))
        return results
    except Exception as e:
        logger.error(f"[MongoSearch] Error keyword query MongoDB for '{query}': {e}")
        return []
    finally:
        if client:
            client.close()


async def async_search_mongo_stories_by_keyword(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Asynchronously search MongoDB for multiple stories by keyword."""
    return await asyncio.to_thread(search_mongo_stories_by_keyword_sync, query, limit)
