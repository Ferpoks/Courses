import os, re, json, logging, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
# توافق FSInputFile / InputFile لكل النسخ
try:
    from telegram import FSInputFile as TG_FSInputFile
except Exception:
    TG_FSInputFile = None  # type: ignore
    try:
        from telegram import InputFile as TG_InputFile
    except Exception:
        TG_InputFile = None  # type: ignore

from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

# ---------- إعدادات ----------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("courses-bot")

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "").strip()   # مثال: @your_channel
OWNER_USERNAME   = os.environ.get("OWNER_USERNAME", "").strip()     # مثال: your_username

CATALOG_PATH = "assets/catalog.json"
HEALTH_HOST, HEALTH_PORT = "0.0.0.0", int(os.environ.get("PORT_HEALTH", "10000"))

SECTIONS = ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]
user_lang: Dict[int, str] = {}

T = {
    "ar": {
        "lang_prompt": "اختر اللغة:",
        "start_title": "مرحبًا بك في مكتبة الدورات 📚\nاختر القسم:",
        "back": "رجوع 🔙",
        "contact": "تواصل مع الإدارة ✉️",
        "not_member_title": "🔐 للوصول للمحتوى، اشترك في القناة ثم اضغط تحقّق:",
        "join": "الانضمام للقناة 📢",
        "recheck": "تحقّق ✅",
        "children_title": "اختر جزءًا:",
        "reloaded": "تم إعادة تحميل الكاتالوج ✅",
        "missing": "⚠️ لم أجد الملف في السيرفر:\n<code>{path}</code>",
        "unknown": "هذا الخيار غير معروف.",
        "choose_lang_button_ar": "🇸🇦 العربية",
        "choose_lang_button_en": "🇬🇧 English",
        "sections": {
            "prog": "البرمجة", "design": "التصميم", "security": "الأمن",
            "languages": "اللغات", "marketing": "التسويق",
            "maintenance": "الصيانة", "office": "البرامج المكتبية",
        },
        "contact_caption": f"@{OWNER_USERNAME}" if OWNER_USERNAME else "—",
    },
    "en": {
        "lang_prompt": "Choose language:",
        "start_title": "Welcome to the courses library 📚\nPick a category:",
        "back": "Back 🔙",
        "contact": "Contact admin ✉️",
        "not_member_title": "🔐 To access content, join the channel then tap Verify:",
        "join": "Join channel 📢",
        "recheck": "Verify ✅",
        "children_title": "Choose a part:",
        "reloaded": "Catalog reloaded ✅",
        "missing": "⚠️ File not found on server:\n<code>{path}</code>",
        "unknown": "Unknown option.",
        "choose_lang_button_ar": "🇸🇦 Arabic",
        "choose_lang_button_en": "🇬🇧 English",
        "sections": {
            "prog": "Programming", "design": "Design", "security": "Security",
            "languages": "Languages", "marketing": "Marketing",
            "maintenance": "Maintenance", "office": "Office apps",
        },
        "contact_caption": f"@{OWNER_USERNAME}" if OWNER_USERNAME else "—",
    },
}

SECTION_EMOJI = {
    "prog":"💻","design":"🎨","security":"🔐","languages":"🗣️",
    "marketing":"📈","maintenance":"🛠️","office":"🗂️"
}

def pick_emoji(title: str) -> str:
    t = (title or "").lower()
    pairs = [
        (["excel","اكسل"],"📊"), (["word","وورد"],"📝"), (["python","بايثون"],"🐍"),
        (["php"],"🐘"), (["mysql"],"🛢️"), (["linux","لينكس","kali"],"🐧"),
        (["web","ويب"],"🌐"), (["security","أمن","الهكر","اختراق"],"🛡️"),
        (["design","تصميم"],"🎨"), (["marketing","تسويق"],"📈"),
        (["maintenance","صيانة","mobile","موبايل"],"🛠️"),
        (["data","بيانات"],"📚"), (["guide","دليل"],"📘"),
    ]
    for keys, emo in pairs:
        if any(k in t for k in keys): return emo
    return "📄"

def make_input_file(path: str):
    if TG_FSInputFile is not None: return TG_FSInputFile(path)
    if TG_InputFile is not None:   return TG_InputFile(open(path, "rb"))
    return open(path, "rb")

def load_catalog() -> Dict[str, Any]:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    counts = {sec: len(data.get(sec, [])) for sec in SECTIONS if sec in data}
    log.info("📘 Using catalog file: %s", CATALOG_PATH)
    log.info("📦 Catalog on start: %s", counts)
    return data

CATALOG: Dict[str, Any] = {}

# ---------- تطبيع أسماء الملفات للبحث المرن ----------
def norm(s: Optional[str]) -> str:
    if not s: return ""
    # نحذف كل شيء غير حروف/أرقام بأي لغة + نلغي المسافات والرموز
    return re.sub(r"[\W_]+", "", s, flags=re.UNICODE).lower()

