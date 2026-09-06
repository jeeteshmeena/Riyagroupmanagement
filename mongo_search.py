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
import sys
from typing import Optional, Dict, Any, List, Tuple

if 'OpenSSL' not in sys.modules:
    sys.modules['OpenSSL'] = None
    sys.modules['OpenSSL.crypto'] = None
    sys.modules['OpenSSL.SSL'] = None

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


_CACHED_CLIENT = None
_CACHED_COLLECTION = None
_CACHED_SETTINGS = None


def _get_mongo_client():
    global _CACHED_CLIENT, _CACHED_COLLECTION, _CACHED_SETTINGS
    if pymongo is None:
        return None, None, None

    # Check if cached client is alive
    if _CACHED_COLLECTION is not None and _CACHED_CLIENT is not None:
        try:
            # ping server to verify connection is open
            _CACHED_CLIENT.admin.command('ping')
            return _CACHED_COLLECTION, _CACHED_CLIENT, _CACHED_SETTINGS
        except Exception:
            _CACHED_CLIENT = None
            _CACHED_COLLECTION = None

    try:
        from plugins.poster_bot import load_poster_settings
        settings = load_poster_settings()
    except Exception:
        settings = {}

    mongo_uri = settings.get("mongo_uri") or os.getenv("MONGO_URI", "")
    if not mongo_uri:
        try:
            import json
            for p in ["poster_settings.json", os.path.join(os.getenv("DATA_DIR", "."), "poster_settings.json")]:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            settings = data
                            mongo_uri = data.get("mongo_uri") or mongo_uri
                            break
        except Exception:
            pass

    if not mongo_uri:
        return None, None, None

    try:
        client = pymongo.MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=6000,
            readPreference='primaryPreferred',
            retryReads=True,
            maxPoolSize=20
        )
        
        # Auto-discover database name from settings -> default DB -> cluster DB list
        db_name = settings.get("database_name") or os.getenv("DATABASE_NAME")
        if not db_name or db_name == "arya_premium":
            try:
                db_name = client.get_default_database().name
            except Exception:
                pass
        
        if not db_name or db_name == "arya_premium":
            try:
                dbs = client.list_database_names()
                if "forward-bot" in dbs:
                    db_name = "forward-bot"
                elif "arya_premium" in dbs:
                    db_name = "arya_premium"
                elif dbs:
                    non_sys = [d for d in dbs if d not in ("admin", "local", "config")]
                    if non_sys:
                        db_name = non_sys[0]
            except Exception:
                db_name = "forward-bot"

        if not db_name:
            db_name = "forward-bot"

        col_name = settings.get("collection_name") or "premium_stories"
        col = client[db_name][col_name]
        
        _CACHED_CLIENT = client
        _CACHED_COLLECTION = col
        _CACHED_SETTINGS = settings
        return col, client, settings
    except Exception as e:
        logger.error(f"[MongoSearch] Failed to connect to MongoDB: {e}")
        return None, None, None




