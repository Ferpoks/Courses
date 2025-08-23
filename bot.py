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

# ----------------------- إعدادات أساسية -----------------------
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "")  # مثال: @my_channel
BASE_DIR = Path(__file__).parent
CATALOG_PATH = BASE_DIR / "assets" / "catalog.json"
ASSETS_DIR = BASE_DIR / "assets"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("courses-bot")

# لغة افتراضية لكل مستخدم (بالذاكرة)
USER_LANG: dict[int, str] = {}

# قاموس نصوص الواجهة
I18N = {
    "ar": {
        "home_title": "مرحبًا بك في مكتبة الكورسات 📚\nاختر القسم:",
        "back": "رجوع",
        "contact": "تواصل مع الإدارة 🛠️",
        "arabic": "العربية 🇸🇦",
        "english": "English 🇬🇧",
        "not_found": "⚠️ لم أجد الملف في السيرفر:\n<code>{path}</code>",
        "must_join": "للاستخدام يجب الاشتراك في القناة أولاً ثم اضغط /start",
        "menu_contact_value": "https://t.me/{admin}",
        "series": "سلسلة",
    },
    "en": {
        "home_title": "Welcome to the courses library 📚\nPick a category:",
        "back": "Back",
        "contact": "Contact admin 🛠️",
        "arabic": "العربية 🇸🇦",
        "english": "English 🇬🇧",
        "not_found": "⚠️ File not found on server:\n<code>{path}</code>",
        "must_join": "Please join the channel first, then press /start.",
        "menu_contact_value": "https://t.me/{admin}",
        "series": "Series",
    },
}

# أيقونات الأقسام
CAT_EMOJI = {
    "prog": "💻",
    "design": "🎨",
    "security": "🛡️",
    "languages": "🗣️",
    "marketing": "📈",
    "maintenance": "🛠️",
    "office": "📁",
}

# قاموس ترجمة للعناوين العربية الشائعة في صورك (للوضع الإنجليزي إذا لم تتوفر title_en)
TITLE_EN_MAP = {
    # ---- programming (أمثلة من صورك) ----
    "تعلّم يونتي Unity": "Learn Unity",
    "PHP و MySQL": "PHP and MySQL",
    "تعلم C++ من الصفر خطوة بخطوة": "C++ step by step",
    "خبير المسار الوظيفي": "Career expert",
    "لغة البرمجة": "Programming language",
    "دليل الماتش الكامل": "Maths complete guide",
    "JavaScript للمبتدئين": "JavaScript for beginners",
    "علوم الحاسب من الألف إلى الياء": "Computer science from A to Z",
    "نصائح في بيانات بايثون": "Python data tips",
    "س/ج تعلم الذكاء الاصطناعي": "ML/DL/DS Q&A",
    "دخل علمي": "Deep learning PDF",

    # ---- design ----
    "دليل هوية العلامة": "Brand identity guide",
    "أساسيات التصميم الجرافيكي": "Graphic design basics",
    "قوالب تصميم شعارات": "Logo design templates",

    # ---- security ----
    "أمن الأجهزة المحمولة": "Security for mobile",
    "نظام Kali linux": "Kali Linux OS",
    "أخلاقيات الأمن": "Security ethics",
    "اختراق الشبكات": "Network hacking",
    "لينكس للمبتدئين": "Linux for beginners",
    "الهكر الأخلاقي (سلسلة)": "Ethical hacking (Series)",
    "الهكر الأخلاقي (سلسلة) — الجزء 1": "Ethical hacking (Series) — Part 1",
    "الهكر الأخلاقي (سلسلة) — الجزء 2": "Ethical hacking (Series) — Part 2",
    "الهكر الأخلاقي (سلسلة) — الجزء 3": "Ethical hacking (Series) — Part 3",
    "الهكر الأخلاقي (سلسلة) — الجزء 4": "Ethical hacking (Series) — Part 4",
    "اختبار اختراق تطبيقات الويب": "Web app hacking",

    # ---- languages ----
    "١٠٠ محادثة إنجليزية": "100 English conversations",
    "تحدث الإنجليزية في 10 أيام": "Speak English in 10 days",
    "إنجليزي مستوى 1": "English level 1",

    # ---- marketing ----
    "دليل العمل الحر": "Freelancing guide",
    "تسويق عبر النت": "Network marketing",
    "دليل المنتجات الرقمية": "Sell digital products guide",
    "دليل السيو": "SEO guide",
    "مصطلحات التسويق": "Marketing terms",

    # ---- maintenance ----
    "مكونات صيانة الجوال": "Mobile maintenance components",
    "أساسيات صيانة الجوال": "Mobile maintenance basics",
    "ورشة صيانة الجوال": "Mobile repair workshop",

    # ---- office ----
    "excel": "excel",
    "تعلم Microsoft word": "Microsoft Word",
    "شرح الاكسل خطوة بخطوة": "Excel step by step",
}

