"""
ElevenLabs TTS — Human-quality voice synthesis
Supports Hindi, English, and 29+ Indian/global languages.
Model: eleven_multilingual_v2

Popular female voice IDs (for Indian content):
  Aria      : 9BWtsMINqrJLrRacOk9x  (warm, expressive)
  Sarah     : EXAVITQu4vr4xnSDxMaL  (gentle, clear)
  Charlotte  : XB0fDUnXU5powFXDhCwa  (professional)
  Matilda   : XrExE9yKIg1WjnnlVkGX  (nurturing, warm)
  Elli      : MF3mGyEYCl7XYWbV9V6O  (youthful, energetic)

Get more voices at: https://elevenlabs.io/voice-library
"""
import asyncio
import logging
import re

import requests

logger = logging.getLogger(__name__)

ELEVENLABS_BASE  = "https://api.elevenlabs.io/v1"
ELEVENLABS_MODEL = "eleven_multilingual_v2"

# Default female voice (Aria — warm, expressive, works great with Hindi/English mix)
DEFAULT_VOICE_ID = "9BWtsMINqrJLrRacOk9x"

# Voice settings tuned for natural, human-like Hindi/Hinglish speech
_VOICE_SETTINGS = {
    "stability":        0.40,   # Lower = more expressive/natural variation
    "similarity_boost": 0.75,   # Voice similarity to original
    "style":            0.50,   # Expressiveness / emotion level
    "use_speaker_boost": True   # Enhances clarity
}


def _do_tts(text: str, voice_id: str, api_key: str) -> bytes:
    """Blocking HTTP call — run in asyncio thread."""
    text = text[:3000]   # ElevenLabs supports up to ~5000 chars, keep safe
    url  = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key":   api_key,
        "Content-Type": "application/json",
        "Accept":       "audio/mpeg",
    }
    payload = {
        "text":            text,
        "model_id":        ELEVENLABS_MODEL,
        "voice_settings":  _VOICE_SETTINGS,
        "output_format":   "mp3_44100_128",  # High quality 128kbps MP3
    }
    logger.info(f"[EL-TTS] Calling ElevenLabs: voice={voice_id!r}, text_len={len(text)}")
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if not resp.ok:
        logger.error(f"[EL-TTS] HTTP {resp.status_code}: {resp.text[:400]}")
        resp.raise_for_status()
    logger.info(f"[EL-TTS] OK, audio_len={len(resp.content)} bytes")
    return resp.content   # Raw MP3 bytes


async def synthesize(text: str, voice_id: str, api_key: str) -> bytes:
    """Async wrapper — returns MP3 bytes or raises on failure."""
    vid   = voice_id or DEFAULT_VOICE_ID
    clean = re.sub(r"<[^>]+>", "", text).strip()
    if not clean:
        raise ValueError("Empty text")
    return await asyncio.to_thread(_do_tts, clean, vid, api_key)
