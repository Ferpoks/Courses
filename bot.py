# bot.py
import os
import json
import logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ----------------- إعدادات عامة -----------------
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN") or ""
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip()  # مثال: "@my_channel"

CATALOG_PATH = "assets/catalog.json"
BASE_DIR = Path(__file__).parent.resolve()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("courses-bot")

# لغات الواجهة (العربية الافتراضية)
USER_LANG = {}  # user_id -> "ar" | "en"

L = {
    "ar": {
        "welcome": "مرحبًا بك في مكتبة الكورسات 📚\nاختر القسم:",
        "back": "رجوع",
        "contact": "تواصل مع الإدارة",
        "must_join": "للاستخدام، اشترك أولًا بالقناة ثم اضغط ✅ تم الاشتراك",
        "joined": "✅ تم التحقق — يمكنك المتابعة الآن.",
        "verify": "✅ تم الاشتراك",
        "join_channel": "🔔 الذهاب إلى القناة",
        "missing": "⚠️ لم أجد الملف في السيرفر:\n",
        "sections": {
            "prog": "💻 البرمجة",
            "design": "🎨 التصميم",
            "security": "🛡️ الأمن",
            "languages": "🗣️ اللغات",
            "marketing": "📈 التسويق",
            "maintenance": "🔧 الصيانة",
            "office": "🗂️ البرامج المكتبية",
        },
        "arabic": "🇸🇦 عربي",
        "english": "🇬🇧 English",
    },
    "en": {
        "welcome": "Welcome to the courses library 📚\nPick a category:",
        "back": "Back",
        "contact": "Contact admin",
        "must_join": "Please join the channel first, then press ✅ Joined",
        "joined": "✅ Verified — you can continue.",
        "verify": "✅ Joined",
        "join_channel": "🔔 Go to channel",
        "missing": "⚠️ File not found on server:\n",
        "sections": {
            "prog": "💻 Programming",
            "design": "🎨 Design",
            "security": "🛡️ Security",
            "languages": "🗣️ Languages",
            "marketing": "📈 Marketing",
            "maintenance": "🔧 Maintenance",
            "office": "🗂️ Office apps",
        },
        "arabic": "🇸🇦 عربي",
        "english": "🇬🇧 English",
    },
}

# الامتدادات المسموحة للإرسال كما هي (بدون أي تغيير على بقية المنطق)
ALLOWED_EXTS = {".pdf", ".zip", ".rar"}

# ----------------- تحميل الكتالوج -----------------
def load_catalog() -> dict:
    cat_file = BASE_DIR / CATALOG_PATH
    # للسماح أيضًا بالمسار القديم في الجذر إذا وُجد
    if not cat_file.exists():
        root_alt = BASE_DIR / "catalog.json"
        if root_alt.exists():
            cat_file = root_alt
    log.info("📘 Using catalog file: %s", cat_file.as_posix())
    with cat_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # احصائيات بسيطة
    stats = {k: (len(v) if isinstance(v, list) else len(v.get("children", [])))
             for k, v in data.items()}
    log.info("📦 Catalog on start: %s", stats)
    return data

CATALOG = load_catalog()

# ----------------- سيرفر صحة بسيط -----------------
class Healthz(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), Healthz)
    log.info("🌐 Health server on 0.0.0.0:%s", port)
    Thread(target=server.serve_forever, daemon=True).start()

# ----------------- أدوات مساعدة -----------------
def ulang(update: Update) -> str:
    uid = update.effective_user.id if update.effective_user else 0
    return USER_LANG.get(uid, "ar")

def t(update: Update, key: str) -> str:
    return L[ulang(update)].get(key, key)

def section_label(update: Update, key: str) -> str:
    return L[ulang(update)]["sections"].get(key, key)