def resolve_best_file(section: str, path: Optional[str], title: Optional[str]) -> Optional[str]:
    """
    يرجع المسار الحقيقي إن وُجد، وإلا يبحث داخل:
      - مجلد المسار نفسه (إن كان محددًا)
      - مجلد القسم: assets/<section>
    يطابق بالأسماء بدون حساسية لرموز/مسافات وباللغتين.
    """
    # 1) لو المسار صالح مباشرة
    if path:
        p = path.strip()
        if p and p not in {".","./","/","assets","assets/"} and os.path.isfile(p):
            return p

    # 2) حضّر أهداف البحث
    dir_candidates: List[str] = []
    if path and "/" in path:
        parent = os.path.dirname(path)
        if parent and os.path.isdir(parent): dir_candidates.append(parent)
    sec_dir = os.path.join("assets", section)
    if os.path.isdir(sec_dir) and sec_dir not in dir_candidates:
        dir_candidates.append(sec_dir)

    if not dir_candidates:
        return None

    base = os.path.splitext(os.path.basename(path or ""))[0]
    nbase = norm(base)
    ntit  = norm(title or "")

    for d in dir_candidates:
        try:
            files = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
        except Exception:
            continue
        # تطابق تام مع الاسم (بعد التطبيع)
        for f in files:
            if norm(os.path.splitext(f)[0]) == nbase and nbase:
                return os.path.join(d, f)
        # تطابق يحتوي/يُحتوى
        for f in files:
            stem = norm(os.path.splitext(f)[0])
            if (nbase and (nbase in stem or stem in nbase)) or (ntit and (ntit == stem or ntit in stem or stem in ntit)):
                return os.path.join(d, f)

    return None

# ---------- اشتراك القناة ----------
async def ensure_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not REQUIRED_CHANNEL: return True
    user = update.effective_user
    if not user: return False
    try:
        m = await context.bot.get_chat_member(REQUIRED_CHANNEL, user.id)
        ok = m.status in ("member","administrator","creator")
        if not ok:
            lang = user_lang.get(user.id, "ar")
            await show_join_prompt(update, context, lang)
        return ok
    except Exception as e:
        log.warning("membership check failed: %s", e)
        return True  # ما نمنع بسبب خطأ مؤقت

async def show_join_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    txt = T[lang]["not_member_title"]
    btn_join  = InlineKeyboardButton(T[lang]["join"], url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")
    btn_check = InlineKeyboardButton(T[lang]["recheck"], callback_data="recheck")
    kb = InlineKeyboardMarkup([[btn_join],[btn_check]])
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=kb)
    else:
        await update.effective_message.reply_text(txt, reply_markup=kb)

# ---------- لوحات الأزرار ----------
def main_menu_kbd(lang: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for sec in SECTIONS:
        label = f"{SECTION_EMOJI.get(sec,'📁')} {T[lang]['sections'][sec]}"
        row.append(InlineKeyboardButton(label, callback_data=f"sec:{sec}"))
        if len(row) == 2:
            rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([
        InlineKeyboardButton(T[lang]["choose_lang_button_ar"], callback_data="lang:ar"),
        InlineKeyboardButton(T[lang]["choose_lang_button_en"], callback_data="lang:en"),
    ])
    if OWNER_USERNAME:
        rows.append([InlineKeyboardButton(T[lang]["contact"], url=f"https://t.me/{OWNER_USERNAME}")])
    return InlineKeyboardMarkup(rows)

def items_kbd(items: List[Dict[str, Any]], sec: str, lang: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for i, it in enumerate(items):
        title = it.get("title","—"); emo = pick_emoji(title)
        cb = f"children:{sec}:{i}" if "children" in it else f"file:{sec}:{i}"
        rows.append([InlineKeyboardButton(f"{emo} {title}", callback_data=cb)])
    rows.append([InlineKeyboardButton(T[lang]["back"], callback_data="back:root")])
    return InlineKeyboardMarkup(rows)

def children_kbd(children: List[Dict[str, Any]], sec: str, idx: int, lang: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for j, ch in enumerate(children):
        title = ch.get("title","—"); emo = pick_emoji(title)
        rows.append([InlineKeyboardButton(f"{emo} {title}", callback_data=f"cfile:{sec}:{idx}:{j}")])
    rows.append([InlineKeyboardButton(T[lang]["back"], callback_data=f"back:sec:{sec}")])
    return InlineKeyboardMarkup(rows)

# ---------- أوامر ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if u and u.id not in user_lang:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(T["ar"]["choose_lang_button_ar"], callback_data="lang:ar"),
            InlineKeyboardButton(T["en"]["choose_lang_button_en"], callback_data="lang:en"),
        ]])
        await update.message.reply_text(T["ar"]["lang_prompt"] + "\n" + T["en"]["lang_prompt"], reply_markup=kb)
        return
    lang = user_lang.get(u.id, "ar") if u else "ar"
    if not await ensure_member(update, context): return
    await update.message.reply_text(T[lang]["start_title"], reply_markup=main_menu_kbd(lang))

