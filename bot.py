# bot.py
import os
import json
import logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.error import Forbidden, BadRequest

# ----------------------- إعدادات -----------------------
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "")
REQUIRED_CHANNEL = (os.getenv("REQUIRED_CHANNEL", "") or "").strip()   # مثل: @my_channel

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "assets"
CATALOG_PATH = ASSETS_DIR / "catalog.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("courses-bot")

# لغة المستخدم بالذاكرة (جلسة العملية)
USER_LANG: dict[int, str] = {}

I18N = {
    "ar": {
        "home_title": "مرحبًا بك في مكتبة الكورسات 📚\nاختر القسم:",
        "back": "رجوع",
        "contact": "تواصل مع الإدارة 🛠️",
        "arabic": "العربية 🇸🇦",
        "english": "English 🇬🇧",
        "not_found": "⚠️ لم أجد الملف في السيرفر:\n<code>{path}</code>",
        "must_join": "لاستخدام البوت، اشترك في القناة أولًا ثم اضغط /start",
        "join_btn": "الانضمام للقناة 📣",
        "series": "سلسلة",
        "diag_header": "الملفات الموجودة:\n{ok}\n\nالمفقودة:\n{miss}",
    },
    "en": {
        "home_title": "Welcome to the courses library 📚\nPick a category:",
        "back": "Back",
        "contact": "Contact admin 🛠️",
        "arabic": "العربية 🇸🇦",
        "english": "English 🇬🇧",
        "not_found": "⚠️ File not found on server:\n<code>{path}</code>",
        "must_join": "Please join the channel first, then press /start",
        "join_btn": "Join channel 📣",
        "series": "Series",
        "diag_header": "Present files:\n{ok}\n\nMissing:\n{miss}",
    },
}

# أسماء الأقسام للعرض فقط (لا نغيّر المسارات)
SECTION_TITLES = {
    "prog":      {"ar": "البرمجة",       "en": "Programming"},
    "design":    {"ar": "التصميم",       "en": "Design"},
    "security":  {"ar": "الأمن",         "en": "Security"},
    "languages": {"ar": "اللغات",        "en": "Languages"},
    "marketing": {"ar": "التسويق",       "en": "Marketing"},
    "maintenance": {"ar": "الصيانة",    "en": "Maintenance"},
    "office":    {"ar": "البرامج المكتبية","en": "Office apps"},
}

CAT_EMOJI = {
    "prog": "💻",
    "design": "🎨",
    "security": "🛡️",
    "languages": "🗣️",
    "marketing": "📈",
    "maintenance": "🛠️",
    "office": "📁",
}

# ترجمة عناوين (عرض فقط). لا نلمس path.
TITLE_EN_MAP = {
    "الهكر الأخلاقي (سلسلة)": "Ethical hacking (Series)",
    "الهكر الأخلاقي (سلسلة) — الجزء 1": "Ethical hacking (Series) — Part 1",
    "الهكر الأخلاقي (سلسلة) — الجزء 2": "Ethical hacking (Series) — Part 2",
    "الهكر الأخلاقي (سلسلة) — الجزء 3": "Ethical hacking (Series) — Part 3",
    "الهكر الأخلاقي (سلسلة) — الجزء 4": "Ethical hacking (Series) — Part 4",
}

# ----------------------- تحميل الكاتالوج -----------------------
def load_catalog() -> dict:
    use_path = CATALOG_PATH if CATALOG_PATH.exists() else (BASE_DIR / "catalog.json")
    log.info("📘 Using catalog file: %s", use_path.relative_to(BASE_DIR))
    with use_path.open("r", encoding="utf-8") as f:
        return json.load(f)

CATALOG = load_catalog()

# ----------------------- لغة/ترجمة -----------------------
def get_lang(user_id: int) -> str:
    return USER_LANG.get(user_id, "ar")

def set_lang(user_id: int, lang: str) -> None:
    USER_LANG[user_id] = "en" if lang == "en" else "ar"

def t(user_id: int, key: str, **kw) -> str:
    return I18N[get_lang(user_id)][key].format(**kw)

def display_title(raw_title, lang: str) -> str:
    # قد يكون العنوان dict متعدد اللغات داخل JSON
    if isinstance(raw_title, dict):
        return raw_title.get(lang) or raw_title.get("ar") or next(iter(raw_title.values()))
    if lang == "en":
        return TITLE_EN_MAP.get(str(raw_title), str(raw_title))
    return str(raw_title)