async def ensure_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يتحقق من اشتراك المستخدم بالقناة إن كانت محددة عبر env."""
    if not REQUIRED_CHANNEL:
        return True
    user = update.effective_user
    if not user:
        return False
    try:
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL, user.id)
        status = getattr(member, "status", "left")
        if status in ("left", "kicked"):
            # غير مشترك
            kb = [
                [InlineKeyboardButton(L[ulang(update)]["join_channel"], url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")],
                [InlineKeyboardButton(L[ulang(update)]["verify"], callback_data="verify")]
            ]
            await update.effective_message.reply_text(
                L[ulang(update)]["must_join"], reply_markup=InlineKeyboardMarkup(kb)
            )
            return False
        return True
    except Exception:
        # لو فشل الاستعلام لأي سبب نسمح مؤقتًا
        return True

def main_menu_kb(update: Update) -> InlineKeyboardMarkup:
    # ترتيب الأقسام كما في الكتالوج
    order = ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]
    rows = []
    row = []
    for key in order:
        if key in CATALOG:
            row.append(InlineKeyboardButton(section_label(update, key), callback_data=f"cat|{key}"))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)

    # أزرار اللغة والتواصل
    rows.append([
        InlineKeyboardButton(L[ulang(update)]["arabic"], callback_data="lang|ar"),
        InlineKeyboardButton(L[ulang(update)]["english"], callback_data="lang|en"),
    ])
    rows.append([InlineKeyboardButton(L[ulang(update)]["contact"], url="https://t.me/")])  # ضع رابطك إن رغبت
    return InlineKeyboardMarkup(rows)

def back_kb(update: Update) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(L[ulang(update)]["back"], callback_data="back|main")]])

def build_section_kb(section: str, update: Update) -> InlineKeyboardMarkup:
    """يبني قائمة عناصر القسم. يدعم عناصر children (سلاسل) والملفات المباشرة."""
    items = CATALOG.get(section, [])
    rows = []
    for itm in items:
        if "children" in itm:  # مجموعة فرعية
            title = itm.get("title", "Series")
            rows.append([InlineKeyboardButton(f"📚 {title}", callback_data=f"series|{section}")])
        else:
            title = itm.get("title", "file")
            path = itm.get("path", "")
            rows.append([InlineKeyboardButton(f"📄 {title}", callback_data=f"file|{path}")])
    rows.append([InlineKeyboardButton(L[ulang(update)]["back"], callback_data="back|main")])
    return InlineKeyboardMarkup(rows)

def build_series_kb(section: str, update: Update) -> InlineKeyboardMarkup:
    series = None
    for itm in CATALOG.get(section, []):
        if "children" in itm:
            series = itm["children"]
            break
    rows = []
    if series:
        for child in series:
            title = child.get("title", "part")
            path = child.get("path", "")
            rows.append([InlineKeyboardButton(f"📘 {title}", callback_data=f"file|{path}")])
    rows.append([InlineKeyboardButton(L[ulang(update)]["back"], callback_data=f"cat|{section}")])
    return InlineKeyboardMarkup(rows)

# ----------------- إرسال الملفات (PDF/ZIP/RAR) -----------------
async def send_book(update: Update, context: ContextTypes.DEFAULT_TYPE, rel_path: str):
    """يرسل الملف كما هو. يسمح بـ PDF / ZIP / RAR بدون تغيير أي سلوك آخر."""
    fs_path = (BASE_DIR / rel_path).resolve()
    # الأمان: لا نسمح بالخروج خارج مجلد المشروع
    if not str(fs_path).startswith(str(BASE_DIR)):
        log.warning("Blocked path traversal: %s", rel_path)
        await update.effective_message.reply_text(L[ulang(update)]["missing"] + rel_path)
        return

    if not fs_path.exists():
        log.warning("Missing file: %s", rel_path)
        await update.effective_message.reply_text(L[ulang(update)]["missing"] + rel_path)
        return

    ext = fs_path.suffix.lower()
    if ext not in ALLOWED_EXTS:
        # لو الامتداد مختلف، نرسله أيضًا كـ Document (إبقاء السلوك مرنًا)
        log.info("Sending non-whitelisted extension as document: %s", fs_path.name)

    try:
        with fs_path.open("rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=InputFile(f, filename=fs_path.name),
            )
    except Exception as e:
        log.error("Failed to send %s: %s", fs_path, e, exc_info=True)
        await update.effective_message.reply_text(L[ulang(update)]["missing"] + rel_path)

# ----------------- الأوامر والمعالجات -----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # اللغة الافتراضية
    USER_LANG.setdefault(update.effective_user.id, USER_LANG.get(update.effective_user.id, "ar"))

    if not await ensure_membership(update, context):
        return

    await update.effective_message.reply_text(
        t(update, "welcome"),
        reply_markup=main_menu_kb(update),
    )

async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    try:
        CATALOG = load_catalog()
        await update.effective_message.reply_text("✅ تم إعادة تحميل الكاتالوج.")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ خطأ في إعادة التحميل: {e}")

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_membership(update, context):
        return

    q = update.callback_query
    await q.answer()
    data = q.data or ""
    parts = data.split("|", 1)
    kind = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if kind == "verify":
        await q.edit_message_text(L[ulang(update)]["joined"])
        await update.effective_message.reply_text(
            t(update, "welcome"), reply_markup=main_menu_kb(update)
        )
        return

    if kind == "lang":
        lang = rest if rest in ("ar", "en") else "ar"
        USER_LANG[update.effective_user.id] = lang
        await q.edit_message_text(
            t(update, "welcome"), reply_markup=main_menu_kb(update)
        )
        return

    if kind == "back" and rest == "main":
        await q.edit_message_text(
            t(update, "welcome"), reply_markup=main_menu_kb(update)
        )
        return

    if kind == "cat":
        section = rest
        await q.edit_message_text(
            section_label(update, section), reply_markup=build_section_kb(section, update)
        )
        return

    if kind == "series":
        section = rest
        await q.edit_message_text(
            section_label(update, section), reply_markup=build_series_kb(section, update)
        )
        return

    if kind == "file":
        rel_path = rest
        await send_book(update, context, rel_path)
        return

# ----------------- التشغيل -----------------
def main():
    start_health_server()

    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()




