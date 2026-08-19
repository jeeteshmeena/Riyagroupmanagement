import re

# ── Conversational & Small Talk Recognition ──────────────────────────────────
_CONVERSATIONAL_EXACT = {
    # Greetings & Check-ins
    "hi", "hello", "hey", "hii", "hiii", "heyy", "hlo", "hlw", "namaste", "namaskar", "suno", "sunona", "oye",
    "kaise ho", "kaisi ho", "kya haal hai", "kya haal", "sab badhiya", "kya chal raha hai", "kya chal raha",
    "kya ho raha hai", "kya ho raha", "kya kar rahi ho", "kya kar rahe ho", "kya kar rhi ho", "kya kr rhi ho",
    "how are you", "how r u", "how are u", "what are you doing", "what r u doing", "whats up", "what's up",
    "good morning", "good night", "good evening", "good afternoon", "gm", "gn", "bye", "tata", "alvida",
    "take care", "shubh ratri", "suprabhat",
    
    # Casual Chat & Emotion
    "love you", "i love you", "love u", "i like you", "miss you", "miss u", "kuch sunao", "kuch batao",
    "aur batao", "aur btao", "aur sunao", "aur sab", "aur kuch", "theek hai", "thik hai", "achha", "achha ji",
    "sahi hai", "badhiya", "mast", "khoob", "pata nahi", "nahi pata", "kuch nahi", "kuch ni",
    "tum kon ho", "who are you", "who r u", "tum kaun ho", "aap kaun ho", "aap kon ho", "naam kya hai",
    "your name", "what is your name", "kaha se ho", "where are you from", "kaha rehti ho",
    "shukriya", "dhanyawad", "thank you", "thanks", "welcome", "sorry", "maaf karna",
    "ok", "okay", "hmmm", "hmm", "ha", "haan", "nah", "nahi", "no", "yes", "yup", "nope"
}

_CONVERSATIONAL_PREFIXES = (
    "kaise ho", "kaisi ho", "kya haal", "kya kar rahi", "kya kar rahe", "how are you", "what are you",
    "kya chal raha", "aur batao", "kuch sunao", "kuch batao", "tum kaun", "tum kon", "who are you",
    "who is", "aap kaun", "aap kon", "kaha se ho", "where are you", "love you", "miss you",
    "kya tum", "kya aap", "can you", "tum kya"
)


_CONVERSATIONAL_KEYWORDS = {
    "maine", "humne", "tumne", "aapne", "mujhe", "tujhe", "humko", "tumko", "aapko",
    "kaise", "kaisi", "kyun", "kyu", "kya", "kab", "kaha", "kahan", "kaun", "kon",
    "bolo", "batao", "sunao", "kaho", "padho", "dekho", "samjho", "samjhao",
    "karu", "karun", "karein", "kare", "karte", "karti", "karta", "karega", "karegi",
    "raha", "rahi", "rahe", "tha", "thi", "the", "hai", "hain", "hoon", "hun",
    "hoga", "hogi", "honge", "chahiye", "chahta", "chahti", "chahte",
    "achha", "accha", "theek", "thik", "sahi", "galat", "pata", "malum", "maloom",
    "pagal", "bhai", "yaar", "dost", "meri", "mera", "mere", "tera", "teri", "tere",
    "apna", "apni", "apne", "kuch", "sab", "kisi", "koi", "sirf", "bas", "today",
    "tomorrow", "yesterday", "always", "never", "because", "think", "doing"
}


def is_conversational_text(text: str) -> bool:
    """Check if the input is small-talk, greeting, sentence chat, or casual conversation."""
    if not text:
        return False
    t = text.strip().lower()
    alphanumeric = re.sub(r"[^\w\s]", "", t).strip()
    
    if not alphanumeric:
        return True  # Pure emojis / punctuation is conversational
        
    if t in _CONVERSATIONAL_EXACT or alphanumeric in _CONVERSATIONAL_EXACT:
        return True
        
    for p in _CONVERSATIONAL_PREFIXES:
        if t.startswith(p) or alphanumeric.startswith(p):
            return True

    # Check multi-word sentence conversational density
    words = [w for w in re.findall(r"\w+", t) if len(w) >= 2]
    if len(words) >= 4 and sum(1 for w in words if w in _CONVERSATIONAL_KEYWORDS) >= 2:
        return True

    return False


def is_valid_query(text: str) -> bool:
    """Validate whether input text qualifies as a legitimate story search query."""
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
    # If text is only emojis and whitespace/punctuation, not a valid story query
    alphanumeric = re.sub(r"[^\w\s]", "", t).strip()
    if not alphanumeric:
        return False
    # Conversational phrases are not story search queries
    if is_conversational_text(t):
        return False
    return True
