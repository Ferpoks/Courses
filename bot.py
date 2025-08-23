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
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ===================== إعدادات عامة =====================
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TOKEN") or ""
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip()  # مثال: @my_channel
OWNER_USERNAME = (os.getenv("OWNER_USERNAME") or os.getenv("ADMIN_USERNAME") or "").lstrip("@")

CATALOG_PATH = "assets/catalog.json"
BASE_DIR = Path(__file__).parent.resolve()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("courses-bot")

# لغة المستخدم
USER_LANG: dict[int, str] = {}  # user_id -> 'ar' | 'en'
# رسالة القائمة (لنتحكم بها من أزرار اللوحة السفلية)
MENU_MSG: dict[int, tuple[int, int]] = {}  # user_id -> (chat_id, message_id)

L = {
    "ar": {
        "welcome": "مرحبًا بك في مكتبة الكورسات 📚\nاختر القسم:",
        "back": "رجوع",
        "contact": "قناة المطور 🧑‍💻",
        "contact_short": "تواصل مع الإدارة",
        "must_join": "الرجاء الاشتراك في القناة أولًا ثم اضغط ✅ تم الاشتراك",
        "joined": "✅ تم التحقق — يمكنك المتابعة الآن.",
        "verify": "✅ تم الاشتراك",
        "join_channel": "🔔 الذهاب إلى القناة",
        "missing": "⚠️ لم أجد الملف في السيرفر:\n",
        "change_language": "🌍 تغيير اللغة | Change Language",
        "start": "▶️ Start",
        "help": "🆘 Help",
        "myinfo": "🪪 معلوماتي",
        "greet": "👋 الترحيب",
        "help_text_contact": "للتواصل مع الإدارة:",
        "greet_text": "أهلًا وسهلًا! استمتع بالتصفح 🤍",
        "info_fmt": "اسم: {name}\nيوزر: @{user}\nمعرّف: {uid}\nاللغة: {lang}",
        "sections": {
            "prog": "💻 البرمجة",
            "design": "🎨 التصميم",
            "security": "🛡️ الأمن",
            "languages": "🗣️ اللغات",
            "marketing": "📈 التسويق",
            "maintenance": "🔧 الصيانة",
            "office": "🗂️ البرامج المكتبية",
        },
    },
    "en": {
        "welcome": "Welcome to the courses library 📚\nPick a category:",
        "back": "Back",
        "contact": "Developer channel 🧑‍💻",
        "contact_short": "Contact admin",
        "must_join": "Please join the channel first, then press ✅ Joined",
        "joined": "✅ Verified — you can continue.",
        "verify": "✅ Joined",
        "join_channel": "🔔 Go to channel",
        "missing": "⚠️ File not found on server:\n",
        "change_language": "🌍 Change Language | تغيير اللغة",
        "start": "▶️ Start",
        "help": "🆘 Help",
        "myinfo": "🪪 My info",
        "greet": "👋 Welcome",
        "help_text_contact": "Contact the admin:",
        "greet_text": "Hi there! Enjoy browsing 🤍",
        "info_fmt": "Name: {name}\nUser: @{user}\nUser ID: {uid}\nLang: {lang}",
        "sections": {
            "prog": "💻 Programming",
            "design": "🎨 Design",
            "security": "🛡️ Security",
            "languages": "🗣️ Languages",
            "marketing": "📈 Marketing",
            "maintenance": "🔧 Maintenance",
            "office": "🗂️ Office apps",
        },
    },
}

ALLOWED_EXTS = {".pdf", ".zip", ".rar"}

# ===================== تحميل الكتالوج =====================
def load_catalog() -> dict:
    cat_file = BASE_DIR / CATALOG_PATH
    if not cat_file.exists():
        alt = BASE_DIR / "catalog.json"
        if alt.exists():
            cat_file = alt
    log.info("📘 Using catalog file: %s", cat_file.as_posix())
    with cat_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    stats = {k: (len(v) if isinstance(v, list) else len(v.get("children", [])))
             for k, v in data.items()}
    log.info("📦 Catalog on start: %s", stats)
    return data

CATALOG = load_catalog()

# ===================== Health server =====================
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
    HTTPServer(("0.0.0.0", port), Healthz).serve_forever()

def start_health_thread():
    Thread(target=start_health_server, daemon=True).start()
    log.info("🌐 Health server on 0.0.0.0:%s", os.getenv("PORT", "10000"))

# ===================== أدوات لغة/قوائم =====================
def ulang(update: Update) -> str:
    uid = update.effective_user.id if update.effective_user else 0
    return USER_LANG.get(uid, "ar")

def t(update: Update, key: str) -> str:
    return L[ulang(update)].get(key, key)

def section_label(update: Update, key: str) -> str:
    return L[ulang(update)]["sections"].get(key, key)

