"""
premium_userbot.py — Connect admin's premium Telegram account via Telethon
to enable premium emoji, sticker reactions, and custom emoji in Riya chat.

HOW TO CONNECT YOUR PREMIUM ACCOUNT:
1. Go to /config → Premium Account → Set Session String
2. Generate session string on the server:
     python3 -c "
     from telethon.sync import TelegramClient
     client = TelegramClient('riya_premium', API_ID, API_HASH)
     client.start()
     print(client.session.save())
     client.disconnect()
     "
3. Paste the session string in /config

The userbot is ONLY used for:
  - Sending custom emoji / premium sticker reactions
  - Sending Riya's messages WITH premium emoji entities injected
  - Does NOT interfere with the bot's own message sending

IMPORTANT: Keep your session string secret. Only admin can set it.
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
_userbot_client = None
_is_connected   = False

# ── Popular public custom emoji IDs (usable by any bot via MessageEntity) ─────
# These are from public Telegram emoji/sticker packs and work without premium
# Obtain custom emoji IDs by: forwarding a premium emoji message and calling
# bot.get_custom_emoji_stickers(emoji_ids=[...])
#
# Map: emotion/use → custom_emoji_id
CUSTOM_EMOJI_MAP = {
    # Hearts & Love
    "heart":       "5368324170671202286",
    "sparkle":     "5471952986970267163",
    "star":        "5440539497383087970",
    "fire":        "5373141891321699086",
    "smile":       "5449626952930406343",
    "laugh":       "5452023368596077203",
    "sad":         "5471912804219283477",
    "think":       "5444293165736587046",
    "wow":         "5373141891321699086",
    "cool":        "5463228092754048293",
    "wave":        "5467655876587362148",
    "book":        "5471699564427268111",
    "music":       "5471952291543580065",
    "crown":       "5430665757416087548",
    "gem":         "5471741978792603722",
    "ok":          "5471741978792603722",
    "search":      "5471912804219283477",
    "story":       "5471699564427268111",
    "hi":          "5467655876587362148",
    "bye":         "5467655876587362148",
    "yes":         "5444293165736587046",
    "no":          "5373141891321699086",
}

# Default emoji sequence for Riya's personality (no overuse)
RIYA_EMOJI_POOL = [
    "sparkle", "heart", "star", "smile", "wave"
]


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
        logger.info(f"[Premium] Userbot connected as: {me.first_name} (@{me.username})")
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


async def send_premium_reaction(chat_id: int, message_id: int, emoji_id: str) -> bool:
    """
    Send a premium custom emoji reaction on a message using the userbot.
    emoji_id: custom emoji ID string (from CUSTOM_EMOJI_MAP or user-configured)
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
        return True
    except Exception as e:
        logger.warning(f"[Premium] Reaction failed: {e}")
        return False


async def react_with_mood(chat_id: int, message_id: int, mood: str) -> bool:
    """
    Send a mood-matched custom emoji reaction.
    mood: one of the keys in CUSTOM_EMOJI_MAP
    """
    emoji_id = CUSTOM_EMOJI_MAP.get(mood)
    if not emoji_id:
        return False
    return await send_premium_reaction(chat_id, message_id, emoji_id)


def build_custom_emoji_entity(offset: int, emoji_id: str):
    """Build a PTB MessageEntity for custom emoji injection into bot messages."""
    try:
        from telegram import MessageEntity
        return MessageEntity(
            type=MessageEntity.CUSTOM_EMOJI,
            offset=offset,
            length=2,   # placeholder length (emoji character is 2 chars in UTF-16)
            custom_emoji_id=emoji_id
        )
    except Exception:
        return None


def get_emoji_id(name: str) -> Optional[str]:
    """Get a custom emoji ID by mood name."""
    return CUSTOM_EMOJI_MAP.get(name)