# تحميل الكاتالوج
def load_catalog() -> dict:
    use_path = CATALOG_PATH
    if not use_path.exists():
        # دعم المسار القديم إن وجد
        alt = BASE_DIR / "catalog.json"
        if alt.exists():
            use_path = alt
    log.info("📘 Using catalog file: %s", use_path.relative_to(BASE_DIR))
    with use_path.open("r", encoding="utf-8") as f:
        return json.load(f)

CATALOG = load_catalog()

# ----------------------- أدوات لغة/ترجمة -----------------------
def get_lang(user_id: int) -> str:
    return USER_LANG.get(user_id, "ar")

def set_lang(user_id: int, lang: str) -> None:
    USER_LANG[user_id] = "en" if lang == "en" else "ar"

def t(user_id: int, key: str, **kwargs) -> str:
    lang = get_lang(user_id)
    return I18N[lang][key].format(**kwargs)

def display_title(raw_title, lang: str) -> str:
    """يدعم:
       - title كنص: يحاول ترجمة عبر TITLE_EN_MAP لو lang='en'
       - أو title ككائن {"ar": "...", "en": "..."}"""
    if isinstance(raw_title, dict):
        return raw_title.get(lang) or raw_title.get("ar") or next(iter(raw_title.values()))
    if lang == "en":
        return TITLE_EN_MAP.get(str(raw_title).strip(), str(raw_title))
    return str(raw_title)

# ----------------------- تحقّق الاشتراك -----------------------
async def ensure_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        user = update.effective_user
        if not user:
            return False
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user.id)
        status = member.status  # 'creator','administrator','member','left','kicked','restricted'
        if status in ("member", "administrator", "creator"):
            return True
    except Exception as e:
        log.warning("membership check failed: %s", e)
    # غير مشترك
    if update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=t(update.effective_user.id, "must_join"),
        )
    return False

# ----------------------- بناء القوائم -----------------------
def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    lang = get_lang(user_id)
    rows = []
    # الأقسام
    for key in ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]:
        label_ar = key  # سنعرض مترجم
        title = {
            "prog": {"ar": "البرمجة", "en": "Programming"},
            "design": {"ar": "التصميم", "en": "Design"},
            "security": {"ar": "الأمن", "en": "Security"},
            "languages": {"ar": "اللغات", "en": "Languages"},
            "marketing": {"ar": "التسويق", "en": "Marketing"},
            "maintenance": {"ar": "الصيانة", "en": "Maintenance"},
            "office": {"ar": "البرامج المكتبية", "en": "Office apps"},
        }[key]
        text = f"{CAT_EMOJI.get(key,'📁')} {display_title(title, lang)}"
        rows.append([InlineKeyboardButton(text, callback_data=f"cat:{key}")])
    # سطر اللغة + تواصل
    rows.append([
        InlineKeyboardButton(I18N["ar"]["arabic"], callback_data="lang:ar"),
        InlineKeyboardButton(I18N["en"]["english"], callback_data="lang:en"),
    ])
    contact_label = t(user_id, "contact")
    rows.append([InlineKeyboardButton(contact_label, url=t(user_id, "menu_contact_value", admin=OWNER_USERNAME or ''))])
    return InlineKeyboardMarkup(rows)

