# -*- coding: utf-8 -*-
"""
Telegram Courses Library Bot (PTB v21.x)
- Render-friendly: aiohttp health server on $PORT (main thread)
- Subscription gate before use
- Admin contact button

Important:
- أضف البوت كـ Admin في قنوات الاشتراك ليقدر يتحقق من العضوية.
- ضع ملفات PDF في assets/ بأسماء مطابقة لـ ASSET_MAP.
"""

import os
import threading
import logging
from pathlib import Path
from typing import List, Tuple

import asyncio
from aiohttp import web
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden

# ========= الإعدادات =========
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

REQUIRED_CHANNELS = [c.strip() for c in os.getenv("REQUIRED_CHANNELS", "@ferpokss,@Ferp0ks").split(",") if c.strip()]
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Ferp0ks").lstrip("@")

ASSETS_DIR = Path("assets")
PORT = int(os.getenv("PORT", "10000"))

ASSET_MAP = {
    "ai":       ("courses_ai.pdf",       "الذكاء الاصطناعي"),
    "python":   ("courses_python.pdf",   "برمجة وبايثون"),
    "cyber":    ("courses_cyber.pdf",    "الأمن السيبراني والاختراق"),
    "business": ("courses_business.pdf", "التجارة والتسويق"),
    "english":  ("courses_english.pdf",  "اللغة الإنجليزية"),
}

# ========= لوجينغ =========
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("courses-bot")

# ========= واجهة المستخدم =========
def build_main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🤖 ذكاء اصطناعي", callback_data="sec:ai")],
        [InlineKeyboardButton("🐍 برمجة وبايثون", callback_data="sec:python")],
        [InlineKeyboardButton("🛡️ أمن سيبراني واختراق", callback_data="sec:cyber")],
        [InlineKeyboardButton("💼 تجارة وتسويق", callback_data="sec:business")],
        [InlineKeyboardButton("🇬🇧 اللغة الإنجليزية", callback_data="sec:english")],
        [
            InlineKeyboardButton("🛠 تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}"),
            InlineKeyboardButton("🔄 تحديث القائمة", callback_data="menu"),
        ],
    ]
    return InlineKeyboardMarkup(rows)

def build_gate_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for ch in REQUIRED_CHANNELS:
        uname = ch.lstrip("@")
        url = f"https://t.me/{uname}"
        buttons.append([InlineKeyboardButton(f"📢 اشترك في {uname}", url=url)])
    buttons.append([
        InlineKeyboardButton("✅ تحقّق الاشتراك", callback_data="verify"),
        InlineKeyboardButton("🛠 تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}")
    ])
    return InlineKeyboardMarkup(buttons)

# ========= التحقق من الاشتراك =========
async def is_member_of(chat_id: str, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
    except (BadRequest, Forbidden) as e:
        log.warning(f"[membership] chat={chat_id} user={user_id} error={e}")
        return False
    except Exception as e:
        log.error(f"[membership] unexpected chat={chat_id} user={user_id}: {e}")
        return False

async def passes_gate(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> Tuple[bool, List[str]]:
    missing = []
    for ch in REQUIRED_CHANNELS:
        if not await is_member_of(ch, user_id, context):
            missing.append(ch)
    return (len(missing) == 0), missing

# ========= الهاندلرز =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message or update.callback_query.message

    ok, missing = await passes_gate(user.id, context)
    if not ok:
        text = (
            "🔒 للوصول إلى المكتبة، يلزم الاشتراك في القنوات التالية أولاً:\n"
            + "\n".join([f"• {m}" for m in missing]) +
            "\n\nبعد الاشتراك اضغط «✅ تحقّق الاشتراك»."
        )
        await msg.reply_text(text, reply_markup=build_gate_keyboard())
        return

    await msg.reply_text(
        "📚 أهلاً بك في مكتبة الكورسات.\nاختر قسمًا لإرسال ملف PDF:",
        reply_markup=build_main_menu()
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "verify":
        ok, missing = await passes_gate(q.from_user.id, context)
        if not ok:
            text = (
                "❗️ما زال هناك قنوات غير مشترَك بها:\n"
                + "\n".join([f"• {m}" for m in missing]) +
                "\n\nاشترك ثم اضغط «✅ تحقّق الاشتراك» مرة أخرى."
            )
            await q.message.edit_text(text, reply_markup=build_gate_keyboard())
            return
        await q.message.edit_text("✅ تم التحقق. اختر قسمًا:", reply_markup=build_main_menu())
        return

    if data == "menu":
        await q.message.edit_text("📚 القائمة الرئيسية:", reply_markup=build_main_menu())
        return

    if data.startswith("sec:"):
        key = data.split(":", 1)[1]
        filename, nice_name = ASSET_MAP.get(key, ("", ""))
        if not filename:
            await q.message.reply_text("⚠️ قسم غير معروف.")
            return

        file_path = ASSETS_DIR / filename
        if file_path.exists():
            caption = f"📘 {nice_name} — ملف PDF يضم دورات وكتب وروابط موثوقة.\n\n🛠 تواصل مع الإدارة: @{OWNER_USERNAME}"
            await q.message.reply_document(InputFile(file_path), caption=caption)
        else:
            await q.message.reply_text(f"🚫 الملف غير متوفر حالياً ({filename}).\nراسل الإدارة: @{OWNER_USERNAME}")
        return

    await q.message.reply_text("🤖 أمر غير معروف.")

# ========= تشغيل بوت تيليجرام في خيط جانبي مع event loop =========
def run_telegram_bot():
    try:
        # إنشاء لوب مخصص لهذا الثريد وتعيينه كـ current
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(on_button))

        log.info("🤖 Telegram bot starting (background thread)…")
        # run_polling يعتمد على get_event_loop؛ الآن موجود
        application.run_polling(stop_signals=None, close_loop=False)
    except Exception as e:
        log.exception("❌ Telegram thread crashed: %s", e)

# ========= Health/Web على $PORT في الخيط الرئيسي =========
async def health(_request):
    return web.Response(text="OK")

async def root(_request):
    return web.Response(text="Courses Bot is alive")

def main():
    # شغّل بوت تيليجرام في خيط جانبي
    threading.Thread(target=run_telegram_bot, daemon=True).start()

    # aiohttp في الخيط الرئيسي (حتى Render يلتقط المنفذ)
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)

    log.info("🌐 Health server on 0.0.0.0:%s", PORT)
    # handle_signals=False لتفادي set_wakeup_fd
    web.run_app(app, host="0.0.0.0", port=PORT, handle_signals=False)

if __name__ == "__main__":
    main()
