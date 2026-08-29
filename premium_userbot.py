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
    "👋":        "5850698420200675761",
    "♨️":       "5465432711218863135",
    "♨":        "5465432711218863135",
    "🔰":        "6019118553326689234",
    "🖥":       "6019455905827920171",
    "🖥️":      "6019455905827920171",
    "🧩":        "6024065724291488135",
    "🎬":        "5937999673510858217",
    "🛍":        "5456343263340405032",
    "🛍️":       "5456343263340405032",
    "💓":       "6217662152547766453",
    "💗":       "6217662152547766453",
    "🗑️":       "6021413766669801212",
    "🗑":        "6021413766669801212"
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
        await _userbot_client.connect()
        if not await _userbot_client.is_user_authorized():
            raise ValueError(
                "SESSION_STRING is invalid or has expired. "
                "Please generate a new session string using generate_session.py and update it."
            )
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
    Send a reaction using the premium userbot.
    emoji_id: ignored here — we use the emoji string passed via react_with_emoji()
    Use react_with_emoji() for full control.
    """
    return await react_with_emoji(chat_id, message_id, "❤️")


async def react_with_emoji(chat_id: int, message_id: int, emoji: str,
                            custom_emoji_id: Optional[str] = None,
                            big: bool = True) -> bool:
    """
    Send an animated custom emoji reaction via the premium USERBOT (human) account.
    NOTE: This reacts AS THE USER ACCOUNT, not as the bot.
    For bot reactions, use bot.set_message_reaction() with ReactionTypeCustomEmoji instead.
    This function is kept for edge cases where userbot-side reactions are explicitly needed.
    """
    if not is_connected():
        return False
    try:
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionEmoji, ReactionCustomEmoji, PeerChannel, PeerChat, PeerUser

        cid = int(chat_id)
        try:
            peer = await _userbot_client.get_input_entity(cid)
        except Exception:
            try:
                peer = await _userbot_client.get_entity(cid)
            except Exception:
                cid_str = str(cid)
                if cid_str.startswith("-100"):
                    peer = await _userbot_client.get_input_entity(int(cid_str.replace("-100", "")))
                else:
                    peer = cid

        if custom_emoji_id and str(custom_emoji_id).isdigit():
            reaction_item = ReactionCustomEmoji(document_id=int(custom_emoji_id))
        else:
            custom_id = CUSTOM_EMOJI_MAP.get(emoji)
            if custom_id and str(custom_id).isdigit():
                reaction_item = ReactionCustomEmoji(document_id=int(custom_id))
            else:
                std_em = emoji.replace('\ufe0f', '')
                reaction_item = ReactionEmoji(emoticon=std_em)

        await _userbot_client(SendReactionRequest(
            peer=peer,
            msg_id=message_id,
            reaction=[reaction_item],
            big=big,
        ))
        logger.info(f"[Premium Reaction ✅] Userbot reacted on chat={chat_id} msg={message_id} with emoji={emoji}")
        return True

    except Exception as e:
        logger.warning(f"[Premium Reaction Warning] Userbot custom reaction failed | chat_id={chat_id} | msg_id={message_id} | emoji={emoji} | custom_emoji_id={custom_emoji_id} | error={e}")
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            std_em = emoji.replace('\ufe0f', '')
            await _userbot_client(SendReactionRequest(
                peer=peer,
                msg_id=message_id,
                reaction=[ReactionEmoji(emoticon=std_em)],
                big=big,
            ))
            logger.info(f"[Premium Reaction ✅] Userbot fallback standard reaction sent on chat={chat_id} msg={message_id}")
            return True
        except Exception as e2:
            logger.warning(f"[Premium Reaction Error] Userbot fallback reaction failed | chat_id={chat_id} | msg_id={message_id} | error={e2}")
            return False




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