def bottom_keyboard(update: Update) -> ReplyKeyboardMarkup:
    s = L[ulang(update)]["sections"]
    rows = [
        [KeyboardButton(s["prog"]), KeyboardButton(s["design"])],
        [KeyboardButton(s["security"]), KeyboardButton(s["languages"])],
        [KeyboardButton(s["marketing"]), KeyboardButton(s["maintenance"])],
        [KeyboardButton(s["office"])],
        [KeyboardButton(L[ulang(update)]["change_language"]),
         KeyboardButton(L[ulang(update)]["contact_short"])],
        [KeyboardButton(L[ulang(update)]["start"]),
         KeyboardButton(L[ulang(update)]["help"])],
        [KeyboardButton(L[ulang(update)]["myinfo"]),
         KeyboardButton(L[ulang(update)]["greet"])],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def contact_inline_button(update: Update):
    if OWNER_USERNAME:
        return InlineKeyboardButton(L[ulang(update)]["contact"],
                                    url=f"https://t.me/{OWNER_USERNAME}")
    return None

def main_menu_inline(update: Update) -> InlineKeyboardMarkup:
    order = ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]
    rows, row = [], []
    for key in order:
        if key in CATALOG:
            row.append(InlineKeyboardButton(section_label(update, key), callback_data=f"cat|{key}"))
            if len(row) == 2:
                rows.append(row); row = []
    if row: rows.append(row)
    lang_row = [
        InlineKeyboardButton("🇸🇦 عربي", callback_data="lang|ar"),
        InlineKeyboardButton("🇬🇧 English", callback_data="lang|en"),
    ]
    rows.append(lang_row)
    btn = contact_inline_button(update)
    if btn:
        rows.append([btn])
    return InlineKeyboardMarkup(rows)

def build_section_kb(section: str, update: Update) -> InlineKeyboardMarkup:
    items = CATALOG.get(section, [])
    rows = []
    for itm in items:
        if "children" in itm:
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
            series = itm["children"]; break
    rows = []
    if series:
        for child in series:
            rows.append([InlineKeyboardButton(f"📘 {child.get('title','part')}",
                                              callback_data=f"file|{child.get('path','')}")])
    rows.append([InlineKeyboardButton(L[ulang(update)]["back"], callback_data=f"cat|{section}")])
    return InlineKeyboardMarkup(rows)

