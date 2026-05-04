"""
premium_userbot.py — Connect admin's premium Telegram account via Telethon
to enable premium emoji reactions, custom emoji in messages, and premium stickers.

HOW TO CONNECT:
  /config → Premium Account → Set API ID / API Hash / Session String → Connect Now

The userbot is used for:
  - Sending custom emoji reactions (premium animated reactions)
  - Sending messages WITH premium emoji via the user account
  - Forwarding premium stickers on behalf of the premium account
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
_userbot_client = None
_is_connected   = False

# ── Custom emoji document IDs (verified public Telegram premium emoji) ─────────
# These are document IDs of custom animated emoji from Telegram's own packs.
# They work in reactions via any premium account.
CUSTOM_EMOJI_MAP = {
    "heart":    "5368324170671202286",
    "sparkle":  "5471952986970267163",
    "star":     "5440539497383087970",
    "fire":     "5373141891321699086",
    "smile":    "5449626952930406343",
    "laugh":    "5452023368596077203",
    "sad":      "5471912804219283477",
    "think":    "5444293165736587046",
    "wow":      "5373141891321699086",
    "cool":     "5463228092754048293",
    "wave":     "5467655876587362148",
    "book":     "5471699564427268111",
    "music":    "5471952291543580065",
    "crown":    "5430665757416087548",
    "gem":      "5471741978792603722",
    "ok":       "5471741978792603722",
    "search":   "5471912804219283477",
    "story":    "5471699564427268111",
    "hi":       "5467655876587362148",
    "bye":      "5467655876587362148",
    "yes":      "5444293165736587046",
    "no":       "5373141891321699086",
}

RIYA_EMOJI_POOL = ["sparkle", "heart", "star", "smile", "wave"]


# ── Connect / Disconnect ───────────────────────────────────────────────────────

async def connect_userbot(session_str: str, api_id: int, api_hash: str) -> bool:
    """Connect the admin's premium Telegram account as a userbot."""
    global _userbot_client, _is_connected
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        _userbot_client = TelegramClient(
            StringSession(session_str),
            api_id,
            api_hash,
            device_model="Riya Premium",
            system_version="Linux",
            app_version="1.0",
        )
        await _userbot_client.start()
        me = await _userbot_client.get_me()
        _is_connected = True
        logger.info(f"[Premium] Userbot connected as: {me.first_name} (@{me.username}) — Premium: {me.premium}")
        return True
    except Exception as e:
        logger.error(f"[Premium] Userbot connect failed: {e}")
        _is_connected = False
        return False


async def disconnect_userbot():
    global _userbot_client, _is_connected
    if _userbot_client:
        try:
            await _userbot_client.disconnect()
        except Exception:
            pass
    _userbot_client = None
    _is_connected   = False


def is_connected() -> bool:
    return _is_connected and _userbot_client is not None


def get_client():
    """Return the raw Telethon client (None if not connected)."""
    return _userbot_client if _is_connected else None


# ── Premium Reactions ──────────────────────────────────────────────────────────

async def send_premium_reaction(chat_id: int, message_id: int, emoji_id: str) -> bool:
    """
    Send a premium custom emoji reaction using the userbot.
    emoji_id: document ID string from CUSTOM_EMOJI_MAP
    """
    if not is_connected():
        return False
    try:
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionCustomEmoji
        await _userbot_client(SendReactionRequest(
            peer=chat_id,
            msg_id=message_id,
            reaction=[ReactionCustomEmoji(document_id=int(emoji_id))]
        ))
        logger.debug(f"[Premium] Custom emoji reaction sent → msg {message_id}")
        return True
    except Exception as e:
        logger.warning(f"[Premium] Reaction failed: {e}")
        return False


async def react_with_mood(chat_id: int, message_id: int, mood: str) -> bool:
    """Send a mood-matched custom emoji reaction."""
    emoji_id = CUSTOM_EMOJI_MAP.get(mood)
    if not emoji_id:
        return False
    return await send_premium_reaction(chat_id, message_id, emoji_id)


# ── Premium Sticker Sending ────────────────────────────────────────────────────

async def send_premium_sticker(chat_id, sticker_id: int) -> bool:
    """
    Send a premium sticker (document) to a chat via the userbot.
    sticker_id: the document ID of the premium sticker.
    """
    if not is_connected():
        return False
    try:
        from telethon.tl.functions.messages import SendMediaRequest
        from telethon.tl.types import InputMediaDocument, InputDocument
        await _userbot_client(SendMediaRequest(
            peer=chat_id,
            media=InputMediaDocument(
                id=InputDocument(id=sticker_id, access_hash=0, file_reference=b"")
            ),
            message="",
            random_id=int.from_bytes(os.urandom(8), "big", signed=True),
        ))
        return True
    except Exception as e:
        logger.warning(f"[Premium] Sticker send failed: {e}")
        return False


# ── Message with Premium Emoji (sent via userbot) ─────────────────────────────

async def send_with_premium_emoji(chat_id, text: str,
                                   reply_to: Optional[int] = None,
                                   parse_mode: str = "html") -> bool:
    """
    Send a text message via the userbot account so that premium animated
    emoji in the text are rendered correctly for all users.
    Falls back silently if userbot is not connected.
    """
    if not is_connected():
        return False
    try:
        kwargs = dict(message=text, parse_mode=parse_mode)
        if reply_to:
            kwargs["reply_to"] = reply_to
        await _userbot_client.send_message(chat_id, **kwargs)
        return True
    except Exception as e:
        logger.warning(f"[Premium] send_with_premium_emoji failed: {e}")
        return False


# ── PTB Entity Helper ──────────────────────────────────────────────────────────

def build_custom_emoji_entity(offset: int, emoji_id: str):
    """Build a PTB MessageEntity for custom emoji injection into bot messages."""
    try:
        from telegram import MessageEntity
        return MessageEntity(
            type=MessageEntity.CUSTOM_EMOJI,
            offset=offset,
            length=2,
            custom_emoji_id=emoji_id
        )
    except Exception:
        return None


def get_emoji_id(name: str) -> Optional[str]:
    """Get a custom emoji document ID by mood name."""
    return CUSTOM_EMOJI_MAP.get(name)
