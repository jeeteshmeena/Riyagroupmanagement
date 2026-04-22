"""
Sarvam AI — Text to Speech helper
Converts text to Hindi voice using Sarvam API and returns raw WAV bytes.
"""
import asyncio
import base64
import logging
import io

import requests

logger = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# Map the short voice names stored in config to Sarvam speaker IDs
_SPEAKER_MAP = {
    # Male
    "sarvam:default-amit":   "anushka",   # placeholder — update per Sarvam docs
    "sarvam:default-arvind": "manisha",
    "sarvam:default-sumit":  "vidya",
    "sarvam:default-puneet": "arjun",
    # Female
    "sarvam:default-meera":   "meera",
    "sarvam:default-smriti":  "smriti",
    "sarvam:default-pallavi": "pallavi",
    "sarvam:default-shilpa":  "shilpa",
}

# Sarvam actual speaker IDs (male)
_MALE_SPEAKERS   = ["anushka", "vikram", "arjun", "advaith"]
# Sarvam actual speaker IDs (female)
_FEMALE_SPEAKERS = ["meera", "smriti", "pallavi", "shilpa"]


def _resolve_speaker(voice_id: str, gender: str = "female") -> str:
    """Resolve a config voice_id to an actual Sarvam speaker string."""
    if voice_id and voice_id in _SPEAKER_MAP:
        return _SPEAKER_MAP[voice_id]
    # Fallback
    return _FEMALE_SPEAKERS[0] if gender == "female" else _MALE_SPEAKERS[0]


def _do_tts(text: str, speaker: str, api_key: str) -> bytes:
    """Blocking HTTP call — run in thread from async context."""
    # Sarvam supports max ~500 chars per call — truncate safely
    text = text[:500]
    payload = {
        "inputs": [text],
        "target_language_code": "hi-IN",
        "speaker": speaker,
        "pitch": 0,
        "pace": 1.05,
        "loudness": 1.5,
        "speech_sample_rate": 8000,
        "enable_preprocessing": True,
        "model": "bulbul:v1"
    }
    headers = {
        "Content-Type": "application/json",
        "api-subscription-key": api_key
    }
    resp = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    audio_b64 = data["audios"][0]
    return base64.b64decode(audio_b64)


async def synthesize(text: str, voice_id: str, api_key: str, gender: str = "female") -> bytes:
    """Async wrapper — returns WAV bytes or raises on failure."""
    speaker = _resolve_speaker(voice_id, gender)
    # Strip HTML tags before sending to TTS
    import re
    clean = re.sub(r"<[^>]+>", "", text)
    return await asyncio.to_thread(_do_tts, clean, speaker, api_key)
