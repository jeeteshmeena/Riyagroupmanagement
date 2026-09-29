"""
colored_buttons.py — Bridge for ButtonStyle (PRIMARY, SUCCESS, DANGER)
"""

from button_styles import ButtonStyle, PRIMARY, SUCCESS, DANGER
from telegram import InlineKeyboardButton

# Color constants mapped to actual ButtonStyles
GREEN = ButtonStyle.SUCCESS   # 🟢 Green
RED   = ButtonStyle.DANGER    # 🔴 Red
BLUE  = ButtonStyle.PRIMARY   # 🔵 Blue


class CBtn(InlineKeyboardButton):
    """
    InlineKeyboardButton with style support.
    Accepts style= or color= (both accept ButtonStyle or "primary"/"success"/"danger").
    """

    def __init__(self, text: str, *args, style=None, color=None, **kwargs):
        chosen_style = style or color
        super().__init__(text, *args, style=chosen_style, **kwargs)