def resolve_file_id_via_source_bot(file_id: str) -> Optional[str]:
    """
    If SOURCE_BOT_TOKEN or ARYA_BOT_TOKEN or FORWARD_BOT_TOKEN or BOT_TOKEN is configured,
    queries Telegram Bot API getFile to get the direct download URL for the file_id.
    """
    if not file_id:
        return None
    file_id = str(file_id).strip()
    if not (file_id.startswith("AgAC") or len(file_id) >= 20):
        return None

    import requests
    tokens = [
        os.getenv("SOURCE_BOT_TOKEN"),
        os.getenv("ARYA_BOT_TOKEN"),
        os.getenv("FORWARD_BOT_TOKEN"),
        os.getenv("POSTER_BOT_TOKEN"),
        os.getenv("BOT_TOKEN"),
    ]
    tokens = [t.strip() for t in tokens if t and t.strip()]

    for tok in tokens:
        try:
            resp = requests.get(f"https://api.telegram.org/bot{tok}/getFile", params={"file_id": file_id}, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok") and data.get("result", {}).get("file_path"):
                    file_path = data["result"]["file_path"]
                    logger.info(f"[FileID Resolve] ✅ Successfully resolved file_id to Telegram URL: {file_path}")
                    return f"https://api.telegram.org/file/bot{tok}/{file_path}"
        except Exception as e:
            logger.debug(f"[FileID Resolve] Error with token: {e}")

    return None


def extract_square_image_url(doc: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> str:
    """
    Intelligently extracts the Square Poster (1:1) image URL from a MongoDB document in read-only mode.
    Priority:
      1. Square / R2 Poster fields: poster_url, square_poster, poster, square_url, poster_image, square_cover, r2_url, cloudflare_url
      2. Nested objects: media.poster, images.square, images.poster, assets.poster, cloudflare.url, r2.url
      3. General cover / image fields: image_url, cover_url, cover, image, photo_url, photo
      4. Telegram file_id resolution via source bot token
    """
    if not isinstance(doc, dict):
        return ""

    square_keys = [
        "banner_url", "banner", "banner_image", "story_banner", "bannerUrl",
        "poster_url", "posterurl", "posterUrl", "square_poster", "square_poster_url",
        "square_image", "square_url", "square_cover", "poster", "poster_image", "poster_path",
        "r2_url", "r2_poster", "r2_image", "cloudflare_url", "cdn_url",
        "image_url", "imageurl", "imageUrl", "cover_url", "coverurl", "coverUrl",
        "cover", "cover_image", "cover_path", "photo_url", "photourl", "photoUrl",
        "photo", "thumbnail_url", "thumbnail", "thumb", "image", "img_url", "img"
    ]

    R2_BASE = "https://pub-d738aa13a7944050a7edb60cc5cd91bb.r2.dev/"

    def _extract_url_from_val(val: Any) -> str:
        if isinstance(val, str):
            s = val.strip()
            if s.startswith(("http://", "https://")):
                return s
            if any(s.lower().endswith(ext) for ext in [".webp", ".jpg", ".jpeg", ".png", ".jfif"]):
                return f"{R2_BASE}{s.lstrip('/')}"
            # Check for /api/tg-image?file_id=...
            if "/api/tg-image" in s and "file_id=" in s:
                try:
                    import urllib.parse
                    parsed = urllib.parse.urlparse(s)
                    params = urllib.parse.parse_qs(parsed.query)
                    fid = params.get("file_id", [None])[0]
                    if fid:
                        resolved = resolve_file_id_via_source_bot(fid)
                        if resolved:
                            return resolved
                except Exception:
                    pass
            # Check if val is a raw Telegram File ID (AgAC...)
            if s.startswith("AgAC"):
                resolved = resolve_file_id_via_source_bot(s)
                if resolved:
                    return resolved
        elif isinstance(val, dict):
            for sub_k in ["banner", "banner_url", "square", "square_poster", "poster", "poster_url", "url", "src", "link", "r2_url", "cdn_url", "path", "image", "cover"]:
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

    # 1. Check top-level square and banner keys
    for k in square_keys:
        if k in doc:
            url = _extract_url_from_val(doc[k])
            if url:
                return url

    # 2. Check nested containers
    for container_key in ["media", "images", "assets", "cloudflare", "r2", "files", "story", "data"]:
        container = doc.get(container_key)
        if isinstance(container, dict):
            for k in square_keys:
                if k in container:
                    url = _extract_url_from_val(container[k])
                    if url:
                        return url
        elif isinstance(container, list) and container:
            for item in container:
                url = _extract_url_from_val(item)
                if url:
                    return url

    # 3. Deep search any key containing 'poster', 'cover', 'square', 'image', 'photo', 'r2', 'banner'
    for k, v in doc.items():
        k_lower = k.lower()
        if any(w in k_lower for w in ["banner", "poster", "cover", "square", "image", "photo", "r2"]):
            url = _extract_url_from_val(v)
            if url:
                return url

    return ""


def extract_smart_field(doc: Dict[str, Any], field_type: str, default: str = "") -> str:
    """Extract a field intelligently from a MongoDB story document."""
    if not isinstance(doc, dict):
        return default

    if field_type == "name":
        # 1. Direct explicit title keys in MongoDB documents
        for direct_key in ["story_name_en", "story_name_hi", "story_name", "title", "name", "story_title", "book_name", "audiobook_name"]:
            val = doc.get(direct_key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    try:
        from plugins.poster_bot import extract_smart_field as _psf
        res = _psf(doc, field_type, default=default)
        if res:
            return res
    except Exception:
        pass

    if field_type == "name":
        return str(
            doc.get("story_name_en")
            or doc.get("story_name_hi")
            or doc.get("story_name")
            or doc.get("title")
            or doc.get("name")
            or default
        ).strip()
    elif field_type == "status":
        return str(doc.get("status") or doc.get("state") or default).strip()
    elif field_type == "platform":
        return str(doc.get("platform") or doc.get("source") or default).strip()
    elif field_type == "episodes":
        return str(doc.get("episodes") or doc.get("total_episodes") or doc.get("eps") or default).strip()
    return str(doc.get(field_type, default)).strip()


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
        image_url = ""

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

        # Build token-flexible regex pattern (e.g. 'mr and mrs agent' matches 'Mr. & Mrs. Agent')
        qn = normalize_text(base_q)
        tokens = [t for t in qn.split() if t]
        
        if tokens:
            pattern_parts = []
            for t in tokens:
                if t in ("and", "&", "+"):
                    pattern_parts.append(r"(?:and|\&|\+)")
                else:
                    pattern_parts.append(re.escape(t))
            sep = r"[\s\.\-_,:;'/\\!*?\"~|]+"
            # Allow title with optional trailing punctuation / subtitle (e.g. : Season 1, Pocket FM)
            flexible_exact = rf"^[\s\.\-_,:;'/\\!*?\"~|]*" + sep.join(pattern_parts) + rf"(?:[\s\.\-_,:;'/\\!*?\"~|].*)?$"
            flexible_wb = rf"\b" + sep.join(pattern_parts) + rf"\b"
        else:
            flexible_exact = rf"^{re.escape(base_q)}(?:[\s\.\-_,:;'/\\!*?\"~|].*)?$"
            flexible_wb = rf"\b{re.escape(base_q)}\b"

        # Pass 1: Flexible Exact / Prefix Match (Bypasses punctuation, maps & <-> and)
        exact_filter = {
            "$or": [
                {"story_name_en": {"$regex": flexible_exact, "$options": "i"}},
                {"story_name_hi": {"$regex": flexible_exact, "$options": "i"}},
                {"story_name": {"$regex": flexible_exact, "$options": "i"}},
                {"title": {"$regex": flexible_exact, "$options": "i"}},
                {"name": {"$regex": flexible_exact, "$options": "i"}},
                {"aliases": {"$regex": flexible_exact, "$options": "i"}},
            ]
        }
        doc = col.find_one(exact_filter)
        if doc:
            res = format_mongo_story_to_result(doc, settings)
            try:
                from database import save_learned_alias
                save_learned_alias(query, res["text"])
            except Exception:
                pass
            return res

        # Pass 2: Word-boundary Regex Match (e.g. "Mr and Mrs Agent" in "Mr. & Mrs. Agent: Complete Series")
        if len(tokens) >= 1:
            wb_filter = {
                "$or": [
                    {"story_name_en": {"$regex": flexible_wb, "$options": "i"}},
                    {"story_name_hi": {"$regex": flexible_wb, "$options": "i"}},
                    {"story_name": {"$regex": flexible_wb, "$options": "i"}},
                    {"title": {"$regex": flexible_wb, "$options": "i"}},
                    {"name": {"$regex": flexible_wb, "$options": "i"}},
                    {"aliases": {"$regex": flexible_wb, "$options": "i"}},
                ]
            }
            doc = col.find_one(wb_filter)
            if doc:
                res = format_mongo_story_to_result(doc, settings)
                try:
                    from database import save_learned_alias
                    save_learned_alias(query, res["text"])
                except Exception:
                    pass
                return res

        # Pass 3: Multi-word AND lookahead match with & / and tolerance
        if len(tokens) > 1:
            lookahead_parts = []
            for t in tokens:
                if t in ("and", "&", "+"):
                    lookahead_parts.append(r"(?=.*(?:and|\&|\+))")
                else:
                    lookahead_parts.append(rf"(?=.*{re.escape(t)})")
            lookahead_pattern = "".join(lookahead_parts)
            multi_filter = {
                "$or": [
                    {"story_name_en": {"$regex": lookahead_pattern, "$options": "i"}},
                    {"story_name_hi": {"$regex": lookahead_pattern, "$options": "i"}},
                    {"story_name": {"$regex": lookahead_pattern, "$options": "i"}},
                    {"title": {"$regex": lookahead_pattern, "$options": "i"}},
                    {"name": {"$regex": lookahead_pattern, "$options": "i"}},
                    {"aliases": {"$regex": lookahead_pattern, "$options": "i"}},
                ]
            }
            doc = col.find_one(multi_filter)
            if doc:
                res = format_mongo_story_to_result(doc, settings)
                try:
                    from database import save_learned_alias
                    save_learned_alias(query, res["text"])
                except Exception:
                    pass
                return res

        # Pass 4: Substring Match
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
                res = format_mongo_story_to_result(doc, settings)
                try:
                    from database import save_learned_alias
                    save_learned_alias(query, res["text"])
                except Exception:
                    pass
                return res

        # Pass 5: RapidFuzz In-Memory Candidate Fallback (Smart Typo Tolerance)
        try:
            from rapidfuzz import fuzz
            sig_words = [w for w in tokens if len(w) >= 3 and w not in ("and", "the", "for", "with", "story", "audiobook")]
            or_clauses = []
            if sig_words:
                for sw in sig_words[:3]:
                    or_clauses.extend([
                        {"story_name_en": {"$regex": re.escape(sw), "$options": "i"}},
                        {"title": {"$regex": re.escape(sw), "$options": "i"}},
                        {"name": {"$regex": re.escape(sw), "$options": "i"}},
                        {"aliases": {"$regex": re.escape(sw), "$options": "i"}},
                    ])
            if not or_clauses and tokens:
                for tok in tokens[:2]:
                    or_clauses.append({"story_name_en": {"$regex": re.escape(tok), "$options": "i"}})
                    or_clauses.append({"title": {"$regex": re.escape(tok), "$options": "i"}})

            if or_clauses:
                candidates = list(col.find({"$or": or_clauses}).limit(50))
                best_c = None
                best_s = 0
                for c_doc in candidates:
                    c_title = extract_smart_field(c_doc, "name", "")
                    c_norm = normalize_text(c_title)
                    if not c_norm:
                        continue
                    r_ratio = fuzz.ratio(qn, c_norm)
                    r_sort = fuzz.token_sort_ratio(qn, c_norm)
                    r_set = fuzz.token_set_ratio(qn, c_norm)
                    score = max(r_ratio, r_sort, r_set if len(tokens) >= 2 else 0)

                    for al in c_doc.get("aliases", []):
                        al_norm = normalize_text(al)
                        if al_norm:
                            al_score = max(fuzz.ratio(qn, al_norm), fuzz.token_sort_ratio(qn, al_norm), fuzz.token_set_ratio(qn, al_norm))
                            score = max(score, al_score)

                    threshold = 75 if (len(tokens) >= 2 or len(qn) >= 6) else 82
                    if score >= threshold and score > best_s:
                        best_s = score
                        best_c = c_doc

                if best_c:
                    res = format_mongo_story_to_result(best_c, settings)
                    try:
                        save_mongo_alias_sync(str(best_c.get("_id") or res["text"]), query)
                        from database import save_learned_alias
                        save_learned_alias(query, res["text"])
                    except Exception:
                        pass
                    return res
        except Exception as e_fuzz:
            logger.debug(f"[MongoSearch] Typo search error: {e_fuzz}")

        return None
    except Exception as e:
        logger.error(f"[MongoSearch] Error querying MongoDB for '{query}': {e}")
        return None


def save_mongo_alias_sync(doc_id_or_name: str, alias: str) -> bool:
    """
    Saves an alias permanently to the story document in MongoDB using $addToSet.
    """
    if not doc_id_or_name or not alias:
        return False
    col, client, settings = _get_mongo_client()
    if col is None:
        return False
    clean_alias = _clean_query(alias).strip()
    if not clean_alias or len(clean_alias) < 2:
        return False
    try:
        from bson import ObjectId
        query = None
        if ObjectId.is_valid(doc_id_or_name):
            query = {"_id": ObjectId(doc_id_or_name)}
        else:
            query = {
                "$or": [
                    {"story_name_en": doc_id_or_name},
                    {"story_name": doc_id_or_name},
                    {"title": doc_id_or_name},
                    {"name": doc_id_or_name}
                ]
            }
        col.update_one(query, {"$addToSet": {"aliases": clean_alias}})
        logger.info(f"[MongoSearch] ✅ Saved alias '{clean_alias}' to Mongo story '{doc_id_or_name}'")
        return True
    except Exception as e:
        logger.debug(f"[MongoSearch] Error saving alias to Mongo: {e}")
        return False


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


async def async_search_mongo_stories_by_keyword(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Asynchronously search MongoDB for multiple stories by keyword."""
    return await asyncio.to_thread(search_mongo_stories_by_keyword_sync, query, limit)
