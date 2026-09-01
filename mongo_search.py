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


def format_mongo_story_to_result(doc: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convert a raw MongoDB document into a full Riya Paid Story result dictionary."""
    try:
        from plugins.poster_bot import extract_smart_field
    except Exception:
        def extract_smart_field(d, t, default=""):
            return d.get(t, default)

    story_id = str(doc.get("_id", ""))
    raw_name = (
        doc.get("story_name_en") or
        doc.get("story_name_hi") or
        doc.get("story_name") or
        doc.get("title") or
        doc.get("name") or
        extract_smart_field(doc, "name", default="Untitled Story")
    )
    story_name = str(raw_name).strip()
    status = extract_smart_field(doc, "status", default="Completed")
    platform = extract_smart_field(doc, "platform", default="Audiobook")

    raw_genre = (
        doc.get("genre") or
        doc.get("genres") or
        doc.get("Genre") or
        doc.get("Genres") or
        doc.get("category") or
        doc.get("Category")
    )
    if isinstance(raw_genre, list) and raw_genre:
        genre = str(raw_genre[0]).strip()
    elif raw_genre:
        genre = str(raw_genre).split(",")[0].strip()
    else:
        genre = "Drama"

    episodes = extract_smart_field(doc, "episodes", default="Full Story")
    description = extract_smart_field(doc, "description", default="")

    # Link: prefer doc link, or fallback to StoryfiBot / Arya MiniApp deep link
    story_link = str(doc.get("link") or doc.get("url") or "").strip()
    if not story_link.startswith(("http://", "https://", "tg://")):
        if story_id:
            story_link = f"https://t.me/StoryfiBot?start=buy_{story_id}"
        else:
            story_link = "https://t.me/StoryfiBot"

    # Photo / Cover - Priority resolution for SQUARE Poster (1:1):
    image_url = ""
    for field in ["poster_url", "poster", "image_url", "cover", "image", "cover_url", "photo_url", "photo", "banner_url", "banner"]:
        val = doc.get(field)
        if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
            image_url = val.strip()
            break
        elif isinstance(val, dict):
            for sub_k in ["url", "src", "link", "path"]:
                s = str(val.get(sub_k) or "").strip()
                if s.startswith(("http://", "https://")):
                    image_url = s
                    break
            if image_url:
                break
        elif isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str) and first.strip().startswith(("http://", "https://")):
                image_url = first.strip()
                break
            elif isinstance(first, dict):
                for sub_k in ["url", "src", "link", "path"]:
                    s = str(first.get(sub_k) or "").strip()
                    if s.startswith(("http://", "https://")):
                        image_url = s
                        break
                if image_url:
                    break

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
        "description": description,
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
