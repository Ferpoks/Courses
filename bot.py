# -*- coding: utf-8 -*-
import os
import threading
from pathlib import Path

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web

# ========= الإعدادات =========
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

ASSETS_DIR = Path("assets")
PORT = int(os.getenv("PORT", "10000"))  # Render يمرّر PORT تلقائياً

# ========= واجهة المستخدم =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 ذكاء اصطناعي", callback_data="ai")],
        [InlineKeyboardButton("🐍 برمجة وبايثون", callback_data="python")],
        [InlineKeyboardButton("🛡️ أمن سيبراني واختراق", callback_data="cyber")],
        [InlineKeyboardButton("💼 تجارة وتسويق", callback_data="business")],
        [InlineKeyboardButton("🇬🇧 اللغة الإنجليزية", callback_data="english")],
    ]
    text = (
        "📚 مرحباً بك في مكتبة الكورسات\n\n"
        "اختر القسم المطلوب لإرسال ملف PDF الموثوق:"
    )
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def _send_pdf(msg, filename: str, nice_name: str):
    file_path = ASSETS_DIR / filename
    if file_path.exists():
        await msg.reply_document(
            InputFile(file_path),
            caption=f"📘 {nice_name} — ملف PDF يضم دورات وكتب وروابط موثوقة"
        )
    else:
        await msg.reply_text("🚫 الملف غير متوفر حالياً")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    file_map = {
        "ai":       ("courses_ai.pdf",       "الذكاء الاصطناعي"),
        "python":   ("courses_python.pdf",   "برمجة وبايثون"),
        "cyber":    ("courses_cyber.pdf",    "الأمن السيبراني والاختراق"),
        "business": ("courses_business.pdf", "التجارة والتسويق"),
        "english":  ("courses_english.pdf",  "اللغة الإنجليزية"),
    }

    key = q.data
    filename, nice = file_map.get(key, ("", ""))
    await _send_pdf(q.message, filename, nice)

# ========= تشغيل بوت تيليجرام في خيط جانبي =========
def run_telegram_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))

    print("🤖 Telegram bot starting (background thread)…")
    # تعطيل signals لأننا لسنا في الخيط الرئيسي
    application.run_polling(stop_signals=None, close_loop=False)

# ========= Health/Web على المنفذ الذي يطلبه Render =========
async def health(_request):
    return web.Response(text="OK")

async def root(_request):
    return web.Response(text="Courses Bot is alive")

def main():
    # شغّل بوت تيليجرام في خيط جانبي
    threading.Thread(target=run_telegram_bot, daemon=True).start()

    # شغّل aiohttp في الخيط الرئيسي على $PORT حتى Render يلتقط المنفذ
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)

    print(f"🌐 Health server on 0.0.0.0:{PORT}")
    # مهم: handle_signals=False لتفادي set_wakeup_fd
    web.run_app(app, host="0.0.0.0", port=PORT, handle_signals=False)

if __name__ == "__main__":
    main()
