"""
plugins/group/welcome.py
────────────────────────
Welcome system: supports text messages (with {name} placeholder),
up to 5 messages per group, optional random selection.
"""
import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .utils import get_welcome, save_welcome, is_enabled, is_welcome_enabled, set_welcome_enabled


logger = logging.getLogger(__name__)
_DIV = "━━━━━━━━━━━━━━━━━━"


def welcome_panel(chat_id: int) -> tuple:
    """Build the welcome sub-panel text + markup."""
    w = get_welcome(chat_id)
    count   = len(w.get("messages", []))
    rand    = w.get("random", False)
    enabled = w.get("welcome_enabled", False)
    text = (
        f"<b>👋 Welcome System</b>\n{_DIV}\n\n"
        f"Status:          <b>{'✅ Enabled' if enabled else '❌ Disabled'}</b>\n"
        f"Messages set: <b>{count}/5</b>\n"
        f"Random mode:  <b>{'🟢 ON' if rand else '🔴 OFF'}</b>\n\n"
        "<i>Commands:\n"
        "/setwelcome &lt;text&gt; — add a welcome message (use {name} for user)\n"
        "/clearwelcome — remove all messages\n"
        "/previewwelcome — preview current message</i>\n\n"
        "<i>⚠️ Welcome only sends in groups where it is Enabled.</i>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{'🔴 Disable Welcome' if enabled else '🟢 Enable Welcome'}",
            callback_data="grp_welcome_enable"
        )],
        [InlineKeyboardButton(
            f"{'🟢' if rand else '🔴'} Toggle Random",
            callback_data="grp_welcome_toggle_random"
        )],
        [InlineKeyboardButton("« Back", callback_data="grp_back")],
    ])
    return text, markup


DEFAULT_WELCOME_TEMPLATE = """♡ <b>We're glad to have you here</b>, {mention}.

Welcome to <b>{group_name}</b>, a community created for story lovers, readers, and listeners.

<blockquote>Discover a growing collection of Free and Premium stories from leading storytelling platforms. Explore content across multiple genres and languages, with fresh stories added regularly for everyone to enjoy.</blockquote>

<blockquote expandable>Before getting started, please take a moment to review the community rules.

➠ Respect all members. Harassment, hate speech, discrimination, threats, bullying, personal attacks, and abusive behavior are strictly prohibited.

➠ Spam, flooding, repetitive messages, excessive mentions, unsolicited advertisements, and other disruptive activities are not allowed.

➠ Do not share NSFW, explicit, graphic, violent, or otherwise inappropriate content.

➠ Respect everyone's privacy. Never share personal information, private conversations, screenshots, or confidential content without permission.

➠ Do not invite members to private messages for stories already available publicly or through Arya Premium. Stories may only be shared privately if they are unavailable on any public channel and not available on Arya Premium.

➠ Buying, selling, trading, or advertising Story Links (SL) is strictly prohibited. Copying, leaking, reposting, forwarding, redistributing, or publicly sharing any content available through Arya Premium is also prohibited.

➠ Promotion of external groups, channels, communities, websites, invite links, referral programs, or third-party services is not allowed.

➠ Attempting to abuse, exploit, spam, or manipulate the bot may result in automatic restrictions or a permanent ban.

➠ Violations may result in warnings, message removal, temporary restrictions, or permanent bans. By remaining in this community, you agree to comply with all of the above rules.</blockquote>

<u>L̲o̲o̲k̲i̲n̲g̲ ̲f̲o̲r̲ ̲t̲h̲e̲ ̲u̲l̲t̲i̲m̲a̲t̲e̲ ̲s̲t̲o̲r̲y̲ ̲m̲a̲r̲k̲e̲t̲p̲l̲a̲c̲e̲?̲</u>

<b>Arya Premium</b> gives you instant access to 250+ premium stories from popular platforms—all in one place at affordable prices.

➠ <b>Lifetime Access</b> (T&C Apply)

➠ <b>Forwarding & Download Allowed</b> • <b>High-Quality Audio</b>

➠ <b>Smooth & Secure Payments</b> via UPI, Cards & International Payment Methods

➠ <b>250+ Stories</b> • <b>Multiple Genres</b> • <b>Multiple Languages</b>

Tap the Open AP button below to browse the marketplace, explore the complete collection, and purchase your favorite stories in just a few taps.

♡ <b>Enjoy your stay and happy listening!</b>"""

