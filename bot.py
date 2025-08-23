import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    FSInputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ========= إعدادات عامة =========
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("courses-bot")

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "").strip()  # مثال: @your_channel
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "").strip()

CATALOG_PATH = "assets/catalog.json"
ASSETS_ROOT = "assets"
HEALTH_HOST = "0.0.0.0"
HEALTH_PORT = int(os.environ.get("PORT_HEALTH", "10000"))

# لغة المستخدم في الذاكرة (مؤقت — تُفقد عند إعادة التشغيل)
user_lang: Dict[int, str] = {}  # user_id -> 'ar' | 'en'

# الكاتالوج بالذاكرة
CATALOG: Dict[str, Any] = {}

# أسماء الأقسام المتاحة (مفاتيح JSON)
SECTIONS = ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]

# ترجمة واجهة الاستخدام
T = {
    "ar": {
        "lang_prompt": "اختر اللغة:",
        "start_title": "مرحبًا بك في مكتبة الدورات 📚\nاختر القسم:",
        "back": "رجوع للقائمة 🔙",
        "contact": "تواصل مع الإدارة ✉️",
        "not_member_title": "🔐 للوصول للمحتوى، اشترك في القناة ثم اضغط تحقّق:",
        "join": "الانضمام للقناة 📢",
        "recheck": "تحقّق ✅",
        "missing": "⚠️ لم أجد الملف في السيرفر:\n<code>{path}</code>",
        "sent": "تم الإرسال ✅",
        "reloaded": "تم إعادة تحميل الكاتالوج ✅",
        "unknown": "هذا الأمر غير معروف.",
        "choose_lang_button_ar": "🇸🇦 العربية",
        "choose_lang_button_en": "🇬🇧 English",
        "sections": {
            "prog": "البرمجة",
            "design": "التصميم",
            "security": "الأمن",
            "languages": "اللغات",
            "marketing": "التسويق",
            "maintenance": "الصيانة",
            "office": "البرامج المكتبية",
        },
        "children_title": "اختر جزءًا:",
        "contact_caption": f"للتواصل: @{OWNER_USERNAME}" if OWNER_USERNAME else "—",
    },
    "en": {
        "lang_prompt": "Choose language:",
        "start_title": "Welcome to the courses library 📚\nPick a category:",
        "back": "Back 🔙",
        "contact": "Contact admin ✉️",
        "not_member_title": "🔐 To access content, please join the channel then tap Verify:",
        "join": "Join the channel 📢",
        "recheck": "Verify ✅",
        "missing": "⚠️ File not found on server:\n<code>{path}</code>",
        "sent": "Sent ✅",
        "reloaded": "Catalog reloaded ✅",
        "unknown": "Unknown command.",
        "choose_lang_button_ar": "🇸🇦 Arabic",
        "choose_lang_button_en": "🇬🇧 English",
        "sections": {
            "prog": "Programming",
            "design": "Design",
            "security": "Security",
            "languages": "Languages",
            "marketing": "Marketing",
            "maintenance": "Maintenance",
            "office": "Office apps",
        },
        "children_title": "Choose a part:",
        "contact_caption": f"Contact: @{OWNER_USERNAME}" if OWNER_USERNAME else "—",
    },
}

# إيموجي للأقسام
SECTION_EMOJI = {
    "prog": "💻",
    "design": "🎨",
    "security": "🔐",
    "languages": "🗣️",
    "marketing": "📈",
    "maintenance": "🛠️",
    "office": "🗂️",
}

# اختيار إيموجي لكل عنصر حسب العنوان
def pick_emoji(title: str) -> str:
    t = title.lower()
    pairs = [
        (["excel", "اكسل"], "📊"),
        (["word", "وورد"], "📝"),
        (["python", "بايثون"], "🐍"),
        (["javascript", "جافاسكربت"], "🟨"),
        (["php"], "🐘"),
        (["mysql"], "🛢️"),
        (["linux", "لينكس", "kali"], "🐧"),
        (["web", "ويب"], "🌐"),
        (["unity"], "🎮"),
        (["security", "أمن", "الهكر", "اختراق"], "🛡️"),
        (["design", "تصميم"], "🎨"),
        (["marketing", "تسويق"], "📈"),
        (["maintenance", "صيانة", "mobile"], "🛠️"),
        (["data", "بيانات"], "📚"),
        (["guide", "دليل"], "📘"),
    ]
    for keys, emo in pairs:
        if any(k in t for k in keys):
            return emo
    return "📄"


