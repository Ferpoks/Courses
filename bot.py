# -*- coding: utf-8 -*-
import os
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web

# ========= الإعدادات =========
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

ASSETS_DIR = Path("assets")

# ========= واجهة المستخدم =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 ذكاء اصطناعي", callback_data="ai")],
        [InlineKeyboardButton("🐍 برمجة وبايثون", callback_data="python")],
        [InlineKeyboardButton("🛡️ أمن سيبراني واختراق", callback_data="cyber")],
        [InlineKeyboardButton("💼 تجارة وتسويق", callback_data="business")],
    ]
    await update.message.reply_text(
        "📚 مرحباً بك في مكتبة الكورسات\n\nاختر القسم المطلوب:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    section = query.data
    file_map = {
        "ai": "courses_ai.pdf",
        "python": "courses_python.pdf",
        "cyber": "courses_cyber.pdf",
        "business": "courses_business.pdf",
    }
    file_path = ASSETS_DIR / file_map.get(section, "")
    if file_path.exists():
        await query.message.reply_document(
            InputFile(file_path),
            caption=f"📘 هذا ملف {section} يحتوي على دورات وكتب موثوقة"
        )
    else:
        await query.message.reply_text("🚫 الملف غير متوفر حالياً")

# ========= health check =========
async def health(request):
    return web.Response(text="OK")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    # aiohttp health server
    web_app = web.Application()
    web_app.router.add_get("/health", health)
    import threading
    threading.Thread(target=lambda: web.run_app(web_app, port=10000)).start()

    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
