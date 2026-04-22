"""
Sarvam AI — Text to Speech helper
Valid bulbul:v1 speakers (from Sarvam docs):
  Female: meera, pavithra, maitreyi, diya, misha, maya
  Male:   arvind, amol, amartya, neel, vian, arjun
"""
import asyncio
import base64
import logging
import re

import requests

logger = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# ── Verified speaker IDs from Sarvam bulbul:v1 docs ──────────────────────────
_FEMALE_SPEAKERS = ["meera", "pavithra", "maitreyi", "diya", "misha", "maya"]
_MALE_SPEAKERS   = ["arvind", "amol", "amartya", "neel", "vian", "arjun"]

# Config key (stored as "sarvam:default-<name>") → real Sarvam speaker ID
_SPEAKER_MAP = {
    # Female
    "sarvam:default-meera":     "meera",
    "sarvam:default-pavithra":  "pavithra",
    "sarvam:default-maitreyi":  "maitreyi",
    "sarvam:default-diya":      "diya",
    "sarvam:default-misha":     "misha",
    "sarvam:default-maya":      "maya",
    # Male
    "sarvam:default-arvind":    "arvind",
    "sarvam:default-amol":      "amol",
    "sarvam:default-amartya":   "amartya",
    "sarvam:default-neel":      "neel",
    "sarvam:default-vian":      "vian",
    "sarvam:default-arjun":     "arjun",
    # Legacy names kept for backward compat
    "sarvam:default-smriti":    "meera",
    "sarvam:default-pallavi":   "pavithra",
    "sarvam:default-shilpa":    "maitreyi",
    "sarvam:default-amit":      "arvind",
    "sarvam:default-sumit":     "amol",
    "sarvam:default-puneet":    "arjun",
}


def _resolve_speaker(voice_id: str, gender: str = "female") -> str:
    if voice_id and voice_id in _SPEAKER_MAP:
        return _SPEAKER_MAP[voice_id]
    return _FEMALE_SPEAKERS[0] if gender != "male" else _MALE_SPEAKERS[0]


def _do_tts(text: str, speaker: str, api_key: str) -> bytes:
    """Blocking HTTP call — run in thread."""
    text = text[:490]  # Sarvam limit is ~500 chars
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
    logger.info(f"[TTS] Calling Sarvam: speaker={speaker!r}, text_len={len(text)}")
    resp = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=30)
    if not resp.ok:
        logger.error(f"[TTS] Sarvam HTTP {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
    data = resp.json()
    audio_b64 = data["audios"][0]
    logger.info(f"[TTS] Sarvam OK, audio_b64_len={len(audio_b64)}")
    return base64.b64decode(audio_b64)


async def synthesize(text: str, voice_id: str, api_key: str, gender: str = "female") -> bytes:
    """Async wrapper — returns WAV bytes or raises on failure."""
    speaker  = _resolve_speaker(voice_id, gender)
    clean    = re.sub(r"<[^>]+>", "", text).strip()
    if not clean:
        raise ValueError("Empty text after HTML strip")
    return await asyncio.to_thread(_do_tts, clean, speaker, api_key)
