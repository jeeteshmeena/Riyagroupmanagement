import re

# possible fields used in posts - relaxed to index more stories
NAME_PATTERNS = [
    # Bullet format with status: "- Story Title ( Completed )"
    r"^\s*-\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)\s*$",
    # Name patterns with or without status
    r"name\s*[:\-]\s*([^\n\(]+)",
    r"story\s*[:\-]\s*([^\n\(]+)",
    r"title\s*[:\-]\s*([^\n\(]+)",
    r"story name\s*[:\-]\s*([^\n\(]+)",
    r"♨️Story\s*[:\-]\s*([^\n\(]+)",
    # Strict title with status (fallback)
    r"^\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)\s*$",
]

LINK_PATTERN = r"https://t\.me/[^\s]+"

TYPE_PATTERN = re.compile(
    r"(Story Type|Type|Genre|🗓Genre)\s*[:\-]\s*(.+)",
    re.IGNORECASE
)


def get_photo(message):
    """
    Robustly extract a photo from a Telethon message.
    Handles: regular photo, document thumbnail, album (grouped_id).
    Returns a Telethon photo/document object or None.
    """
    # Direct photo message
    if getattr(message, "photo", None):
        return message.photo

    # Photo inside a document (sent as file but has thumbnail)
    doc = getattr(message, "document", None)
    if doc:
        thumbs = getattr(doc, "thumbs", None)
        if thumbs:
            return doc  # We'll use the document itself; scanner_client will download the thumbnail

    return None


def get_text(message):
    """
    Safely get text from Telethon message
    """
    if hasattr(message, "message") and message.message:
        return message.message

    if hasattr(message, "text") and message.text:
        return message.text

    if hasattr(message, "caption") and message.caption:
        return message.caption

    return None


def extract_name(text):
    """
    Try multiple patterns to detect story name (title).
    EXTREMELY STRICT - only match valid story formats.
    """

    for pattern in NAME_PATTERNS:

        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)

        if match:
            raw = match.group(1).strip()
            # drop any status in parentheses
            cleaned = re.sub(r"\(.*?\)", "", raw).strip()
            # Drop any dangling leading symbols
            cleaned = cleaned.lstrip(":-_!~|> \t")
            if cleaned and len(cleaned) > 2:  # Ensure meaningful title
                return cleaned or None

    # NO FALLBACK - if no pattern matches, it's not a story
    return None


# Keywords that indicate a PRIMARY play/read link (highest priority)
_PRIMARY_BTN_KEYWORDS = ("play", "ᴘʟᴀʏ", "\u1d18\u029f\u1d00\u028f", "buy", "\u0299\u1d1c\u028f", "\u1d18\u029f\u1d04\u028f",
                          "read", "open", "start", "watch")
# Keywords that indicate a BACKUP / secondary link (lowest priority)
_BACKUP_BTN_KEYWORDS   = ("backup", "mirror", "alt", "alternative", "link 2", "link2")


def _btn_priority(btn_text: str) -> int:
    """Return priority score for a button: 2=primary, 1=neutral, 0=backup."""
    t = (btn_text or "").lower()
    if any(k in t for k in _BACKUP_BTN_KEYWORDS):
        return 0
    if any(k in (btn_text or "") or k in t for k in _PRIMARY_BTN_KEYWORDS):
        return 2
    return 1


def extract_link(message):
    """
    Get the best telegram link from a message's inline keyboard or text.

    Priority:
      2 — Play Now / Buy Now / Read / Open   (primary)
      1 — Any other URL button               (neutral)
      0 — Backup / Mirror / Alt link         (lowest)

    Always returns the highest-priority link.
    Falls back to text links only if keyboard has none.
    """
    # Collect (priority, url) pairs from the inline keyboard
    btn_links: list[tuple[int, str]] = []

    if hasattr(message, 'reply_markup') and message.reply_markup:
        try:
            from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonUrl
            rm = message.reply_markup
            if isinstance(rm, ReplyInlineMarkup):
                for row in rm.rows:
                    for btn in row.buttons:
                        if isinstance(btn, KeyboardButtonUrl):
                            btn_links.append((_btn_priority(getattr(btn, 'text', '')), btn.url))
            elif hasattr(rm, 'inline_keyboard'):
                for row in rm.inline_keyboard:
                    for btn in row:
                        url = getattr(btn, 'url', None)
                        if url:
                            btn_links.append((_btn_priority(getattr(btn, 'text', '')), url))
        except Exception:
            pass

    if btn_links:
        # Return the URL with the highest priority score (stable sort keeps first-seen if tied)
        best = max(btn_links, key=lambda x: x[0])
        return best[1]

    # Fallback: extract from message text
    text = get_text(message)
    if text:
        links = re.findall(LINK_PATTERN, text)
        if links:
            return links[0]   # first link in text is usually the primary one

    return None


