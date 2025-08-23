# bot.py
import os
import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =================== إعدادات عامة ===================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("courses-bot")

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip()  # مثال: @my_channel أو -1001234
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip()

BASE_DIR = Path(__file__).parent
CANDIDATE_CATALOGS = [BASE_DIR / "assets" / "catalog.json", BASE_DIR / "catalog.json"]

MAX_BOT_UPLOAD = 49 * 1024 * 1024  # ~49MB حد بوت تيليجرام

# الكاتالوج يحمل عند التشغيل ويعاد تحميله عند /reload
CATALOG_PATH: Path
CATALOG: Dict[str, Any] = {}

# ====================================================


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}PB"


def find_catalog_path() -> Path:
    for p in CANDIDATE_CATALOGS:
        if p.exists():
            return p
    # افتراضيًا نستخدم assets/catalog.json
    return CANDIDATE_CATALOGS[0]


def load_catalog() -> Dict[str, Any]:
    global CATALOG_PATH
    CATALOG_PATH = find_catalog_path()
    logger.info(f"📘 Using catalog file: {CATALOG_PATH.as_posix()}")
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # لعرض العدّادات في اللوج
    counts = {}
    for k, v in data.items():
        if isinstance(v, list):
            counts[k] = len(v)
        else:
            counts[k] = 0
    logger.info(f"📦 Catalog on start: {counts}")
    return data


def list_categories() -> List[Tuple[str, str]]:
    """يعيد [(key, nice_title), ...]"""
    titles = {
        "prog": "📚 كتب البرمجة",
        "design": "🎨 كتب التصميم",
        "security": "🛡️ كتب الأمن",
        "languages": "🗣️ كتب اللغات",
        "marketing": "📈 كتب التسويق",
        "maintenance": "🔧 كتب الصيانة",
        "office": "🗂️ كتب البرامج المكتبية",
    }
    items = []
    for key in ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]:
        if key in CATALOG:
            items.append((key, titles.get(key, key)))
    return items


def build_kb(rows: List[List[Tuple[str, str]]]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text=txt, callback_data=data) for (data, txt) in row]
        for row in rows
    ]
    return InlineKeyboardMarkup(keyboard)


def chunk(lst: List[Any], n: int) -> List[List[Any]]:
    return [lst[i : i + n] for i in range(0, len(lst), n)]


def parse_cb(data: str) -> List[str]:
    return data.split("|")


