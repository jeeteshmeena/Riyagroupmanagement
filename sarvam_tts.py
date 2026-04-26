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

# ── ONLY valid speakers for bulbul:v2 (from live API) ───────────────────────
_FEMALE_SPEAKERS = ["anushka", "manisha", "vidya", "arya"]
_MALE_SPEAKERS   = ["abhilash", "karun", "hitesh"]

# Config voice_id → real Sarvam speaker (bulbul:v2 only has 7 speakers)
_SPEAKER_MAP = {
    # Female
    "sarvam:default-anushka":  "anushka",
    "sarvam:default-manisha":  "manisha",
    "sarvam:default-vidya":    "vidya",
    "sarvam:default-arya":     "arya",
    # Male
    "sarvam:default-abhilash": "abhilash",
    "sarvam:default-karun":    "karun",
    "sarvam:default-hitesh":   "hitesh",
    # All legacy / wrong names → remap to nearest valid
    "sarvam:default-meera":    "anushka",
    "sarvam:default-priya":    "manisha",
    "sarvam:default-neha":     "vidya",
    "sarvam:default-pooja":    "arya",
    "sarvam:default-simran":   "anushka",
    "sarvam:default-kavya":    "manisha",
    "sarvam:default-ishita":   "vidya",
    "sarvam:default-shreya":   "arya",
    "sarvam:default-roopa":    "anushka",
    "sarvam:default-pavithra": "manisha",
    "sarvam:default-maitreyi": "vidya",
    "sarvam:default-diya":     "arya",
    "sarvam:default-misha":    "anushka",
    "sarvam:default-maya":     "manisha",
    "sarvam:default-ritu":     "vidya",
    "sarvam:default-arvind":   "abhilash",
    "sarvam:default-amol":     "karun",
    "sarvam:default-amartya":  "hitesh",
    "sarvam:default-neel":     "abhilash",
    "sarvam:default-vian":     "karun",
    "sarvam:default-arjun":    "hitesh",
    "sarvam:default-rahul":    "abhilash",
    "sarvam:default-rohan":    "karun",
    "sarvam:default-amit":     "hitesh",
    "sarvam:default-dev":      "abhilash",
    "sarvam:default-varun":    "karun",
    "sarvam:default-kabir":    "hitesh",
    "sarvam:default-advait":   "abhilash",
    "sarvam:default-sumit":    "karun",
    "sarvam:default-puneet":   "hitesh",
    "sarvam:default-aditya":   "abhilash",
}


def _resolve_speaker(voice_id: str, gender: str = "female") -> str:
    if voice_id and voice_id in _SPEAKER_MAP:
        return _SPEAKER_MAP[voice_id]
    # Fallback to first valid speaker for the gender
    return _FEMALE_SPEAKERS[0] if gender != "male" else _MALE_SPEAKERS[0]


def _is_english_text(text: str) -> bool:
    """Return True if text is primarily English (not Hindi/Devanagari)."""
    devanagari = sum(1 for c in text if '\u0900' <= c <= '\u097f')
    ratio = devanagari / max(len(text.strip()), 1)
    hinglish_kw = [
        "main ", "mujhe", "karo", "hain", "nahi", "kaise", "kya ", "tera",
        "mera", "yaar", "bhai", "bolo", "batao", "chahiye", "acha",
        "theek", "bahut", "zyada", "abhi", "kyun", "lekin", "phir",
    ]
    hinglish_hits = sum(1 for kw in hinglish_kw if kw in text.lower())
    return ratio < 0.05 and hinglish_hits < 2


def _do_tts(text: str, speaker: str, api_key: str) -> bytes:
    """Blocking HTTP call — run in asyncio thread."""
    text = text[:490]
    payload = {
        "inputs": [text],
        "target_language_code": "hi-IN",
        "speaker": speaker,
        "pitch":   0,
        "pace":    1.1,
        "loudness": 1.8,
        "speech_sample_rate": 22050,
        "enable_preprocessing": True,
        "model": "bulbul:v2"
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

