# bot.py
import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ContextTypes
)

# —————————————————— ضبط السجلات ——————————————————
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("courses-bot")

# —————————————————— المسارات والبيئة ——————————————————
BASE_DIR = Path(__file__).parent.resolve()
CATALOG_PATH = BASE_DIR / "assets" / "catalog.json"

TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "").lstrip("@")
REQUIRED_CHANNELS = [
    c.strip() for c in os.environ.get("REQUIRED_CHANNELS", "").split(",") if c.strip()
]

# —————————————————— تحميل الكتالوج ——————————————————
def load_catalog() -> Dict[str, Any]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"catalog.json غير موجود: {CATALOG_PATH}")
    with CATALOG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data

CATALOG = load_catalog()
log.info("📦 Catalog on start: %s", {k: (len(v) if isinstance(v, list) else "obj") for k, v in CATALOG.items()})

# —————————————————— أسماء الأقسام + أيقونات ——————————————————
# المفاتيح هي مفاتيح catalog.json
CATEGORY_META: Dict[str, Tuple[str, str]] = {
    "prog":        ("📚 كتب البرمجة", "💻"),
    "design":      ("📚 كتب التصميم", "🎨"),
    "security":    ("📚 كتب الأمن", "🛡️"),
    "languages":   ("📚 كتب اللغات", "🗣️"),
    "marketing":   ("📚 كتب التسويق", "📈"),
    "maintenance": ("📚 كتب الصيانة", "🛠️"),
    "office":      ("📚 كتب البرامج المكتبية", "🗃️"),
}

# —————————————————— أدوات مساعدة ——————————————————
def chunk_buttons(buttons: List[InlineKeyboardButton], n: int = 2):
    return [buttons[i:i+n] for i in range(0, len(buttons), n)]

async def ensure_member(ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if not REQUIRED_CHANNELS:
        return True
    try:
        for ch in REQUIRED_CHANNELS:
            chat = await ctx.bot.get_chat(ch)
            member = await ctx.bot.get_chat_member(chat.id, user_id)
            if member.status in ("left", "kicked"):
                return False
        return True
    except Exception as e:
        log.warning("membership check failed: %s", e)
        # لو صار خطأ نسمح بالدخول بدل ما نوقف الناس
        return True

def human_counts(catalog: Dict[str, Any]) -> str:
    parts = []
    for key, (label, icon) in CATEGORY_META.items():
        block = catalog.get(key, [])
        # لو القسم فيه dict خاص بالأجزاء (مثل ethical_hacking_parts) نحسب الأطفال
        count = 0
        if isinstance(block, list):
            for item in block:
                if isinstance(item, dict) and "children" in item:
                    count += len(item["children"])
                else:
                    count += 1
        parts.append(f"{icon} {label.split(' ',1)[1]}: {count}")
    return "\n".join(parts)

def office_buttons() -> List[InlineKeyboardButton]:
    # ترتيب جذاب داخل البرامج المكتبية
    nice_order = [
        ("📊 Excel",             "assets/office/excel.pdf"),
        ("📘 شرح الإكسل خطوة بخطوة", "assets/office/excel_step_by_step.pdf"),
        ("📝 Microsoft Word",    "assets/office/word.pdf"),
    ]
    btns = []
    for title, path in nice_order:
        btns.append(InlineKeyboardButton(title, callback_data=f"file:{path}"))
    return btns

# —————————————————— بناء القوائم ——————————————————
def build_main_menu() -> InlineKeyboardMarkup:
    buttons: List[InlineKeyboardButton] = []
    for key, (label, icon) in CATEGORY_META.items():
        buttons.append(InlineKeyboardButton(f"{icon} {label}", callback_data=f"cat:{key}"))
    kb = chunk_buttons(buttons, 2)

    # صف الأدوات
    tools_row = []
    if OWNER_USERNAME:
        tools_row.append(InlineKeyboardButton("🛠️ تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}"))
    tools_row.append(InlineKeyboardButton("🔄 تحديث الكتالوج", callback_data="reload"))
    kb.append(tools_row)
    return InlineKeyboardMarkup(kb)

def build_category_menu(cat_key: str) -> InlineKeyboardMarkup:
    items = CATALOG.get(cat_key, [])
    buttons: List[InlineKeyboardButton] = []

    # تخصيص عرض البرامج المكتبية
    if cat_key == "office":
        buttons = office_buttons()
    else:
        # أعرض العناصر (وأيضًا الأطفال إن وُجدوا)
        for it in items:
            if isinstance(it, dict) and "children" in it:
                for child in it["children"]:
                    title = child.get("title", "بدون عنوان")
                    path = child.get("path")
                    if path:
                        buttons.append(InlineKeyboardButton(f"📘 {title}", callback_data=f"file:{path}"))
            else:
                title = it.get("title", "بدون عنوان")
                path = it.get("path")
                if path:
                    buttons.append(InlineKeyboardButton(f"📘 {title}", callback_data=f"file:{path}"))

    kb = chunk_buttons(buttons, 1)  # صف واحد لكل كتاب لقراءة أوضح
    kb.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="back:main")])
    return InlineKeyboardMarkup(kb)

