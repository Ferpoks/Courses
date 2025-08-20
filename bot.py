# -*- coding: utf-8 -*-
import os
import threading
from pathlib import Path

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

ASSETS_DIR = Path("assets")
PORT = int(os.getenv("PORT", "10000"))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 ذكاء اصطناعي", callback_data="ai")],
        [InlineKeyboardButton("🐍 برمجة وبايثون", callback_data="python")],
        [InlineKeyboardButton("🛡️ أمن سيبراني واختراق", callback_data="cyber")],
        [InlineKeyboardButton("💼 تجارة وتسويق", callback_data="business")],
    ]
    text = "📚 مرحباً بك في مكتبة الكورسات\n\nاختر القسم المطلوب لإرسال ملف PDF الموثوق:"
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def send_section_pdf(msg, section: str):
    file_map = {
        "ai": "courses_ai.pdf",
        "python": "courses_python.pdf",
        "cyber": "courses_cyber.pdf",
        "business": "courses_business.pdf",
    }
    filename = file_map.get(section, "")
    file_path = ASSETS_DIR / filename
    if file_path.exists():
        await msg.reply_document(InputFile(file_path), caption=f"📘 ملف {filename} — يحتوي دورات وكتب وروابط موثوقة")
    else:
        await msg.reply_text("🚫 الملف غير متوفر حالياً")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await send_section_pdf(q.message, q.data)

def run_telegram_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))
    print("🤖 Telegram bot starting (in background thread)...")
    application.run_polling(stop_signals=None, close_loop=False)

async def health(_request):
    return web.Response(text="OK")

async def root(_request):
    return web.Response(text="Courses Bot is alive")

def main():
    threading.Thread(target=run_telegram_bot, daemon=True).start()

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)

    print(f"🌐 Health server on 0.0.0.0:{PORT}")
    # المهم: عطّل إشارات النظام هنا
    web.run_app(app, host="0.0.0.0", port=PORT, handle_signals=False)

if __name__ == "__main__":
    main()

