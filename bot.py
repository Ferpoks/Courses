# bot.py
import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Any

from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, Forbidden

# ==== إعدادات عامة ===========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("courses-bot")

BASE_DIR = Path(__file__).parent.resolve()
CATALOG_PATH = BASE_DIR / "assets" / "catalog.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
OWNER_USERNAME = (os.getenv("OWNER_USERNAME") or "").lstrip("@")
REQUIRED_CHANNELS = [c.strip().lstrip("@").lower()
                     for c in (os.getenv("REQUIRED_CHANNELS") or "").split(",")
                     if c.strip()]

if not TELEGRAM_TOKEN:
    print("❌ ضع TELEGRAM_TOKEN في متغيرات البيئة على Render")
    raise SystemExit(1)

SECTION_TITLES = {
    "prog": "كتب البرمجة 💻",
    "design": "كتب التصميم 🎨",
    "security": "كتب الأمن 🛡️",
    "languages": "كتب اللغات 🌐",
    "marketing": "كتب التسويق 📈",
    "maintenance": "كتب الصيانة 🛠️",
    "office": "كتب البرامج المكتبية 🗂️",
}

# ==== تحميل الكاتالوج ========================================================
Catalog = Dict[str, List[Dict[str, Any]]]
CATALOG: Catalog = {k: [] for k in SECTION_TITLES.keys()}

def load_catalog() -> Catalog:
    if not CATALOG_PATH.exists():
        log.warning("catalog.json غير موجود في: %s", CATALOG_PATH)
        return {k: [] for k in SECTION_TITLES.keys()}
    with CATALOG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for k in SECTION_TITLES.keys():
        data.setdefault(k, [])
    return data

CATALOG = load_catalog()
log.info("📦 Catalog on start: %s", {k: len(v) for k, v in CATALOG.items()})

# ==== أدوات مساعدة ===========================================================
def file_abs(path_str: str) -> Path:
    return (BASE_DIR / path_str).resolve()

def section_counts_text() -> str:
    lines = ["ℹ️ حالة المحتوى:"]
    for key, title in SECTION_TITLES.items():
        lines.append(f"- {title}: {len(CATALOG.get(key, []))}")
    return "\n".join(lines)

def main_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, title in SECTION_TITLES.items():
        rows.append([InlineKeyboardButton(title, callback_data=f"cat:{key}")])
    if OWNER_USERNAME:
        rows.append([InlineKeyboardButton("🛠️ تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}")])
    return InlineKeyboardMarkup(rows)

def back_row():
    return [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back:root")]

async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not REQUIRED_CHANNELS:
        return True
    try:
        for ch in REQUIRED_CHANNELS:
            member = await context.bot.get_chat_member(f"@{ch}", user_id)
            if member.status in ("creator", "administrator", "member"):
                continue
            return False
        return True
    except BadRequest as e:
        log.warning("[membership] %s", e)  # قناة خاصة أو البوت ليس مشرفاً
        return True
    except Forbidden:
        return True
    except Exception as e:
        log.exception("[membership] unexpected: %s", e)
        return True

# ==== Handlers ===============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "مرحبًا بك في مكتبة الكورسات 📚\nاختر القسم المطلوب:",
            reply_markup=main_menu_kb()
        )

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(section_counts_text())

