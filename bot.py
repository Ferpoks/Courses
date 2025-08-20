# -*- coding: utf-8 -*-
import os
from pathlib import Path
import threading

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from aiohttp import web

# ========= الإعدادات =========
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

ASSETS_DIR = Path("assets")
PORT = int(os.getenv("PORT", "10000"))  # Render يوفر PORT تلقائياً

# ========= واجهة المستخدم =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 ذكاء اصطناعي", callback_data="ai")],
        [InlineKeyboardButton("🐍 برمجة وبايثون", callback_data="python")],
        [InlineKeyboardButton("🛡️ أمن سيبراني واختراق", callback_data="cyber")],
        [InlineKeyboardButton("💼 تجارة وتسويق", callback_data="business")],
    ]
    text = (
        "📚 مرحباً بك في مكتبة الكورسات\n\n"
        "اختر القسم المطلوب لإرسال ملف PDF الموثوق:"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def send_section_pdf(query_message, section: str):
    file_map = {
        "ai": "courses_ai.pdf",
        "python": "courses_python.pdf",
        "cyber": "courses_cyber.pdf",
        "business": "courses_business.pdf",
    }
    filename = file_map.get(section, "")
    file_path = ASSETS_DIR / filename
    if file_path.exists():
        await query_message.reply_document(
            InputFile(file_path),
            caption=f"📘 ملف {filename} — يحتوي دورات وكتب وروابط موثوقة"
        )
    else:
        await query_message.reply_text("🚫 الملف غير متوفر حالياً")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    section = query.data
    await send_section_pdf(query.message, section)

# ========= health check =========
async def health(_request):
    return web.Response(text="OK")

def run_health_server():
    app = web.Application()
    app.router.add_get("/health", health)
    # مهم: تعطيل signals لأننا داخل thread جانبي
    web.run_app(app, host="0.0.0.0", port=PORT, handle_signals=False)

def main():
    # شغّل health server في ثريد جانبي بدون signals
    threading.Thread(target=run_health_server, daemon=True).start()

    # تيليجرام بوت
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))

    print(f"✅ Bot is running... Health on 0.0.0.0:{PORT}")
    application.run_polling(close_loop=False)  # إبقِ اللوب شغّال

if __name__ == "__main__":
    main()

