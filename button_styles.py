"""
button_styles.py — Telegram Native Inline Button Styles (Bot API 9.4+)

Provides ButtonStyle enum and enables `style` parameter on telegram.InlineKeyboardButton.
Telegram Bot API 9.4 natively supports colored button backgrounds:
  - ButtonStyle.PRIMARY -> "primary" (🔵 Blue button background)
  - ButtonStyle.SUCCESS -> "success" (🟢 Green button background)
  - ButtonStyle.DANGER  -> "danger"  (🔴 Red button background)

Usage:
    from button_styles import ButtonStyle
    from telegram import InlineKeyboardButton

    btn = InlineKeyboardButton(
        "Open Story",
        url="https://t.me/...",
        style=ButtonStyle.SUCCESS,
        api_kwargs={"icon_custom_emoji_id": "5429633836684157942"}
    )
"""

from enum import Enum
import telegram
from telegram import InlineKeyboardButton


class ButtonStyle(str, Enum):
    PRIMARY = "primary"  # 🔵 Dark Blue Button
    SUCCESS = "success"  # 🟢 Green Button
    DANGER  = "danger"   # 🔴 Red Button


PRIMARY = ButtonStyle.PRIMARY
SUCCESS = ButtonStyle.SUCCESS
DANGER  = ButtonStyle.DANGER


# Automatically patch telegram.InlineKeyboardButton so any call with style= works directly
_original_init = telegram.InlineKeyboardButton.__init__


def _patched_button_init(self, text: str, *args, style=None, **kwargs):
    api_kwargs = kwargs.pop("api_kwargs", None)
    if api_kwargs is None:
        api_kwargs = {}
    else:
        api_kwargs = dict(api_kwargs)

    if style is not None:
        style_str = style.value if hasattr(style, "value") else str(style)
        api_kwargs["style"] = style_str

    _original_init(self, text, *args, api_kwargs=api_kwargs, **kwargs)


if not getattr(telegram.InlineKeyboardButton, "_style_patched", False):
    telegram.InlineKeyboardButton.__init__ = _patched_button_init
    telegram.InlineKeyboardButton._style_patched = True
