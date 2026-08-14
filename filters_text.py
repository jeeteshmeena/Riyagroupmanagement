def is_valid_query(text):
    if not text:
        return False
    t = text.strip()
    if t.isdigit():
        return True
    if len(t) < 2 or len(t) > 70:
        return False
    lower_t = t.lower()
    # Ignore request form button texts from triggering searches
    if "start request form" in lower_t or "sᴛᴀʀᴛ ʀᴇqᴜᴇsᴛ ꜰᴏʀᴍ" in lower_t:
        return False
    # Ignore URLs / Telegram links from triggering story searches
    if any(url_kw in lower_t for url_kw in ["http://", "https://", "t.me/", "telegram.me/", "www."]):
        return False
    ignore = ["hi", "hello", "thanks", "ok", "good morning", "good night"]
    if lower_t in ignore:
        return False
    return True