# ===================== اشتراك القناة =====================
async def ensure_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    user = update.effective_user
    if not user:
        return False
    try:
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL, user.id)
        status = getattr(member, "status", "left")
        if status in ("left", "kicked"):
            kb = [
                [InlineKeyboardButton(L[ulang(update)]["join_channel"],
                                      url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")],
                [InlineKeyboardButton(L[ulang(update)]["verify"], callback_data="verify")],
            ]
            await update.effective_message.reply_text(
                L[ulang(update)]["must_join"], reply_markup=InlineKeyboardMarkup(kb)
            )
            return False
        return True
    except Exception:
        # لو فشل الاستعلام لأي سبب، نسمح
        return True

# ===================== إرسال الملفات =====================
async def send_book(update: Update, context: ContextTypes.DEFAULT_TYPE, rel_path: str):
    fs_path = (BASE_DIR / rel_path).resolve()
    if not str(fs_path).startswith(str(BASE_DIR)):
        log.warning("Blocked path traversal: %s", rel_path)
        await update.effective_message.reply_text(L[ulang(update)]["missing"] + rel_path)
        return
    if not fs_path.exists():
        log.warning("Missing file: %s", rel_path)
        await update.effective_message.reply_text(L[ulang(update)]["missing"] + rel_path)
        return
    try:
        with fs_path.open("rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=InputFile(f, filename=fs_path.name),
            )
    except Exception as e:
        log.error("Failed to send %s: %s", fs_path, e, exc_info=True)
        await update.effective_message.reply_text(L[ulang(update)]["missing"] + rel_path)

# ===================== أدوات التحكم برسالة القائمة =====================
async def set_menu_message(user_id: int, chat_id: int, message_id: int):
    MENU_MSG[user_id] = (chat_id, message_id)

def get_menu_message(user_id: int):
    return MENU_MSG.get(user_id)

async def ensure_menu_exists(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pair = get_menu_message(uid)
    if not pair:
        # أنشئ رسالة القائمة الأولى
        msg = await update.effective_message.reply_text(
            t(update, "welcome"),
            reply_markup=main_menu_inline(update),
        )
        await set_menu_message(uid, msg.chat.id, msg.message_id)

async def menu_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, kb: InlineKeyboardMarkup):
    uid = update.effective_user.id
    pair = get_menu_message(uid)
    if not pair:
        msg = await update.effective_message.reply_text(text, reply_markup=kb)
        await set_menu_message(uid, msg.chat.id, msg.message_id)
        return
    chat_id, msg_id = pair
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            reply_markup=kb,
        )
    except Exception:
        # لو فشل (محذوفة مثلاً) أرسل جديدة وخزّنها
        msg = await update.effective_message.reply_text(text, reply_markup=kb)
        await set_menu_message(uid, msg.chat.id, msg.message_id)

# ===================== أوامر ومعالجات =====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER_LANG.setdefault(update.effective_user.id, USER_LANG.get(update.effective_user.id, "ar"))
    if not await ensure_membership(update, context):
        return
    # لوحة سفلية + تجهيز رسالة القائمة الواحدة
    await update.effective_message.reply_text(
        t(update, "welcome"),
        reply_markup=bottom_keyboard(update),
    )
    await ensure_menu_exists(update, context)
    # تحديث رسالة القائمة إلى القائمة الرئيسية
    await menu_edit(update, context, t(update, "welcome"), main_menu_inline(update))

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # الآن الهلب = تواصل مع الإدارة
    if not await ensure_membership(update, context):
        return
    if OWNER_USERNAME:
        await update.effective_message.reply_text(
            f"{L[ulang(update)]['help_text_contact']} https://t.me/{OWNER_USERNAME}",
            reply_markup=bottom_keyboard(update),
            disable_web_page_preview=True,
        )
    else:
        await update.effective_message.reply_text(
            "ضع OWNER_USERNAME في متغيرات البيئة لتمكين رابط التواصل.",
            reply_markup=bottom_keyboard(update),
        )

async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    try:
        CATALOG = load_catalog()
        await update.effective_message.reply_text("✅ تم إعادة تحميل الكاتالوج.")
        # حدث رسالة القائمة الرئيسية بعد الإعادة
        await menu_edit(update, context, t(update, "welcome"), main_menu_inline(update))
    except Exception as e:
        await update.effective_message.reply_text(f"❌ خطأ في إعادة التحميل: {e}")

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_membership(update, context):
        return
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    kind, _, rest = data.partition("|")

    # حدّث مؤشر رسالة القائمة (حتى لو تبدلت)
    await set_menu_message(update.effective_user.id, q.message.chat.id, q.message.message_id)

    if kind == "verify":
        await q.edit_message_text(t(update, "welcome"), reply_markup=main_menu_inline(update))
        await update.effective_message.reply_text(
            L[ulang(update)]["joined"], reply_markup=bottom_keyboard(update)
        )
        return

    if kind == "lang":
        USER_LANG[update.effective_user.id] = "ar" if rest == "ar" else "en"
        await q.edit_message_text(t(update, "welcome"), reply_markup=main_menu_inline(update))
        return

    if kind == "back" and rest == "main":
        await q.edit_message_text(t(update, "welcome"), reply_markup=main_menu_inline(update))
        return

    if kind == "cat":
        section = rest
        await q.edit_message_text(section_label(update, section), reply_markup=build_section_kb(section, update))
        return

    if kind == "series":
        section = rest
        await q.edit_message_text(section_label(update, section), reply_markup=build_series_kb(section, update))
        return

    if kind == "file":
        await send_book(update, context, rest)
        return

# خرائط عناوين الأقسام (لللوحة السفلية)
def label_to_section_map(lang: str) -> dict[str, str]:
    return {v: k for k, v in L[lang]["sections"].items()}

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_membership(update, context):
        return
    text = (update.message.text or "").strip()
    uid = update.effective_user.id
    lang = USER_LANG.get(uid, "ar")

    # القسم المختار من اللوحة السفلية → حرّك رسالة القائمة (Inline) بدل إنشاء رسالة جديدة
    for l in ("ar", "en"):
        sec_map = label_to_section_map(l)
        if text in sec_map:
            key = sec_map[text]
            await menu_edit(update, context, section_label(update, key), build_section_kb(key, update))
            return

    # تغيير اللغة (زر سُفلي)
    if text == L[lang]["change_language"]:
        USER_LANG[uid] = ("en" if lang == "ar" else "ar")
        await update.effective_message.reply_text(
            t(update, "welcome"),
            reply_markup=bottom_keyboard(update),
        )
        # حدث رسالة القائمة أيضًا
        await menu_edit(update, context, t(update, "welcome"), main_menu_inline(update))
        return

    # تواصل مع الإدارة (زر سُفلي)
    if text == L[lang]["contact_short"] or text == L[lang]["help"]:
        if OWNER_USERNAME:
            await update.effective_message.reply_text(
                f"{L[ulang(update)]['help_text_contact']} https://t.me/{OWNER_USERNAME}",
                reply_markup=bottom_keyboard(update),
                disable_web_page_preview=True,
            )
        else:
            await update.effective_message.reply_text(
                "ضع OWNER_USERNAME في متغيرات البيئة لتمكين رابط التواصل.",
                reply_markup=bottom_keyboard(update),
            )
        return

    # Start / معلوماتي / الترحيب
    if text == L[lang]["start"]:
        await cmd_start(update, context); return

    if text == L[lang]["myinfo"]:
        name = (update.effective_user.full_name or "-")
        user = (update.effective_user.username or "-")
        msg = L[lang]["info_fmt"].format(name=name, user=user, uid=update.effective_user.id, lang=lang)
        await update.effective_message.reply_text(msg, reply_markup=bottom_keyboard(update))
        return

    if text == L[lang]["greet"]:
        await update.effective_message.reply_text(L[lang]["greet_text"], reply_markup=bottom_keyboard(update))
        return

# ===================== التشغيل =====================
def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set")
    start_health_thread()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))  # الآن help = تواصل مع الإدارة
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()