def back_kb(user_id: int, parent: str | None) -> InlineKeyboardMarkup:
    rows = []
    if parent:
        rows.append([InlineKeyboardButton(f"⬅️ {t(user_id, 'back')}", callback_data=f"cat:{parent}")])
    else:
        rows.append([InlineKeyboardButton(f"🏠 {t(user_id, 'back')}", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def section_kb(user_id: int, cat_key: str) -> InlineKeyboardMarkup:
    lang = get_lang(user_id)
    items = CATALOG.get(cat_key, [])
    rows = []
    for item in items:
        # عنصر عادي أو مجموعة (children)
        title = display_title(item.get("title"), lang)
        emoji = "📄"
        if "children" in item:
            series_word = I18N[lang]["series"]
            title = f"{title} ({series_word})"
            emoji = "📚"
            rows.append([InlineKeyboardButton(f"{emoji} {title}", callback_data=f"group:{cat_key}:{title}")])
        else:
            rows.append([InlineKeyboardButton(f"{emoji} {title}", callback_data=f"book:{item['path']}")])
    # زر رجوع
    rows.append([InlineKeyboardButton(f"⬅️ {t(user_id,'back')}", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def series_kb(user_id: int, cat_key: str, group_title_ar: str, children: list[dict]) -> InlineKeyboardMarkup:
    lang = get_lang(user_id)
    rows = []
    # نعرض الأجزاء
    for idx, ch in enumerate(children, start=1):
        ch_title = display_title(ch.get("title") or f"الجزء {idx}", lang)
        rows.append([InlineKeyboardButton(f"📘 {ch_title}", callback_data=f"book:{ch['path']}")])
    rows.append([InlineKeyboardButton(f"⬅️ {t(user_id,'back')}", callback_data=f"cat:{cat_key}")])
    return InlineKeyboardMarkup(rows)

# ----------------------- Handlers -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_membership(update, context):
        return
    user_id = update.effective_user.id
    # لا تغيّر لغته إن كانت مضبوطة—فقط اعرض القائمة
    text = t(user_id, "home_title")
    await update.effective_chat.send_message(text, reply_markup=main_menu_kb(user_id))

async def lang_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, lang = q.data.split(":")
    set_lang(q.from_user.id, lang)
    await q.edit_message_text(
        t(q.from_user.id, "home_title"),
        reply_markup=main_menu_kb(q.from_user.id),
    )

async def to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        t(q.from_user.id, "home_title"),
        reply_markup=main_menu_kb(q.from_user.id),
    )

async def open_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_membership(update, context):
        return
    q = update.callback_query
    await q.answer()
    _, cat = q.data.split(":")
    kb = section_kb(q.from_user.id, cat)
    # عنوان القسم بحسب اللغة
    lang = get_lang(q.from_user.id)
    title_map = {
        "prog": {"ar": "البرمجة", "en": "Programming"},
        "design": {"ar": "التصميم", "en": "Design"},
        "security": {"ar": "الأمن", "en": "Security"},
        "languages": {"ar": "اللغات", "en": "Languages"},
        "marketing": {"ar": "التسويق", "en": "Marketing"},
        "maintenance": {"ar": "الصيانة", "en": "Maintenance"},
        "office": {"ar": "البرامج المكتبية", "en": "Office apps"},
    }
    head = f"{CAT_EMOJI.get(cat,'📁')} {display_title(title_map[cat], lang)}"
    await q.edit_message_text(head, reply_markup=kb)

async def open_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, cat, group_title_encoded = q.data.split(":", 2)
    # العثور على المجموعة
    group = None
    for item in CATALOG.get(cat, []):
        if "children" in item:
            # نقارن بالعنوان بالعربي أو بالإنجليزي
            title_ar = display_title(item["title"], "ar")
            title_en = display_title(item["title"], "en")
            if group_title_encoded in (title_ar, f"{title_en} (Series)", title_en, f"{title_ar} (سلسلة)"):
                group = item
                break
    if not group:
        await q.edit_message_reply_markup(reply_markup=back_kb(q.from_user.id, cat))
        return
    kb = series_kb(q.from_user.id, cat, display_title(group["title"], "ar"), group["children"])
    await q.edit_message_text(
        f"📚 {display_title(group['title'], get_lang(q.from_user.id))}",
        reply_markup=kb
    )

async def send_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_membership(update, context):
        return
    q = update.callback_query
    await q.answer()
    _, rel_path = q.data.split(":", 1)
    fs_path = ASSETS_DIR / Path(rel_path).relative_to("assets")
    if not fs_path.exists():
        log.warning("Missing file: %s", rel_path)
        await q.edit_message_text(
            t(q.from_user.id, "not_found", path=rel_path),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(q.from_user.id, None),
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
        log.error("Failed to send %s: %s", rel_path, e)
    # لا نغير الرسالة الأصلية؛ فقط نجيب الملف

async def reload_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if OWNER_USERNAME and update.effective_user.username != OWNER_USERNAME:
        return
    global CATALOG
    CATALOG = load_catalog()
    # إحصاء
    catalog_count = {k: len(v) for k, v in CATALOG.items()}
    await update.message.reply_text(f"✅ تم إعادة تحميل الكاتالوج:\nحالة المحتوى:\n{catalog_count}")

# ----------------------- Health server -----------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b".")

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

    app.add_handler(CallbackQueryHandler(lang_switch, pattern=r"^lang:(ar|en)$"))
    app.add_handler(CallbackQueryHandler(to_home, pattern=r"^home$"))
    app.add_handler(CallbackQueryHandler(open_category, pattern=r"^cat:(.+)$"))
    app.add_handler(CallbackQueryHandler(open_group, pattern=r"^group:.+"))
    app.add_handler(CallbackQueryHandler(send_book, pattern=r"^book:.+"))

    # شغّل healthz في ثريد منفصل
    import threading
    threading.Thread(target=run_health_server, daemon=True).start()

    log.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()