DEFAULT_WELCOME_MARKUP = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("Open AP", url="https://t.me/UseAryaBot/apminibyarya"),
        InlineKeyboardButton("Tutorial", url="https://t.me/StoriesLinkopningguide/9?single")
    ]
])


async def _send_welcome_msg(context, chat_id, msg_data, member=None, reply_to=None, group_title=""):
    import html
    name = html.escape(member.first_name) if member else "Friend"
    mention = f"<a href='tg://user?id={member.id}'>{name}</a>" if member else "Friend"
    username = f"@{html.escape(member.username)}" if member and member.username else mention
    full_name = html.escape(member.full_name) if member else name
    uid = member.id if member else 123456789
    gname = html.escape(group_title) if group_title else "our Community"

    if not msg_data or not msg_data.get("content"):
        content = DEFAULT_WELCOME_TEMPLATE
    else:
        content = msg_data.get("content", DEFAULT_WELCOME_TEMPLATE)

    # Replace placeholders
    content = content.replace("[User Name]", mention)
    content = content.replace("{mention}", mention)
    content = content.replace("{name}", f"<b>{name}</b>")
    content = content.replace("{first_name}", f"<b>{name}</b>")
    content = content.replace("{username}", username)
    content = content.replace("{user_name}", username)
    content = content.replace("{user_fullname}", f"<b>{full_name}</b>")
    content = content.replace("{fullname}", f"<b>{full_name}</b>")
    content = content.replace("{user_id}", str(uid))
    content = content.replace("{id}", str(uid))
    content = content.replace("[Group Name]", gname)
    content = content.replace("{group_name}", gname)

    file_type = msg_data.get("type", "text") if msg_data else "text"
    file_id = msg_data.get("file_id") if msg_data else None

    kwargs = {
        "chat_id": chat_id,
        "parse_mode": "HTML",
        "reply_markup": DEFAULT_WELCOME_MARKUP,
        "disable_web_page_preview": True
    }
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to

    try:
        if file_type == "text" or not file_id:
            await context.bot.send_message(text=content, **kwargs)
        elif file_type == "photo":
            await context.bot.send_photo(photo=file_id, caption=content, **kwargs)
        elif file_type == "video":
            await context.bot.send_video(video=file_id, caption=content, **kwargs)
        elif file_type == "animation":
            await context.bot.send_animation(animation=file_id, caption=content, **kwargs)
        elif file_type == "document":
            await context.bot.send_document(document=file_id, caption=content, **kwargs)
        elif file_type == "sticker":
            await context.bot.send_sticker(sticker=file_id, **{"chat_id": chat_id, "reply_to_message_id": reply_to} if reply_to else {"chat_id": chat_id})
    except Exception as e:
        logger.warning(f"[GCP-Welcome] Send with reply_to failed: {e}, retrying without reply_to...")
        kwargs.pop("reply_to_message_id", None)
        try:
            if file_type == "text" or not file_id:
                await context.bot.send_message(text=content, **kwargs)
            elif file_type == "photo":
                await context.bot.send_photo(photo=file_id, caption=content, **kwargs)
            elif file_type == "video":
                await context.bot.send_video(video=file_id, caption=content, **kwargs)
            elif file_type == "animation":
                await context.bot.send_animation(animation=file_id, caption=content, **kwargs)
            elif file_type == "document":
                await context.bot.send_document(document=file_id, caption=content, **kwargs)
        except Exception as e2:
            logger.error(f"[GCP-Welcome] Failed to send: {e2}")