async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    lang = user_lang.get(u.id, "ar") if u else "ar"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(T[lang]["choose_lang_button_ar"], callback_data="lang:ar"),
        InlineKeyboardButton(T[lang]["choose_lang_button_en"], callback_data="lang:en"),
    ]])
    await update.message.reply_text(T[lang]["lang_prompt"], reply_markup=kb)

async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    CATALOG = load_catalog()
    u = update.effective_user
    lang = user_lang.get(u.id, "ar") if u else "ar"
    await update.message.reply_text(T[lang]["reloaded"])

async def cmd_where(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("usage: /where <section>"); return
    sec = context.args[0]
    arr = CATALOG.get(sec, [])
    lines = [f"• {x.get('title')} -> {x.get('path','children')}" for x in arr]
    await update.message.reply_text("\n".join(lines) or "empty")

# ---------- كول باك ----------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    u = update.effective_user; lang = user_lang.get(u.id, "ar") if u else "ar"
    data = q.data or ""

    if data.startswith("lang:"):
        _, pick = data.split(":", 1)
        user_lang[u.id] = pick; lang = pick
        await q.edit_message_text(T[lang]["start_title"], reply_markup=main_menu_kbd(lang)); return

    if data == "recheck":
        if await ensure_member(update, context):
            await q.edit_message_text(T[lang]["start_title"], reply_markup=main_menu_kbd(lang))
        return

    if not await ensure_member(update, context): return

    if data == "back:root":
        await q.edit_message_text(T[lang]["start_title"], reply_markup=main_menu_kbd(lang)); return
    if data.startswith("back:sec:"):
        _, _, sec = data.split(":", 2)
        items = CATALOG.get(sec, [])
        await q.edit_message_text(f"{SECTION_EMOJI.get(sec,'📁')} {T[lang]['sections'][sec]}",
                                  reply_markup=items_kbd(items, sec, lang))
        return

    if data.startswith("sec:"):
        _, sec = data.split(":", 1)
        items = CATALOG.get(sec, [])
        await q.edit_message_text(f"{SECTION_EMOJI.get(sec,'📁')} {T[lang]['sections'][sec]}",
                                  reply_markup=items_kbd(items, sec, lang))
        return

    if data.startswith("file:"):
        _, sec, idx_s = data.split(":", 2)
        items = CATALOG.get(sec, [])
        try: idx = int(idx_s)
        except ValueError:
            await q.message.reply_text(T[lang]["unknown"]); return
        it = items[idx] if 0 <= idx < len(items) else None
        if not it: await q.message.reply_text(T[lang]["unknown"]); return

        path  = it.get("path"); title = it.get("title","—")
        real  = resolve_best_file(sec, path, title)
        if not real:
            log.warning("Missing/invalid file: %r", path)
            await q.message.reply_text(T[lang]["missing"].format(path=str(path)), parse_mode=ParseMode.HTML)
            return
        await q.message.reply_document(document=make_input_file(real), caption=f"{pick_emoji(title)} {title}")
        return

    if data.startswith("children:"):
        _, sec, idx_s = data.split(":", 2)
        items = CATALOG.get(sec, [])
        try: idx = int(idx_s)
        except ValueError:
            await q.message.reply_text(T[lang]["unknown"]); return
        it = items[idx] if 0 <= idx < len(items) else None
        children = it.get("children", []) if it else []
        await q.edit_message_text(T[lang]["children_title"], reply_markup=children_kbd(children, sec, idx, lang))
        return

    if data.startswith("cfile:"):
        _, sec, pi_s, ci_s = data.split(":", 3)
        items = CATALOG.get(sec, [])
        try:
            pi = int(pi_s); ci = int(ci_s)
        except ValueError:
            await q.message.reply_text(T[lang]["unknown"]); return
        parent = items[pi] if 0 <= pi < len(items) else None
        child  = (parent.get("children") or [])[ci] if parent else None
        if not child: await q.message.reply_text(T[lang]["unknown"]); return

        path  = child.get("path"); title = child.get("title","—")
        real  = resolve_best_file(sec, path, title)
        if not real:
            log.warning("Missing/invalid file (child): %r", path)
            await q.message.reply_text(T[lang]["missing"].format(path=str(path)), parse_mode=ParseMode.HTML)
            return
        await q.message.reply_document(document=make_input_file(real), caption=f"{pick_emoji(title)} {title}")
        return

# ---------- Health ----------
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        else:
            self.send_response(404); self.end_headers()

def _run_health():
    srv = HTTPServer((HEALTH_HOST, HEALTH_PORT), _Health)
    log.info("🌐 Health server on %s:%s", HEALTH_HOST, HEALTH_PORT)
    srv.serve_forever()

# ---------- main ----------
def main():
    global CATALOG
    if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN is missing")
    CATALOG = load_catalog()
    threading.Thread(target=_run_health, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("lang",  cmd_lang))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CommandHandler("where",  cmd_where))
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()


