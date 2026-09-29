"""
colored_buttons.py — Colored Inline Keyboard Buttons for Telegram Bot

Telegram supports colored inline buttons via the `color` field in the
InlineKeyboardButton JSON payload. This module provides a drop-in
replacement that adds the color parameter.

Color values:
  GREEN (Success) = 2
  RED   (Danger)  = 3
  BLUE  (Primary) = 1

Usage:
    from colored_buttons import CBtn, GREEN, RED, BLUE
    btn = CBtn("Open Story", url="https://...", color=GREEN)
"""

from telegram import InlineKeyboardButton


# Color constants
GREEN = 2   # Success — green button
RED   = 3   # Danger  — red button
BLUE  = 1   # Primary — blue button


class CBtn(InlineKeyboardButton):
    """
    Colored InlineKeyboardButton.
    Adds an optional `color` field to the button JSON so Telegram
    renders it in the specified color on supporting clients.

    Falls back to a normal button on clients that don't support colors.
    """

    def __init__(self, *args, color: int = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._btn_color = color

    def to_dict(self) -> dict:
        data = super().to_dict()
        if self._btn_color is not None:
            data["color"] = self._btn_color
        return data
