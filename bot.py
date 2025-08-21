# bot.py
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any
import threading, http.server, socketserver

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

# ================== إعداد السجل ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("courses-bot")

# ================== مسارات وبيئة ==================
BASE_DIR = Path(__file__).parent.resolve()
CATALOG_PATH = BASE_DIR / "assets" / "catalog.json"

# يدعم الاسمين لتفادي لخبطة سابقة
TELEGRAM_TOKEN = (os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
if not TELEGRAM_TOKEN:
    print("❌ ضع TELEGRAM_TOKEN في متغيرات البيئة على Render")
    raise SystemExit(1)

# الإدارة
ADMIN_USERNAME = (os.getenv("ADMIN_USERNAME") or os.getenv("OWNER_USERNAME") or "").lstrip("@")

# القنوات المطلوبة: يدعم REQUIRED_CHANNEL أو REQUIRED_CHANNELS (قائمة مفصولة بفواصل)
_required_single = (os.getenv("REQUIRED_CHANNEL") or "").strip().lstrip("@")
_required_multi = os.getenv("REQUIRED_CHANNELS") or ""
REQUIRED_CHANNELS = []
if _required_single:
    REQUIRED_CHANNELS = [_required_single.lower()]
if _required_multi.strip():
    REQUIRED_CHANNELS.extend([c.strip().lstrip("@").lower() for c in _required_multi.split(",") if c.strip()])
# إزالة المكرر
REQUIRED_CHANNELS = list(dict.fromkeys(REQUIRED_CHANNELS))

# عناوين الأقسام
SECTION_TITLES = {
    "prog": "كتب البرمجة 💻",
    "design": "كتب التصميم 🎨",
    "security": "كتب الأمن 🛡️",
    "languages": "كتب اللغات 🌐",
    "marketing": "كتب التسويق 📈",
    "maintenance": "كتب الصيانة 🛠️",
    "office": "كتب البرامج المكتبية 🗂️",
}

# ================== الكاتالوج ==================
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

# ================== أدوات ==================
def file_abs(path_str: str) -> Path:
    return (BASE_DIR / path_str).resolve()

def section_counts_text() -> str:
    lines = ["ℹ️ حالة المحتوى:"]
    for key, title in SECTION_TITLES.items():
        count = 0
        for item in CATALOG.get(key, []):
            count += len(item.get("children", [])) if "children" in item else 1
        lines.append(f"- {title}: {count}")
    return "\n".join(lines)

def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(title, callback_data=f"cat:{key}")]
            for key, title in SECTION_TITLES.items()]
    if ADMIN_USERNAME:
        rows.append([InlineKeyboardButton("🛠️ تواصل مع الإدارة", url=f"https://t.me/{ADMIN_USERNAME}")])
    return InlineKeyboardMarkup(rows)

def back_row():
    return [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back:root")]

async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """تحقق من العضوية؛ إذا لم تُضبط قنوات يسمح للجميع."""
    if not REQUIRED_CHANNELS:
        return True
    try:
        for ch in REQUIRED_CHANNELS:
            member = await context.bot.get_chat_member(f"@{ch}", user_id)
            if member.status not in ("creator", "administrator", "member"):
                return False
        return True
    except BadRequest as e:
        # قناة خاصة/البوت ليس مشرفًا — لا نمنع (تجنب حظر المستخدمين بالخطأ)
        log.warning("[membership] %s", e)
        return True
    except Forbidden:
        return True
    except Exception as e:
        log.exception("[membership] unexpected: %s", e)
        return True

# ================== الأوامر ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("مرحبًا بك في مكتبة الكورسات 📚\nاختر القسم:", reply_markup=main_menu_kb())

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
        msg = f"❌ خطأ في تحميل الكاتالوج: {e}"
    if update.message:
        await update.message.reply_text(msg)

# ================== أزرار الإنلاين ==================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    # رد سريع لتفادي "Query is too old"
    try:
        await q.answer()
    except Exception:
        pass

    data = (q.data or "").strip()

    if data == "back:root":
        try:
            await q.message.edit_text("اختر القسم:", reply_markup=main_menu_kb())
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
        return

    if data.startswith("cat:"):
        key = data.split(":", 1)[1]
        items = CATALOG.get(key, [])
        if not items:
            try:
                await q.message.edit_text(
                    f"⚠️ لا يوجد عناصر في «{SECTION_TITLES.get(key, key)}».",
                    reply_markup=InlineKeyboardMarkup([back_row()])
                )
            except BadRequest as e:
                if "not modified" not in str(e).lower():
                    raise
            return

        buttons = []
        for idx, item in enumerate(items):
            title = item.get("title", f"عنصر {idx+1}")
            if "children" in item:
                buttons.append([InlineKeyboardButton(f"📁 {title}", callback_data=f"sub:{key}:{idx}")])
            else:
                buttons.append([InlineKeyboardButton(title, callback_data=f"doc:{key}:{idx}")])
        buttons.append(back_row())

        try:
            await q.message.edit_text(
                f"{SECTION_TITLES.get(key, key)} – اختر:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
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

# إرسال ملف
async def send_document_flow(q, context: ContextTypes.DEFAULT_TYPE, item: Dict[str, Any], section_key: str) -> None:
    user = q.from_user
    # تحقق اشتراك (مرن)
    ok = True
    if REQUIRED_CHANNELS:
        ok = await is_member(user.id, context)
    if not ok:
        await q.message.reply_text("🔒 يشترط الاشتراك في القناة/القنوات المطلوبة لاستخدام البوت.")
        return

    path = item.get("path")
    if not path:
        await q.message.reply_text("⚠️ لا يوجد مسار للملف.")
        return

    abs_path = file_abs(path)
    if not abs_path.exists():
        await q.message.reply_text(f"🚫 لم أجد الملف في السيرفر:\n{path}")
        return

    try:
        # بدون كابتشن — اسم الملف يكفي كما طلبت
        with open(abs_path, "rb") as f:
            await q.message.reply_document(document=f, filename=abs_path.name, caption="")
    except BadRequest as e:
        log.warning("send_document bad request: %s", e)
        await q.message.reply_text(f"تعذر إرسال الملف: {e}")
    except Exception as e:
        log.exception("send_document error: %s", e)
        await q.message.reply_text("حدث خطأ غير متوقع أثناء إرسال الملف.")

# ================== Health Server (يفتح المنفذ لـ Render) ==================
def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args):  # تقليل ضجيج اللوج
            return

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("0.0.0.0", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    print(f"🌐 Health server on 0.0.0.0:{port} (paths: /)")

# ================== MAIN ==================
def main():
    start_health_server()  # تأكد فتح المنفذ قبل البوت
    log.info("🤖 Telegram bot starting…")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CallbackQueryHandler(on_button))

    # تجاهل أي رسائل عشوائية
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, lambda u, c: None))

    app.run_polling(
        stop_signals=None,            # لتجنّب تضارب الثريد
        close_loop=False,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,    # لا تتعامل مع نقرات قديمة
    )

if __name__ == "__main__":
    main()