# ----------------------- اشتراك القناة -----------------------
REQUIRED_CHAT_ID: int | str | None = None

def _norm_channel(username: str) -> str:
    u = username.strip()
    return u if (not u) or u.startswith("@") else f"@{u}"

async def _resolve_required_chat_id(context: ContextTypes.DEFAULT_TYPE) -> int | str | None:
    global REQUIRED_CHAT_ID
    if not REQUIRED_CHANNEL:
        return None
    if REQUIRED_CHAT_ID is not None:
        return REQUIRED_CHAT_ID
    try:
        chat = await context.bot.get_chat(_norm_channel(REQUIRED_CHANNEL))
        REQUIRED_CHAT_ID = chat.id
        return REQUIRED_CHAT_ID
    except Exception as e:
        log.warning("resolve channel failed: %s", e)
        REQUIRED_CHAT_ID = _norm_channel(REQUIRED_CHANNEL)
        return REQUIRED_CHAT_ID

async def ensure_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        user = update.effective_user
        if not user:
            return False
        chat_id = await _resolve_required_chat_id(context)
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user.id)
        return member.status in ("member", "administrator", "creator")
    except Forbidden as e:
        log.warning("membership check forbidden; allowing user: %s", e)
        return True
    except BadRequest as e:
        msg = str(e).lower()
        if "user not found" in msg:
            return False
        log.warning("membership check bad request; allowing user: %s", e)
        return True
    except Exception as e:
        log.warning("membership check failed; allowing user: %s", e)
        return True

async def require_or_hint_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    ok = await ensure_membership(update, context)
    if ok:
        return True
    user_id = update.effective_user.id
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(I18N[get_lang(user_id)]["join_btn"],
                              url=f"https://t.me/{_norm_channel(REQUIRED_CHANNEL).lstrip('@')}")]
    ])
    await update.effective_chat.send_message(t(user_id, "must_join"), reply_markup=kb)
    return False

# ----------------------- بناء القوائم -----------------------
def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    lang = get_lang(user_id)
    rows = []
    for key in ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]:
        title = SECTION_TITLES[key]
        text = f"{CAT_EMOJI.get(key,'📁')} {display_title(title, lang)}"
        rows.append([InlineKeyboardButton(text, callback_data=f"cat:{key}")])
    rows.append([
        InlineKeyboardButton(I18N["ar"]["arabic"], callback_data="lang:ar"),
        InlineKeyboardButton(I18N["en"]["english"], callback_data="lang:en"),
    ])
    contact_label = t(user_id, "contact")
    contact_url = f"https://t.me/{OWNER_USERNAME}" if OWNER_USERNAME else "https://t.me/"
    rows.append([InlineKeyboardButton(contact_label, url=contact_url)])
    return InlineKeyboardMarkup(rows)

