"""
ElevenLabs TTS — Human-quality voice synthesis for Hindi, English & Hinglish
Model: eleven_multilingual_v2 (supports Hindi + English natively)

Voice Expressiveness Guide:
  - stability 0.10-0.25 → Maximum emotion, natural pitch swings (ideal for conversational AI)
  - style      0.75-0.90 → Dramatic, intense, emotionally rich delivery
  - similarity_boost 0.85 → Preserves voice character while allowing natural articulation

Recommended female voices:
  Aria       : 9BWtsMINqrJLrRacOk9x  (warm, expressive — best for Hinglish/Hindi)
  Charlotte  : XB0fDUnXU5powFXDhCwa  (clear, professional — great for English)
  Matilda    : XrExE9yKIg1WjnnlVkGX  (nurturing, warm)
  Sarah      : EXAVITQu4vr4xnSDxMaL  (gentle, neutral)

Use your own cloned Indian voice from elevenlabs.io/voice-lab for best results.
"""
import asyncio
import logging
import re

import requests

logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"

# Primary model — true multilingual (Hindi + English + Hinglish)
ELEVENLABS_MODEL = "eleven_multilingual_v2"
# Turbo fallback — slightly faster
ELEVENLABS_TURBO = "eleven_turbo_v2_5"

# Default female voice — Aria (warm, expressive, works great for Hinglish)
DEFAULT_VOICE_ID = "9BWtsMINqrJLrRacOk9x"  # Aria

# ── Highly expressive settings — maximum human-like emotion ────────────────────
# stability=0.18: very low → maximum natural pitch variation (laughter, sadness,
#   surprise, anger all come through naturally instead of flat robotic tone)
# style=0.82: very high → Riya sounds passionate, intense, genuinely emotional
# similarity_boost=0.85: stays true to the voice character
_VOICE_SETTINGS_EXPRESSIVE = {
    "stability":         0.18,   # LOW = maximum emotional range (human-like variation)
    "similarity_boost":  0.85,   # High voice fidelity
    "style":             0.82,   # HIGH = passionate, intense, emotional delivery
    "use_speaker_boost": True,   # Clarity and presence boost
}


def _detect_language(text: str) -> str:
    """
    Detect primary language of the text.
    Returns 'hi' for Hindi/Hinglish (Devanagari + common Hinglish),
    returns 'en' for predominantly English text.
    """
    devanagari_count = sum(1 for c in text if '\u0900' <= c <= '\u097f')
    ratio = devanagari_count / max(len(text.strip()), 1)
    # Hinglish heuristic: common Hindi words in Roman script
    hinglish_kw = [
        "main ", "mujhe", "karo", "hain", "nahi", "kaise", "kya ", "tera",
        "mera", "yaar", "bhai", "didi", "bolo", "batao", "chahiye", "acha",
        "theek", "bahut", "zyada", "abhi", "kyun", "lekin", "phir", "agar",
    ]
    lower = text.lower()
    hinglish_hits = sum(1 for kw in hinglish_kw if kw in lower)

    if ratio > 0.10:          # >10% Devanagari → Hindi
        return "hi"
    if hinglish_hits >= 2:    # 2+ Hinglish keywords → treat as Hindi/Hinglish
        return "hi"
    return "en"               # Otherwise English


def _do_tts(text: str, voice_id: str, api_key: str, lang: str = "auto") -> bytes:
    """Blocking HTTP call — run in asyncio thread."""
    text     = text[:2400]
    url      = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers  = {
        "xi-api-key":   api_key,
        "Content-Type": "application/json",
        "Accept":       "audio/mpeg",
    }
    # Determine language code for multilingual model
    if lang == "auto":
        lang = _detect_language(text)

    payload = {
        "text":           text,
        "model_id":       ELEVENLABS_MODEL,
        "language_code":  lang,            # 'hi' Hindi/Hinglish | 'en' English
        "voice_settings": _VOICE_SETTINGS_EXPRESSIVE,
        "output_format":  "mp3_44100_128",  # 128kbps MP3 — best for voice notes
    }
    logger.info(f"[EL-TTS] voice={voice_id!r}, lang={lang!r}, text_len={len(text)}")
    resp = requests.post(url, json=payload, headers=headers, timeout=30)

    if not resp.ok:
        err = resp.text[:400]
        logger.error(f"[EL-TTS] HTTP {resp.status_code}: {err}")

        # Fallback: turbo model without language_code if primary model errors
        if resp.status_code in (400, 422):
            logger.info("[EL-TTS] Retrying with turbo model (no language_code)")
            payload.pop("language_code", None)
            payload["model_id"] = ELEVENLABS_TURBO
            resp2 = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp2.ok:
                logger.info(f"[EL-TTS] Turbo OK, audio_len={len(resp2.content)} bytes")
                return resp2.content
            resp2.raise_for_status()

        resp.raise_for_status()

    logger.info(f"[EL-TTS] OK, audio_len={len(resp.content)} bytes")
    return resp.content  # Raw MP3 bytes


async def synthesize(text: str, voice_id: str, api_key: str, lang: str = "auto") -> bytes:
    """Async wrapper — returns MP3 bytes or raises on failure."""
    vid   = voice_id or DEFAULT_VOICE_ID
    clean = re.sub(r"<[^>]+>", "", text).strip()
    if not clean:
        raise ValueError("Empty text after HTML strip")
    return await asyncio.to_thread(_do_tts, clean, vid, api_key, lang)
