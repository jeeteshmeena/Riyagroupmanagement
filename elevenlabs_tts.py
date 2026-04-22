"""
ElevenLabs TTS — Human-quality voice synthesis for Hindi & Indian languages
Model: eleven_multilingual_v2 (supports Hindi natively with language_code='hi')

For best Hindi quality:
  - Use eleven_multilingual_v2 with language_code='hi'
  - Lower stability = more natural variation in Hindi prosody
  - Higher style = more expressive/emotional delivery

Recommended female voices for Hindi:
  Aria       : 9BWtsMINqrJLrRacOk9x  (warm, expressive — works well for Hindi)
  Charlotte  : XB0fDUnXU5powFXDhCwa  (clear, professional)
  Matilda    : XrExE9yKIg1WjnnlVkGX  (nurturing, warm)
  Sarah      : EXAVITQu4vr4xnSDxMaL  (gentle, neutral)

Or use your own cloned Indian voice from elevenlabs.io/voice-lab
"""
import asyncio
import logging
import re

import requests

logger = logging.getLogger(__name__)

ELEVENLABS_BASE  = "https://api.elevenlabs.io/v1"

# Primary model — best quality Hindi + Hinglish
ELEVENLABS_MODEL  = "eleven_multilingual_v2"
# Turbo model — faster, also good for Hindi (use as fallback if primary slow)
ELEVENLABS_TURBO  = "eleven_turbo_v2_5"

# Default female voice — warm and expressive (works great with Hindi sentence structure)
DEFAULT_VOICE_ID = "9BWtsMINqrJLrRacOk9x"  # Aria

# Voice settings tuned for Hindi/Hinglish — more natural than English defaults
# Key differences from English settings:
#   - stability=0.35: allows natural pitch variation typical in Hindi speech
#   - style=0.55: expressiveness matches how Hindi is emotionally spoken
#   - similarity_boost=0.80: stays close to voice but allows natural Hindi articulation
_VOICE_SETTINGS_HINDI = {
    "stability":         0.35,   # More natural pitch variation for Hindi
    "similarity_boost":  0.80,   # Close to voice character
    "style":             0.55,   # Expressive — Hindi has more emotional delivery
    "use_speaker_boost": True    # Clarity boost
}


def _do_tts(text: str, voice_id: str, api_key: str) -> bytes:
    """Blocking HTTP call — run in asyncio thread."""
    # ElevenLabs language_code for Hindi
    # The API uses BCP-47 language tags: 'hi' for Hindi
    text = text[:2500]  # Under the 2500 char safe limit
    url  = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key":   api_key,
        "Content-Type": "application/json",
        "Accept":       "audio/mpeg",
    }
    payload = {
        "text":           text,
        "model_id":       ELEVENLABS_MODEL,
        "language_code":  "hi",            # <-- Hindi language code (BCP-47)
        "voice_settings": _VOICE_SETTINGS_HINDI,
        "output_format":  "mp3_44100_128", # 128kbps MP3 — best for voice notes
    }
    logger.info(f"[EL-TTS] voice={voice_id!r}, text_len={len(text)}, lang=hi")
    resp = requests.post(url, json=payload, headers=headers, timeout=30)

    if not resp.ok:
        err = resp.text[:400]
        logger.error(f"[EL-TTS] HTTP {resp.status_code}: {err}")

        # Fallback: try turbo model if primary model has issues
        if resp.status_code in (400, 422) and "language_code" in err:
            logger.info("[EL-TTS] Retrying without language_code (model may not support param)")
            payload.pop("language_code", None)
            payload["model_id"] = ELEVENLABS_TURBO
            resp2 = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp2.ok:
                logger.info(f"[EL-TTS] Turbo fallback OK, audio_len={len(resp2.content)} bytes")
                return resp2.content
            resp2.raise_for_status()

        resp.raise_for_status()

    logger.info(f"[EL-TTS] OK, audio_len={len(resp.content)} bytes")
    return resp.content  # Raw MP3 bytes


async def synthesize(text: str, voice_id: str, api_key: str) -> bytes:
    """Async wrapper — returns MP3 bytes or raises on failure."""
    vid   = voice_id or DEFAULT_VOICE_ID
    clean = re.sub(r"<[^>]+>", "", text).strip()
    if not clean:
        raise ValueError("Empty text after HTML strip")
    return await asyncio.to_thread(_do_tts, clean, vid, api_key)