# —————————————————— Handlers ——————————————————
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        allowed = await ensure_member(context, update.message.from_user.id)
        if not allowed:
            await update.message.reply_text("انضم للقنوات المطلوبة أولًا ثم أرسل /start من جديد.")
            return

    greeting = (
        "مرحبًا بك في مكتبة الدورات 📚\n"
        "اختر القسم الذي تريده من الأزرار بالأسفل.\n\n"
        f"*حالة المحتوى:*\n{human_counts(CATALOG)}"
    )
    if update.message:
        await update.message.reply_text(
            greeting, reply_markup=build_main_menu(), parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.callback_query.edit_message_text(
            greeting, reply_markup=build_main_menu(), parse_mode=ParseMode.MARKDOWN
        )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await q.answer()

    if data.startswith("cat:"):
        cat_key = data.split(":", 1)[1]
        title = CATEGORY_META.get(cat_key, ("القسم", "📚"))[0]
        await q.edit_message_text(
            f"{title} – اختر:",
            reply_markup=build_category_menu(cat_key)
        )
        return

    if data == "back:main":
        await start(update, context)
        return

    if data == "reload":
        try:
            global CATALOG
            CATALOG = load_catalog()
            await q.edit_message_text(
                f"تم تحديث الكتالوج ✅\n\n*حالة المحتوى:*\n{human_counts(CATALOG)}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=build_main_menu()
            )
        except Exception as e:
            await q.edit_message_text(f"حدث خطأ أثناء التحديث: {e}", reply_markup=build_main_menu())
        return

    if data.startswith("file:"):
        path = data.split(":", 1)[1]
        file_path = BASE_DIR / path
        if not file_path.exists():
            await q.message.reply_text(f"لم أجد الملف في السيرفر: \n`{path}`", parse_mode=ParseMode.MARKDOWN)
            return
        try:
            await q.message.reply_document(InputFile(str(file_path)))
        except Exception as e:
            await q.message.reply_text(f"تعذر إرسال الملف: {e}")
        return

async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        global CATALOG
        CATALOG = load_catalog()
        await update.message.reply_text(
            f"تم تحديث الكتالوج ✅\n\n*حالة المحتوى:*\n{human_counts(CATALOG)}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_main_menu()
        )
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ أثناء التحديث: {e}")

# —————————————————— Health server لِـ Render ——————————————————
async def start_health_server():
    # خادم بسيط جدًا باستخدام aiohttp
    try:
        from aiohttp import web
    except Exception:
        log.info("aiohttp غير مثبت، تخطي خادم الصحة")
        return

    async def ok(_):
        return web.Response(text="ok")

    app = web.Application()
    app.add_routes([web.get("/", ok), web.get("/health"), web.get("/healthz")])
    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("🌐 Health server on 0.0.0.0:%s (paths: /healthz,/health,/)", port)

# —————————————————— Main ——————————————————
def main():
    if not TOKEN:
        raise SystemExit("❌ ضع BOT_TOKEN أو TELEGRAM_TOKEN في متغيرات البيئة على Render")

    app: Application = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CallbackQueryHandler(handle_buttons))

    async def run():
        await start_health_server()
        log.info("🤖 Telegram bot starting…")
        # حذف أي Webhook قديم واستخدم polling
        await app.bot.delete_webhook()
        await app.start()
        await app.updater.start_polling()
        await app.updater.wait()
        await app.stop()

    asyncio.run(run())

if __name__ == "__main__":
    main()