# ========= أدوات مساعدة =========
def load_catalog() -> Dict[str, Any]:
    path = CATALOG_PATH if os.path.exists(CATALOG_PATH) else os.path.join(ASSETS_ROOT, "catalog.json")
    used = path
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # عدّ العناصر للّوغ
    counts = {sec: len(data.get(sec, [])) for sec in SECTIONS if sec in data}
    log.info("📘 Using catalog file: %s", used)
    log.info("📦 Catalog on start: %s", counts)
    return data


async def ensure_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يرجع True إذا كان المستخدم مشتركًا في القناة (أو لم يتم ضبط القناة)."""
    if not REQUIRED_CHANNEL:
        return True

    user = update.effective_user
    if not user:
        return False

    try:
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user.id)
        status = member.status  # 'member', 'administrator', 'creator', 'left', 'kicked'
        allowed = status in ("member", "administrator", "creator")
        if not allowed:
            lang = user_lang.get(user.id, "ar")
            await show_join_prompt(update, context, lang)
        return allowed
    except Exception as e:
        log.warning("membership check failed: %s", e)
        # في حال فشل الاستعلام نسمح مؤقتًا
        return True


async def show_join_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    txt = T[lang]["not_member_title"]
    join_btn = InlineKeyboardButton(T[lang]["join"], url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")
    recheck_btn = InlineKeyboardButton(T[lang]["recheck"], callback_data="recheck_membership")
    keyboard = InlineKeyboardMarkup([[join_btn], [recheck_btn]])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(txt, reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(txt, reply_markup=keyboard)


def main_menu_kbd(lang: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for sec in SECTIONS:
        label = f"{SECTION_EMOJI.get(sec,'📁')} {T[lang]['sections'][sec]}"
        row.append(InlineKeyboardButton(label, callback_data=f"sec:{sec}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    contact_btn = InlineKeyboardButton(T[lang]["contact"], url=f"https://t.me/{OWNER_USERNAME}") if OWNER_USERNAME else None
    lang_btns = [
        InlineKeyboardButton(T[lang]["choose_lang_button_ar"], callback_data="lang:ar"),
        InlineKeyboardButton(T[lang]["choose_lang_button_en"], callback_data="lang:en"),
    ]
    if contact_btn:
        rows.append([contact_btn])
    rows.append(lang_btns)
    return InlineKeyboardMarkup(rows)


def items_kbd(items: List[Dict[str, Any]], sec: str, lang: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for item in items:
        title = item.get("title", "—")
        emo = pick_emoji(title)
        if "children" in item:
            cb = f"child:{sec}:{title}"
        else:
            cb = f"file:{sec}:{title}"
        rows.append([InlineKeyboardButton(f"{emo} {title}", callback_data=cb)])

    rows.append([InlineKeyboardButton(T[lang]["back"], callback_data="back:root")])
    return InlineKeyboardMarkup(rows)


def children_kbd(children: List[Dict[str, Any]], sec: str, parent_title: str, lang: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for ch in children:
        title = ch.get("title", "—")
        emo = pick_emoji(title)
        cb = f"file_child:{sec}:{parent_title}:{title}"
        rows.append([InlineKeyboardButton(f"{emo} {title}", callback_data=cb)])
    rows.append([InlineKeyboardButton(T[lang]["back"], callback_data=f"back:sec:{sec}")])
    return InlineKeyboardMarkup(rows)


def find_item_by_title(items: List[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
    for it in items:
        if it.get("title") == title:
            return it
    return None


def fs_exists(path: str) -> bool:
    return os.path.isfile(path)


# ========= Handlers =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # شاشة اختيار اللغة أول مرة
    if user and user.id not in user_lang:
        k = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(T["ar"]["choose_lang_button_ar"], callback_data="lang:ar"),
                InlineKeyboardButton(T["en"]["choose_lang_button_en"], callback_data="lang:en"),
            ]
        ])
        await update.message.reply_text(T["ar"]["lang_prompt"] + "\n" + T["en"]["lang_prompt"], reply_markup=k)
        return

    lang = user_lang.get(user.id, "ar") if user else "ar"

    if not await ensure_member(update, context):
        return

    await update.message.reply_text(T[lang]["start_title"], reply_markup=main_menu_kbd(lang))


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = user_lang.get(user.id, "ar") if user else "ar"
    k = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(T[lang]["choose_lang_button_ar"], callback_data="lang:ar"),
            InlineKeyboardButton(T[lang]["choose_lang_button_en"], callback_data="lang:en"),
        ]
    ])
    await update.message.reply_text(T[lang]["lang_prompt"], reply_markup=k)


async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    if update.effective_user and OWNER_USERNAME:
        # إن أردت قصره على المالك فقط، يمكن فحص username هنا
        pass
    CATALOG = load_catalog()
    lang = user_lang.get(update.effective_user.id, "ar") if update.effective_user else "ar"
    await update.message.reply_text(T[lang]["reloaded"])


async def cmd_where(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug: /where maintenance"""
    if not context.args:
        await update.message.reply_text("usage: /where <section>")
        return
    sec = context.args[0]
    lst = CATALOG.get(sec, [])
    lines = [f"• {it.get('title')} -> {it.get('path', 'children')}" for it in lst]
    await update.message.reply_text("\n".join(lines) or "empty")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    lang = user_lang.get(user.id, "ar") if user else "ar"

    data = q.data or ""

    # اختيار اللغة
    if data.startswith("lang:"):
        _, pick = data.split(":", 1)
        user_lang[user.id] = pick
        lang = pick
        await q.edit_message_text(T[lang]["start_title"], reply_markup=main_menu_kbd(lang))
        return

    # تحقّق الاشتراك
    if data == "recheck_membership":
        if await ensure_member(update, context):
            await q.edit_message_text(T[lang]["start_title"], reply_markup=main_menu_kbd(lang))
        return

    # حماية الاشتراك لكل تفاعل
    if not await ensure_member(update, context):
        return

    # رجوع
    if data == "back:root":
        await q.edit_message_text(T[lang]["start_title"], reply_markup=main_menu_kbd(lang))
        return

    if data.startswith("back:sec:"):
        _, _, sec = data.split(":", 2)
        items = CATALOG.get(sec, [])
        await q.edit_message_text(
            f"{SECTION_EMOJI.get(sec,'📁')} {T[lang]['sections'][sec]}",
            reply_markup=items_kbd(items, sec, lang),
        )
        return

    # فتح قسم
    if data.startswith("sec:"):
        _, sec = data.split(":", 1)
        items = CATALOG.get(sec, [])
        await q.edit_message_text(
            f"{SECTION_EMOJI.get(sec,'📁')} {T[lang]['sections'][sec]}",
            reply_markup=items_kbd(items, sec, lang),
        )
        return

    # عنصر بورقة واحدة
    if data.startswith("file:"):
        _, sec, title = data.split(":", 2)
        items = CATALOG.get(sec, [])
        it = find_item_by_title(items, title)
        if not it:
            await q.message.reply_text(T[lang]["unknown"])
            return
        path = it.get("path")
        if not path:
            # لو كان له children نفتحها
            children = it.get("children", [])
            kb = children_kbd(children, sec, it.get("title", ""), lang)
            await q.edit_message_text(T[lang]["children_title"], reply_markup=kb)
            return
        if not fs_exists(path):
            log.warning("Missing file: %s", path)
            await q.message.reply_text(T[lang]["missing"].format(path=path), parse_mode=ParseMode.HTML)
            return
        await q.message.reply_document(document=FSInputFile(path), caption=f"{pick_emoji(title)} {title}")
        return

    # عنصر بـ children (جزء 1..4)
    if data.startswith("child:"):
        _, sec, parent_title = data.split(":", 2)
        items = CATALOG.get(sec, [])
        parent = find_item_by_title(items, parent_title)
        if not parent:
            await q.message.reply_text(T[lang]["unknown"])
            return
        children = parent.get("children", [])
        kb = children_kbd(children, sec, parent_title, lang)
        await q.edit_message_text(T[lang]["children_title"], reply_markup=kb)
        return

    # إرسال ملف من children
    if data.startswith("file_child:"):
        _, sec, parent_title, child_title = data.split(":", 3)
        items = CATALOG.get(sec, [])
        parent = find_item_by_title(items, parent_title)
        if not parent:
            await q.message.reply_text(T[lang]["unknown"])
            return
        ch = find_item_by_title(parent.get("children", []), child_title)
        if not ch:
            await q.message.reply_text(T[lang]["unknown"])
            return
        path = ch.get("path")
        if not path or not fs_exists(path):
            log.warning("Missing file: %s", path)
            await q.message.reply_text(T[lang]["missing"].format(path=path), parse_mode=ParseMode.HTML)
            return
        await q.message.reply_document(document=FSInputFile(path), caption=f"{pick_emoji(child_title)} {child_title}")
        return


# ========= Health server بسيط =========
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

def _run_health():
    srv = HTTPServer((HEALTH_HOST, HEALTH_PORT), _Health)
    log.info("🌐 Health server on %s:%s", HEALTH_HOST, HEALTH_PORT)
    srv.serve_forever()


# ========= Main =========
def main():
    global CATALOG
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN env var is missing")

    CATALOG = load_catalog()

    # شغّل healthz
    threading.Thread(target=_run_health, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CommandHandler("where", cmd_where))
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