def section_kb(user_id: int, cat_key: str) -> InlineKeyboardMarkup:
    lang = get_lang(user_id)
    items = CATALOG.get(cat_key, [])
    rows = []
    for item in items:
        title = display_title(item.get("title"), lang)
        if "children" in item:
            title = f"{title} ({I18N[lang]['series']})"
            rows.append([InlineKeyboardButton(f"📚 {title}", callback_data=f"group:{cat_key}")])
        else:
            rows.append([InlineKeyboardButton(f"📄 {title}", callback_data=f"book:{item['path']}")])
    rows.append([InlineKeyboardButton(f"⬅️ {t(user_id,'back')}", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def series_kb(user_id: int, cat_key: str, children: list[dict]) -> InlineKeyboardMarkup:
    lang = get_lang(user_id)
    rows = []
    for ch in children:
        ch_title = display_title(ch.get("title") or "Part", lang)
        rows.append([InlineKeyboardButton(f"📘 {ch_title}", callback_data=f"book:{ch['path']}")])
    rows.append([InlineKeyboardButton(f"⬅️ {t(user_id,'back')}", callback_data=f"cat:{cat_key}")])
    return InlineKeyboardMarkup(rows)

# ----------------------- محلّل مسارات متسامح -----------------------
def resolve_file(rel_path: str) -> Path | None:
    """
    لا نعدل المسارات من JSON. نجرب:
    1) المسار كما هو.
    2) مطابقة بدون حساسية حالة الأحرف.
    3) تحويل المسافات <-> _.
    """
    try:
        sub = Path(rel_path).relative_to("assets")
    except Exception:
        return None
    p = ASSETS_DIR / sub
    if p.exists():
        return p

    parent = p.parent
    if not parent.exists():
        return None

    target = p.name
    # بدون حساسية حالة الأحرف
    for f in parent.iterdir():
        if f.is_file() and f.name.lower() == target.lower():
            return f

    # مسافة <-> _
    def norm(s: str) -> str:
        return s.lower().replace(" ", "_").replace("%20", "_")

    nt = norm(target)
    for f in parent.iterdir():
        if f.is_file() and norm(f.name) == nt:
            return f

    return None

# ----------------------- Handlers -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_or_hint_join(update, context):
        return
    user_id = update.effective_user.id
    await update.effective_chat.send_message(t(user_id, "home_title"), reply_markup=main_menu_kb(user_id))

async def lang_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, lang = q.data.split(":")
    set_lang(q.from_user.id, lang)
    await q.edit_message_text(t(q.from_user.id, "home_title"), reply_markup=main_menu_kb(q.from_user.id))

async def to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(t(q.from_user.id, "home_title"), reply_markup=main_menu_kb(q.from_user.id))

async def open_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_or_hint_join(update, context):
        return
    q = update.callback_query
    await q.answer()
    _, cat = q.data.split(":")
    kb = section_kb(q.from_user.id, cat)
    head = f"{CAT_EMOJI.get(cat,'📁')} {display_title(SECTION_TITLES[cat], get_lang(q.from_user.id))}"
    await q.edit_message_text(head, reply_markup=kb)

async def open_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, cat = q.data.split(":")
    group = next((i for i in CATALOG.get(cat, []) if "children" in i), None)
    if not group:
        await q.edit_message_reply_markup(reply_markup=section_kb(q.from_user.id, cat))
        return
    kb = series_kb(q.from_user.id, cat, group["children"])
    await q.edit_message_text(f"📚 {display_title(group['title'], get_lang(q.from_user.id))}", reply_markup=kb)

async def send_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_or_hint_join(update, context):
        return
    q = update.callback_query
    await q.answer()
    _, rel_path = q.data.split(":", 1)

    fs_path = resolve_file(rel_path)
    if not fs_path:
        log.warning("Missing file: %s", rel_path)
        await q.edit_message_text(
            t(q.from_user.id, "not_found", path=rel_path),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"⬅️ {t(q.from_user.id,'back')}", callback_data="home")]]),
        )
        return

    try:
        with fs_path.open("rb") as f:
            await context.bot.send_document(
                chat_id=q.message.chat_id,
                document=InputFile(f, filename=fs_path.name),
                caption=fs_path.name,
            )
    except Exception as e:
        log.error("Failed to send %s: %s", fs_path, e)

async def reload_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if OWNER_USERNAME and update.effective_user.username != OWNER_USERNAME:
        return
    global CATALOG
    CATALOG = load_catalog()
    stat = {k: len(v) for k, v in CATALOG.items()}
    await update.message.reply_text(f"✅ تم إعادة تحميل الكاتالوج:\n{stat}")

# أمر تشخيصي: /where maintenance  (أو أي قسم)
async def where_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /where <category>")
        return
    cat = context.args[0]
    items = CATALOG.get(cat, [])
    ok, miss = [], []
    for item in items:
        if "children" in item:
            for ch in item["children"]:
                p = ch["path"]
                (ok if resolve_file(p) else miss).append(p)
        else:
            p = item["path"]
            (ok if resolve_file(p) else miss).append(p)
    txt = I18N[get_lang(update.effective_user.id)]["diag_header"].format(
        ok="\n".join(f"• {x}" for x in ok) or "—",
        miss="\n".join(f"• {x}" for x in miss) or "—",
    )
    await update.message.reply_text(txt)

# ----------------------- Health server -----------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(200); self.end_headers(); self.wfile.write(b".")


def run_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("🌐 Health server on 0.0.0.0:%d", port)
    server.serve_forever()

# ----------------------- main -----------------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reload", reload_catalog))
    app.add_handler(CommandHandler("where", where_cmd))

    app.add_handler(CallbackQueryHandler(lang_switch, pattern=r"^lang:(ar|en)$"))
    app.add_handler(CallbackQueryHandler(to_home, pattern=r"^home$"))
    app.add_handler(CallbackQueryHandler(open_category, pattern=r"^cat:(.+)$"))
    app.add_handler(CallbackQueryHandler(open_group, pattern=r"^group:(.+)$"))
    app.add_handler(CallbackQueryHandler(send_book, pattern=r"^book:.+"))

    import threading
    threading.Thread(target=run_health_server, daemon=True).start()

    log.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()