async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered when new members join the group."""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        chat_id = update.effective_chat.id

        # Welcome only fires when EXPLICITLY enabled for this group
        if not is_welcome_enabled(chat_id):
            return

        new_members = (update.message.new_chat_members or []) if update.message else []
        if not new_members:
            return

        w = get_welcome(chat_id)
        msgs = w.get("messages", [])

        for member in new_members:
            if member.is_bot:
                continue

            # ── PROFILE / USERNAME GUARD ──────────────────────────────────────
            # Check Display Name / Username for links, ads, and explicit words
            name_text = " ".join(filter(None, [member.first_name, member.last_name, member.username]))
            
            # Common spam patterns and adult keywords
            spam_indicators = [
                r"t\.me/\+", r"t\.me/joinchat", r"bit\.ly", r"sliceurl", r"tinyurl", 
                r"earn", r"money", r"crypto", r"free", r"porn", r"sex", r"xxx", r"nude",
                r"\bchudai\b", r"\brandi\b", r"\+18", r"🔞"
            ]
            import re
            is_spam_name = False
            for pat in spam_indicators:
                if re.search(pat, name_text.lower()):
                    is_spam_name = True
                    break
            
            if is_spam_name:
                try:
                    # Ban instantly
                    await context.bot.ban_chat_member(chat_id, member.id)
                    # Delete the join service message
                    try:
                        await update.message.delete()
                    except Exception:
                        pass
                    # Log / Send alert
                    await context.bot.send_message(
                        chat_id,
                        f"🛡️ <b>Profile Guard Alert</b>\n"
                        f"<code>──────────────────</code>\n"
                        f"<b>Banned User  »</b> {member.full_name} (<code>{member.id}</code>)\n"
                        f"<b>Reason        »</b> Spam/NSFW pattern in profile name.\n"
                        f"<code>──────────────────</code>",
                        parse_mode="HTML"
                    )
                    continue
                except Exception as ex:
                    logger.warning(f"[ProfileGuard] Failed to ban {member.id}: {ex}")
            # ── END PROFILE GUARD ─────────────────────────────────────────────

            msg = (random.choice(msgs) if w.get("random", False) else msgs[0]) if msgs else None
            await _send_welcome_msg(
                context, chat_id, msg, member,
                reply_to=update.message.message_id,
                group_title=update.effective_chat.title
            )
    except Exception as e:
        logger.warning(f"[GCP-Welcome] new_member_handler: {e}")


async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setwelcome <text> or reply to a message — add a welcome message"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if m.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Admin only.")
            return

        chat_id = update.effective_chat.id
        w = get_welcome(chat_id)
        msgs = w.get("messages", [])

        if len(msgs) >= 5:
            await update.message.reply_text("⚠️ Max 5 welcome messages. Use /clearwelcome first.")
            return

        file_id = None
        file_type = "text"
        text = ""
        
        if update.message.reply_to_message:
            rep = update.message.reply_to_message
            if rep.photo:
                file_id = rep.photo[-1].file_id
                file_type = "photo"
            elif rep.video:
                file_id = rep.video.file_id
                file_type = "video"
            elif rep.animation:
                file_id = rep.animation.file_id
                file_type = "animation"
            elif rep.document:
                file_id = rep.document.file_id
                file_type = "document"
            elif rep.sticker:
                file_id = rep.sticker.file_id
                file_type = "sticker"
            
            text = rep.caption or rep.text or ""
            if context.args:
                text = " ".join(context.args)
        else:
            text = " ".join(context.args) if context.args else ""
            
        if not text and not file_id:
            await update.message.reply_text(
                "Usage: /setwelcome &lt;text&gt; or reply to a media/text message.\n"
                "<i>Placeholders: {first_name}, {user_fullname}, {username}, {user_id}</i>",
                parse_mode="HTML",
            )
            return

        msgs.append({"type": file_type, "file_id": file_id, "content": text})
        w["messages"] = msgs
        w["welcome_enabled"] = True
        save_welcome(chat_id, w)
        await update.message.reply_text(f"✅ Welcome message added & enabled! ({len(msgs)}/5)")
    except Exception as e:
        logger.error(f"[GCP-Welcome] setwelcome_cmd: {e}")


async def clearwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clearwelcome — remove all welcome messages & disable welcome"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if m.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Admin only.")
            return
        save_welcome(update.effective_chat.id, {"messages": [], "random": False, "welcome_enabled": False})
        await update.message.reply_text("✅ All welcome messages cleared and welcome disabled.")
    except Exception as e:
        logger.error(f"[GCP-Welcome] clearwelcome_cmd: {e}")


async def previewwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/previewwelcome — show the current welcome message as if a new user joined"""
    try:
        if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
            return
        chat_id = update.effective_chat.id
        w = get_welcome(chat_id)
        msgs = w.get("messages", [])
        if not msgs:
            await update.message.reply_text("❌ No welcome messages set. Use /setwelcome.")
            return
        msg = random.choice(msgs) if w.get("random") else msgs[0]
        
        await update.message.reply_text("<b>👀 Welcome Preview:</b>", parse_mode="HTML")
        await _send_welcome_msg(context, chat_id, msg, update.effective_user)
    except Exception as e:
        logger.error(f"[GCP-Welcome] previewwelcome_cmd: {e}")
