"""
colored_buttons.py — Colored Inline Keyboard Buttons for Telegram Bot

Telegram's official Bot API does not support a `color` field for inline
buttons in standard clients. Instead, we use colored circle emoji prefixed
to the button text to visually indicate button type — this works 100%
on all Telegram clients without any special API support.

Color indicators:
  GREEN  = 🟢 (Success / Open / Confirm)
  RED    = 🔴 (Danger  / Broken / Cancel / Punish)
  BLUE   = 🔵 (Info    / Tutorial / Delete / Guide)

Usage:
    from colored_buttons import CBtn, GREEN, RED, BLUE
    btn = CBtn("Open Story", url="https://...", color=GREEN)
    # Renders as: "🟢 Open Story"

    # Buttons with single space text (icon-only) — no emoji prefix added:
    btn = CBtn(" ", callback_data="delete", color=BLUE)
    # Renders as: " " (unchanged — icon-only buttons keep their look)
"""

from telegram import InlineKeyboardButton

# Color constants — emoji prefix indicators
GREEN = "🟢"
RED   = "🔴"
BLUE  = "🔵"


class CBtn(InlineKeyboardButton):
    """
    Colored InlineKeyboardButton.
    Prepends a colored circle emoji to the button text so users can
    visually distinguish button types (green=action, red=danger, blue=info).

    Buttons with blank/single-space text (icon-only emoji buttons) are
    left unchanged — we don't add prefix to those.
    """

    def __init__(self, text: str, *args, color: str = None, **kwargs):
        # Only add color prefix to buttons that have visible text
        # Skip icon-only buttons (single space or empty)
        if color and text and text.strip():
            colored_text = f"{color} {text}"
        else:
            colored_text = text
        super().__init__(colored_text, *args, **kwargs)

    def to_dict(self, **kwargs) -> dict:
        return super().to_dict(**kwargs)