async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global CATALOG
    try:
        CATALOG = load_catalog()
        msg = "✅ تم إعادة تحميل الكاتالوج:\n" + section_counts_text()
    except Exception as e:
        log.exception("reload error: %s", e)
        msg = f"❌ حدث خطأ أثناء تحميل الكاتالوج: {e}"
    if update.message:
        await update.message.reply_text(msg)

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
    except Exception:
        pass

    data = q.data or ""
    if data.startswith("cat:"):
        key = data.split(":", 1)[1]
        items = CATALOG.get(key, [])
        if not items:
            await q.message.edit_text(
                f"⚠️ لا يوجد عناصر في «{SECTION_TITLES.get(key, key)}» حاليًا.",
                reply_markup=InlineKeyboardMarkup([back_row()])
            )
            return

        buttons = []
        for idx, item in enumerate(items):
            title = item.get("title", f"عنصر {idx+1}")
            if "children" in item:
                buttons.append([InlineKeyboardButton(f"📁 {title}", callback_data=f"sub:{key}:{idx}")])
            else:
                buttons.append([InlineKeyboardButton(title, callback_data=f"doc:{key}:{idx}")])
        buttons.append(back_row())
        await q.message.edit_text(
            f"{SECTION_TITLES.get(key, key)} – اختر عنصرًا:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("sub:"):
        _, key, parent_idx = data.split(":", 2)
        parent = CATALOG.get(key, [])[int(parent_idx)]
        children = parent.get("children", [])
        if not children:
            await q.message.edit_text("⚠️ لا يوجد عناصر.", reply_markup=InlineKeyboardMarkup([back_row()]))
            return
        buttons = []
        for cidx, ch in enumerate(children):
            buttons.append([InlineKeyboardButton(ch.get("title", f"جزء {cidx+1}"), callback_data=f"docsub:{key}:{parent_idx}:{cidx}")])
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"cat:{key}")])
        await q.message.edit_text(parent.get("title", "قائمة فرعية:"), reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("docsub:"):
        _, key, pidx, cidx = data.split(":", 3)
        item = CATALOG.get(key, [])[int(pidx)]["children"][int(cidx)]
        await send_document_flow(q, context, item, section_key=key)
        return

    if data.startswith("doc:"):
        _, key, idx = data.split(":", 2)
        item = CATALOG.get(key, [])[int(idx)]
        await send_document_flow(q, context, item, section_key=key)
        return

    if data == "back:root":
        await q.message.edit_text("اختر القسم المطلوب:", reply_markup=main_menu_kb())

async def send_document_flow(q, context: ContextTypes.DEFAULT_TYPE, item: Dict[str, Any], section_key: str) -> None:
    user = q.from_user
    ok = True
    if OWNER_USERNAME and user.username and user.username.lower() == OWNER_USERNAME.lower():
        ok = True
    else:
        ok = await is_member(user.id, context)

    if not ok:
        await q.message.reply_text("🔒 يشترط الاشتراك في القنوات المطلوبة لاستخدام البوت.")
        return

    path = item.get("path")
    title = item.get("title", "ملف")
    if not path:
        await q.message.reply_text("⚠️ لا يوجد مسار للملف.")
        return

    abs_path = file_abs(path)
    if not abs_path.exists():
        await q.message.reply_text(f"🚫 لم أجد الملف في السيرفر:\n{path}")
        return

    try:
        # استخدام فتح الملف مباشرةً — يعمل مع كل إصدارات المكتبة
        with open(abs_path, "rb") as f:
            await q.message.reply_document(document=f, caption=title)
    except BadRequest as e:
        log.warning("send_document bad request: %s", e)
        await q.message.reply_text(f"تعذر إرسال الملف: {e}")
    except Exception as e:
        log.exception("send_document error: %s", e)
        await q.message.reply_text("حدث خطأ غير متوقع أثناء إرسال الملف.")

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 رجوع", callback_data=f"cat:{section_key}")]]
    )
    try:
        await q.message.reply_text("تم الإرسال ✅", reply_markup=kb)
    except Exception:
        pass

# ==== Health server (AIOHTTP) ================================================
async def handle_health(_request):
    return web.Response(text="ok")

def run_health_server():
    app = web.Application()
    app.add_routes([
        web.get("/", handle_health),
        web.get("/health", handle_health),
        web.get("/healthz", handle_health),
    ])
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

# ==== Main ===================================================================
def main():
    import threading
    threading.Thread(target=run_health_server, daemon=True).start()
    log.info("🌐 Health server on 0.0.0.0:%s (paths: /healthz,/health,/)", os.getenv("PORT", "10000"))

    log.info("🤖 Telegram bot starting…")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("check", cmd_check))
    application.add_handler(CommandHandler("reload", cmd_reload))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, lambda u, c: None))

    application.run_polling(
        stop_signals=None,
        close_loop=False,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()

