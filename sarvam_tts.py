"""
Sarvam AI — Text to Speech helper
Speaker list verified from live API error response (bulbul:v1):
  anushka, abhilash, manisha, vidya, arya, karun, hitesh, aditya, ritu,
  priya, neha, rahul, pooja, rohan, simran, kavya, amit, dev, ishita,
  shreya, ratan, varun, manan, sumit, roopa, kabir, aayan, shubh,
  ashutosh, advait (and more)
"""
import asyncio
import base64
import logging
import re

import requests

logger = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# ── Gender-classified speakers (verified from live API) ───────────────────────
_FEMALE_SPEAKERS = [
    "anushka", "manisha", "vidya", "ritu", "priya", "neha",
    "pooja", "simran", "kavya", "ishita", "shreya", "roopa"
]
_MALE_SPEAKERS = [
    "abhilash", "karun", "hitesh", "aditya", "rahul", "rohan",
    "amit", "dev", "ratan", "varun", "manan", "sumit",
    "kabir", "aayan", "ashutosh", "advait"
]

# Config voice_id → real Sarvam speaker name
_SPEAKER_MAP = {
    # Female (what config panel saves)
    "sarvam:default-anushka":  "anushka",
    "sarvam:default-manisha":  "manisha",
    "sarvam:default-vidya":    "vidya",
    "sarvam:default-ritu":     "ritu",
    "sarvam:default-priya":    "priya",
    "sarvam:default-neha":     "neha",
    "sarvam:default-pooja":    "pooja",
    "sarvam:default-simran":   "simran",
    "sarvam:default-kavya":    "kavya",
    "sarvam:default-ishita":   "ishita",
    "sarvam:default-shreya":   "shreya",
    "sarvam:default-roopa":    "roopa",
    # Male
    "sarvam:default-abhilash": "abhilash",
    "sarvam:default-karun":    "karun",
    "sarvam:default-hitesh":   "hitesh",
    "sarvam:default-aditya":   "aditya",
    "sarvam:default-rahul":    "rahul",
    "sarvam:default-rohan":    "rohan",
    "sarvam:default-amit":     "amit",
    "sarvam:default-dev":      "dev",
    "sarvam:default-ratan":    "ratan",
    "sarvam:default-varun":    "varun",
    "sarvam:default-manan":    "manan",
    "sarvam:default-sumit":    "sumit",
    "sarvam:default-kabir":    "kabir",
    "sarvam:default-aayan":    "aayan",
    "sarvam:default-ashutosh": "ashutosh",
    "sarvam:default-advait":   "advait",
    # Legacy / wrong names that may be stored — remap to valid ones
    "sarvam:default-meera":    "anushka",
    "sarvam:default-pavithra": "priya",
    "sarvam:default-maitreyi": "manisha",
    "sarvam:default-diya":     "neha",
    "sarvam:default-misha":    "simran",
    "sarvam:default-maya":     "kavya",
    "sarvam:default-arvind":   "abhilash",
    "sarvam:default-amol":     "hitesh",
    "sarvam:default-amartya":  "aditya",
    "sarvam:default-neel":     "rahul",
    "sarvam:default-vian":     "varun",
    "sarvam:default-arjun":    "kabir",
    "sarvam:default-pallavi":  "priya",
    "sarvam:default-smriti":   "ishita",
    "sarvam:default-shilpa":   "shreya",
    "sarvam:default-puneet":   "sumit",
}


def _resolve_speaker(voice_id: str, gender: str = "female") -> str:
    if voice_id and voice_id in _SPEAKER_MAP:
        return _SPEAKER_MAP[voice_id]
    # Fallback to first valid speaker for the gender
    return _FEMALE_SPEAKERS[0] if gender != "male" else _MALE_SPEAKERS[0]


def _do_tts(text: str, speaker: str, api_key: str) -> bytes:
    """Blocking HTTP call — run in asyncio thread."""
    text = text[:490]
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
        logger.error(f"[TTS] Sarvam HTTP {resp.status_code}: {resp.text[:400]}")
        resp.raise_for_status()
    data = resp.json()
    audio_b64 = data["audios"][0]
    logger.info(f"[TTS] Sarvam OK, audio_b64_len={len(audio_b64)}")
    return base64.b64decode(audio_b64)


async def synthesize(text: str, voice_id: str, api_key: str, gender: str = "female") -> bytes:
    """Async wrapper — returns WAV bytes or raises on failure."""
    speaker = _resolve_speaker(voice_id, gender)
    clean   = re.sub(r"<[^>]+>", "", text).strip()
    if not clean:
        raise ValueError("Empty text after HTML strip")
    return await asyncio.to_thread(_do_tts, clean, speaker, api_key)