def _extract_best_link(message) -> tuple:
    """
    Extract the best URL from inline keyboard buttons.
    Returns (url, btn_text, is_paid).

    Scoring: Play/Buy > neutral > Backup.
    btn_text is stored so add_story() can use it for quality scoring.
    """
    btn_links = []  # (priority, url, btn_text)

    if hasattr(message, 'reply_markup') and message.reply_markup:
        try:
            from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonUrl
            rm = message.reply_markup
            if isinstance(rm, ReplyInlineMarkup):
                for row in rm.rows:
                    for btn in row.buttons:
                        if isinstance(btn, KeyboardButtonUrl):
                            bt = getattr(btn, 'text', '') or ''
                            btn_links.append((_btn_priority(bt), btn.url, bt))
            elif hasattr(rm, 'inline_keyboard'):
                for row in rm.inline_keyboard:
                    for btn in row:
                        url = getattr(btn, 'url', None)
                        if url:
                            bt = getattr(btn, 'text', '') or ''
                            btn_links.append((_btn_priority(bt), url, bt))
        except Exception:
            pass

    if btn_links:
        best_priority, best_url, best_btn_text = max(btn_links, key=lambda x: x[0])
        bt_lower = best_btn_text.lower()
        is_paid = ("buy" in bt_lower or "x1d299x1d1cx028f" in best_btn_text or
                   "paid" in bt_lower or "purchase" in bt_lower)
        return best_url, best_btn_text, is_paid

    # Fallback: text links
    text = get_text(message)
    if text:
        import re as _re
        links = _re.findall(LINK_PATTERN, text)
        if links:
            return links[0], "", False

    return None, "", False


def extract_story_type(text):
    """
    Detect story type / genre from full message text.
    """
    if not text:
        return None

    match = TYPE_PATTERN.search(text)

    if match:
        return match.group(2).strip()

    return None


def extract_light_pro_format(message):
    """Detect and parse Light Pro format (has 🎬 Episodes field)."""
    text = get_text(message)
    if not text:
        return None

    has_photo = bool(getattr(message, "photo", None))
    # Light Pro has all Light markers PLUS 🎬
    # Need at minimum: name marker (♨️) + status (🔰) + episodes (🎬)
    # Platform (🖥) and genre (🧩/🗓) are optional — be lenient for partial formats
    required = ["♨️", "🔰", "🎬"]
    if not all(x in text for x in required):
        return None
    # Must have a photo (Light Pro always has cover image)
    if not has_photo:
        return None

    name_match    = re.search(r"^♨️(?!.*Description).*?:\s*(.+)", text, re.MULTILINE)
    status_match  = re.search(r"🔰.*?:\s*(.+)", text)
    platform_match= re.search(r"🖥.*?:\s*(.+)", text)
    genre_match   = re.search(r"(?:🧩|🗓).*?:\s*(.+)", text)
    episodes_match= re.search(r"🎬.*?:\s*(.+)", text)

    if not name_match:
        return None

    def _clean(s):
        return re.sub(r"<[^>]+>", "", s).strip()

    name     = _clean(name_match.group(1))
    status   = _clean(status_match.group(1))  if status_match  else "Unknown"
    platform = _clean(platform_match.group(1)) if platform_match else "Unknown"
    genre    = _clean(genre_match.group(1))   if genre_match   else "Unknown"
    episodes = _clean(episodes_match.group(1)) if episodes_match else ""

    # Parse description
    lines = text.split("\n")
    desc_started, desc_lines = False, []
    for l in lines:
        if "Description" in l or "\U0001f4dd" in l:
            desc_started = True
            continue
        if desc_started:
            stripped = l.strip()
            if stripped.startswith(">"):
                desc_lines.append(stripped.lstrip("> ").strip())
            elif stripped:
                desc_lines.append(stripped)
    description = "\n".join(desc_lines).strip()

    # Extract best link using priority scoring (Play/Buy > neutral > Backup)
    link, link_btn_text, is_paid = _extract_best_link(message)

    # Also detect is_paid from caption text
    _PAID_MARKERS = ("paid story", "after purchase", "खरीदना होगा", "फ्री नहीं है")
    if not is_paid and any(m in text for m in _PAID_MARKERS):
        is_paid = True

    key = re.sub(r"\s+", " ", name).strip().lower()
    return {
        "name": key, "text": name,
        "status": status, "platform": platform,
        "genre": genre, "episodes": episodes,
        "description": description, "link": link, "image": None,
        "format": "LIGHT_PRO",
        "is_paid": is_paid,
        "link_btn_text": link_btn_text,
        "message_id": getattr(message, "id", 0),
        "caption": text, "story_type": genre
    }


