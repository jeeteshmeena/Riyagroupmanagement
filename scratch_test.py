import os
import asyncio
from telegram import Bot

async def test():
    bot = Bot(token=os.getenv("BOT_TOKEN", "7052952402:AAG8zE4c0N2C7m05uV8t3A3WqX9n6hRIfMw")) 
    msg = await bot.send_message(
        chat_id=1418514937, # fallback, maybe use a known valid user id from bot logs
        text='Here is a custom emoji: <tg-emoji emoji-id="5471952986970267163">✨</tg-emoji>',
        parse_mode="HTML"
    )
    print("Success! Message ID:", msg.message_id)

asyncio.run(test())