async def ensure_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يتحقق من اشتراك المستخدم بالقناة إذا تم ضبط REQUIRED_CHANNEL."""
    if not REQUIRED_CHANNEL:
        return True

    try:
        user_id = update.effective_user.id
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        status = getattr(member, "status", "left")
        if status in ("left", "kicked"):
            raise Exception("not_member")
        return True
    except Exception as e:
        logger.warning(f"membership check failed: {e}")
        text = (
            "🔒 للوصول إلى المحتوى، يرجى الاشتراك في القناة ثم أرسل /start:\n"
            f"{REQUIRED_CHANNEL}"
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text)
        else:
            await update.effective_chat.send_message(text)
        return False


# =================== Handlers ===================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_member(update, context):
        return

    cats = list_categories()
    rows = chunk([("cat|" + key, title) for key, title in cats], 2)
    kb = build_kb(rows + [[("reload|now", "🔄 إعادة تحميل الكاتالوج")]])
    await update.effective_chat.send_message(
        "مرحبًا بك في مكتبة الدورات 📚\nاختر القسم:",
        reply_markup=kb,
    )


async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    CATALOG = load_catalog()
    counts = []
    for k, v in CATALOG.items():
        if isinstance(v, list):
            counts.append(f"- {k}: {len(v)}")
    msg = "تمت إعادة تحميل الكاتالوج ✅\n" + "\n".join(counts)
    await update.effective_chat.send_message(msg)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = parse_cb(q.data)

    # القوائم الرئيسية
    if parts[0] == "cat":
        key = parts[1]
        await show_category(update, context, key)
        return

    if parts[0] == "child":
        key = parts[1]
        child_idx = int(parts[2])
        await show_child(update, context, key, child_idx)
        return

    if parts[0] == "doc":
        key = parts[1]
        child_idx = int(parts[2])  # -1 لو لا يوجد children
        doc_idx = int(parts[3])
        await send_document(update, context, key, child_idx, doc_idx)
        return

    if parts[0] == "reload":
        await cmd_reload(update, context)
        return

    if parts[0] == "back" and parts[1] == "root":
        await cmd_start(update, context)
        return


async def show_category(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    if not await ensure_member(update, context):
        return

    items = CATALOG.get(key, [])
    # يدعم وجود children (مثلاً security يحتوي "الهكر الأخلاقي")
    buttons: List[Tuple[str, str]] = []
    for idx, it in enumerate(items):
        title = it.get("title", f"Item {idx+1}")
        if "children" in it:
            buttons.append((f"child|{key}|{idx}", f"📁 {title}"))
        else:
            buttons.append((f"doc|{key}|-1|{idx}", f"📄 {title}"))

    rows = chunk(buttons, 1)
    kb = build_kb(rows + [[("back|root", "↩️ رجوع للقائمة")]])
    await update.callback_query.edit_message_text(
        f"اختر من القسم: {key}", reply_markup=kb
    )


async def show_child(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, child_idx: int):
    if not await ensure_member(update, context):
        return

    parent = CATALOG.get(key, [])[child_idx]
    title = parent.get("title", "قسم فرعي")
    ch = parent.get("children", [])
    buttons = []
    for idx, it in enumerate(ch):
        buttons.append((f"doc|{key}|{child_idx}|{idx}", f"📄 {it.get('title','Doc')}"))

    rows = chunk(buttons, 1)
    kb = build_kb(rows + [[("cat|" + key, "↩️ رجوع للقسم")]])
    await update.callback_query.edit_message_text(
        f"اختر من: {title}", reply_markup=kb
    )


async def send_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    key: str,
    child_idx: int,
    doc_idx: int,
):
    if not await ensure_member(update, context):
        return

    # جلب العنصر من الكاتالوج
    src: Dict[str, Any]
    if child_idx == -1:
        src = CATALOG.get(key, [])[doc_idx]
    else:
        src = CATALOG.get(key, [])[child_idx]["children"][doc_idx]

    rel_path = src.get("path", "").lstrip("/")
    file_path = (BASE_DIR / rel_path).resolve()
    exists = file_path.exists()
    size = file_path.stat().st_size if exists else 0

    logger.info(f"[SEND] path={file_path} exists={exists} size={size}")

    if not exists:
        await update.callback_query.edit_message_text(
            f"⚠️ لم أجد الملف على السيرفر:\n`{rel_path}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if size > MAX_BOT_UPLOAD:
        await update.callback_query.edit_message_text(
            f"❗ حجم الملف `{human_size(size)}` أكبر من حد تيليجرام للبوت (~50MB).\n"
            f"فضلاً قلّل حجمه أو جزّئه ثم جرّب مرة أخرى.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.callback_query.edit_message_text("⏳ جاري الإرسال…")
    try:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=FSInputFile(str(file_path)),
            filename=file_path.name,
            caption=src.get("title", file_path.name),
        )
    except Exception as e:
        logger.exception("send_document failed")
        await update.effective_chat.send_message(f"حدث خطأ أثناء الإرسال: {e}")


# =================== Health Server ===================

async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="ok")

async def run_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    logger.info("🌐 Health server on 0.0.0.0:10000")


# =================== Main ===================

def build_application():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CallbackQueryHandler(on_callback))

    return app


def main():
    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN غير مضبوط في متغيرات البيئة.")

    # تحميل الكاتالوج
    global CATALOG
    CATALOG = load_catalog()

    # شغل خادم الصحة
    loop = asyncio.get_event_loop()
    loop.create_task(run_health_server())

    # شغل البوت
    app = build_application()
    logger.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()