def extract_light_format(message):
    text = get_text(message)
    if not text:
        return None

    # photo: works for Telethon (photo object) and any truthy value
    has_photo = bool(getattr(message, "photo", None))
    if not (all(x in text for x in ["♨️", "🔰", "🖥"]) and ("🧩" in text or "🗓" in text) and has_photo):
        return None

    # Name: first ♨️ line ONLY — must NOT be the "Story Description" line
    name_match = re.search(r"^♨️(?!.*Description).*?:\s*(.+)", text, re.MULTILINE)
    status_match = re.search(r"🔰.*?:\s*(.+)", text)
    platform_match = re.search(r"🖥.*?:\s*(.+)", text)
    genre_match = re.search(r"(?:🧩|🗓).*?:\s*(.+)", text)

    if not name_match:
        return None

    def _clean(s):
        """Strip HTML tags and extra whitespace from extracted field."""
        return re.sub(r"<[^>]+>", "", s).strip()

    name     = _clean(name_match.group(1))
    status   = _clean(status_match.group(1)) if status_match else "Unknown"
    platform = _clean(platform_match.group(1)) if platform_match else "Unknown"
    genre    = _clean(genre_match.group(1)) if genre_match else "Unknown"

    lines = text.split("\n")
    desc_started = False
    desc_lines = []

    for l in lines:
        if "Description" in l or "\U0001f4dd" in l:
            desc_started = True
            continue

        if desc_started:
            stripped = l.strip()
            if stripped.startswith(">"):
                desc_lines.append(stripped.lstrip("> ").strip())
            elif stripped:  # also collect plain lines after Description header
                desc_lines.append(stripped)

    description = "\n".join(desc_lines).strip()

    # Extract best link using priority scoring
    link, link_btn_text, is_paid = _extract_best_link(message)

    # Also detect is_paid from caption text
    _PAID_MARKERS = ("paid story", "after purchase", "खरीदना होगा", "फ्री नहीं है")
    if not is_paid and any(m in text for m in _PAID_MARKERS):
        is_paid = True

    key = re.sub(r"\s+", " ", name).strip().lower()
    return {
        "name": key,
        "text": name,
        "status": status,
        "platform": platform,
        "genre": genre,
        "description": description,
        "link": link,
        "image": None,
        "format": "LIGHT",
        "is_paid": is_paid,
        "link_btn_text": link_btn_text,
        "message_id": getattr(message, "id", 0),
        "caption": text,
        "story_type": genre
    }


def parse_story(message):

    # Try Light Pro first (superset of Light — must come before Light check)
    light_pro_data = extract_light_pro_format(message)
    if light_pro_data:
        if not light_pro_data.get("link"):
            light_pro_data["link"] = extract_link(message)
        if not light_pro_data.get("link"):
            return None
        print("[PARSER] Saved Light Pro story:", light_pro_data.get("text"), "| link:", light_pro_data.get("link"))
        return light_pro_data

    # Try Light format next
    light_data = extract_light_format(message)
    if light_data:
        if not light_data.get("link"):
            light_link = extract_link(message)
            light_data["link"] = light_link
        if not light_data.get("link"):
            return None
        print("[PARSER] Saved Light story:", light_data.get("text"), "| link:", light_data.get("link"))
        return light_data

    text = get_text(message)

    if not text:
        return None

    name = extract_name(text)

    if not name:
        return None

    link = extract_link(message)

    if not link:
        return None

    story_type = extract_story_type(text)

    # normalised key for lookups
    key = re.sub(r"\s+", " ", name).strip().lower()

    return {
        "name": key,
        "text": name,
        "link": link,
        "message_id": getattr(message, "id", 0),
        "caption": text,
        "story_type": story_type
    }
