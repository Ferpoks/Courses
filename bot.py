# -*- coding: utf-8 -*-
"""
Telegram Books Library Bot (PTB v21.x)
- Render-ready: aiohttp health server on $PORT with /healthz,/health,/
- Subscription gate قبل الاستخدام
- أقسام فرعية مع ترقيم صفحات، الضغط على عنصر يرسل PDF مباشرة (ملف محلي أو URL مباشر)
- زر تواصل مع الإدارة
"""

import os, json, math, asyncio, threading, logging
from pathlib import Path
from typing import List, Tuple, Union, Dict, Any

from aiohttp import web
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden

# ======== الإعدادات ========
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

REQUIRED_CHANNELS = [c.strip() for c in os.getenv("REQUIRED_CHANNELS", "@yourchannel").split(",") if c.strip()]
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Ferp0ks").lstrip("@")

ASSETS_DIR   = Path("assets")
CATALOG_FILE = ASSETS_DIR / "catalog.json"
PORT         = int(os.getenv("PORT", "10000"))

# مفاتيح الأقسام
SECTION_NAMES = {
    "prog":        "كتب البرمجة",
    "design":      "كتب التصميم",
    "security":    "كتب الأمن",
    "languages":   "كتب اللغات",
    "marketing":   "كتب التسويق",
    "maintenance": "كتب الصيانة",
    "office":      "كتب البرامج المكتبية",
}

PAGE_SIZE = 8

# ======== Logging ========
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("courses-bot")

# ======== الكاتالوج ========
def load_catalog() -> Dict[str, List[Dict[str, Any]]]:
    data: Dict[str, List[Dict[str, Any]]] = {k: [] for k in SECTION_NAMES}
    if CATALOG_FILE.exists():
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            try:
                raw = json.load(f)
                for k in SECTION_NAMES:
                    if isinstance(raw.get(k), list):
                        data[k] = raw[k]
            except Exception as e:
                log.exception("Invalid catalog.json: %s", e)
    return data

CATALOG = load_catalog()

# ======== أدوات ========
def normalize_chat_id(raw: str) -> Union[int, str]:
    s = (raw or "").strip()
    if not s: return s
    if s.startswith("-100") or s.lstrip("-").isdigit():
        try: return int(s)
        except Exception: return s
    return s if s.startswith("@") else f"@{s}"

def public_url_for(raw: str) -> str:
    return f"https://t.me/{(raw or '').lstrip('@')}"

def trim_title(t: str, limit: int = 28) -> str:
    t = t.strip()
    return t if len(t) <= limit else t[:limit-1] + "…"

def build_main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📘 كتب البرمجة",            callback_data="sec:prog")],
        [InlineKeyboardButton("🎨 كتب التصميم",            callback_data="sec:design")],
        [InlineKeyboardButton("🛡️ كتب الأمن",              callback_data="sec:security")],
        [InlineKeyboardButton("🗣️ كتب اللغات",             callback_data="sec:languages")],
        [InlineKeyboardButton("📈 كتب التسويق",            callback_data="sec:marketing")],
        [InlineKeyboardButton("🛠️ كتب الصيانة",            callback_data="sec:maintenance")],
        [InlineKeyboardButton("🗂️ كتب البرامج المكتبية",   callback_data="sec:office")],
        [
            InlineKeyboardButton("🛠 تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}"),
            InlineKeyboardButton("🔄 تحديث القائمة",   callback_data="menu"),
        ],
    ]
    return InlineKeyboardMarkup(rows)

def build_gate_keyboard(missing: List[str]) -> InlineKeyboardMarkup:
    buttons = []
    for ch in missing:
        s = str(ch)
        if not s.startswith("-100"):
            buttons.append([InlineKeyboardButton(f"📢 اشترك في {s.lstrip('@')}", url=public_url_for(s))])
    buttons.append([
        InlineKeyboardButton("✅ تحقّق الاشتراك", callback_data="verify"),
        InlineKeyboardButton("🛠 تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}")
    ])
    return InlineKeyboardMarkup(buttons)

async def safe_edit_text(msg, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            log.info("safe_edit_text: not modified, ignoring.")
        else:
            raise

# ======== الاشتراك ========
async def is_member_of(chat_raw: str, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = normalize_chat_id(chat_raw)
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        ok = member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
        log.info(f"[membership] raw={chat_raw} norm={chat_id} user={user_id} status={member.status} ok={ok}")
        return ok
    except (BadRequest, Forbidden) as e:
        log.warning(f"[membership] raw={chat_raw} norm={chat_id} user={user_id} error={e}")
        return False
    except Exception as e:
        log.error(f"[membership] unexpected raw={chat_raw} norm={chat_id} user={user_id}: {e}")
        return False

async def passes_gate(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> Tuple[bool, List[str]]:
    missing = []
    for ch in REQUIRED_CHANNELS:
        if not await is_member_of(ch, user_id, context):
            missing.append(ch if str(ch).startswith("@") or str(ch).startswith("-100") else f"@{ch}")
    return (len(missing) == 0), missing

# ======== قوائم القسم ========
def section_items(section: str) -> List[Dict[str, Any]]:
    return CATALOG.get(section, [])

def render_section_menu(section: str, page: int = 0) -> InlineKeyboardMarkup:
    items = section_items(section)
    start, end = page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE
    page_items = items[start:end]

    rows = []
    for idx, item in enumerate(page_items, start=start):
        label = f"📄 {trim_title(item.get('title','بدون عنوان'))}"
        rows.append([InlineKeyboardButton(label, callback_data=f"send:{section}:{idx}")])

    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE)) if items else 1
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"page:{section}:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"page:{section}:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="menu"),
        InlineKeyboardButton("🛠 تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}")
    ])
    return InlineKeyboardMarkup(rows)

# ======== الهاندلرز ========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.message or update.callback_query.message
    ok, missing = await passes_gate(user.id, context)
    if not ok:
        text = ("🔒 للوصول إلى المكتبة، يلزم الاشتراك أولاً في القنوات/المجموعات التالية:\n" +
                "\n".join([f"• {m}" for m in missing]) +
                "\n\n- اجعل البوت أدمن في القنوات.\n- القنوات الخاصة: استخدم آي دي بصيغة -100… في REQUIRED_CHANNELS.\n" +
                "بعد الاشتراك اضغط «✅ تحقّق الاشتراك».")
        await msg.reply_text(text, reply_markup=build_gate_keyboard(missing))
        return
    await msg.reply_text("📚 أهلاً بك! اختر قسمًا:", reply_markup=build_main_menu())

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "verify":
        ok, missing = await passes_gate(q.from_user.id, context)
        if not ok:
            text = ("❗️لا يزال هناك قنوات/مجموعات ناقصة:\n" +
                    "\n".join([f"• {m}" for m in missing]) +
                    "\n\n- تأكد أن البوت أدمن.\n- القنوات الخاصة: استخدم -100…\nثم اضغط «✅ تحقّق الاشتراك».")
            await safe_edit_text(q.message, text, reply_markup=build_gate_keyboard(missing))
            return
        await safe_edit_text(q.message, "✅ تم التحقق. اختر قسمًا:", reply_markup=build_main_menu())
        return

    if data == "menu":
        await safe_edit_text(q.message, "📚 القائمة الرئيسية:", reply_markup=build_main_menu())
        return

    if data.startswith("sec:"):
        section = data.split(":", 1)[1]
        title = SECTION_NAMES.get(section, "قسم")
        items = section_items(section)
        if not items:
            await q.message.reply_text(f"⚠️ لا يوجد عناصر في «{title}» حالياً.")
            return
        await safe_edit_text(q.message, f"📂 {title} — اختر كتابًا/دورة لإرسال PDF:", reply_markup=render_section_menu(section, 0))
        return

    if data.startswith("page:"):
        _, section, page_str = data.split(":")
        page = int(page_str)
        title = SECTION_NAMES.get(section, "قسم")
        await safe_edit_text(q.message, f"📂 {title} — اختر عنصرًا:", reply_markup=render_section_menu(section, page))
        return

    if data.startswith("send:"):
        _, section, idx_str = data.split(":")
        items = section_items(section)
        try:
            idx  = int(idx_str)
            item = items[idx]
        except Exception:
            await q.message.reply_text("⚠️ عنصر غير صالح.")
            return

        ok, missing = await passes_gate(q.from_user.id, context)
        if not ok:
            await q.message.reply_text("🔒 يجب الاشتراك أولاً.", reply_markup=build_gate_keyboard(missing))
            return

        title = item.get("title", "ملف")
        doc: Union[str, InputFile, None] = None

        if "path" in item:
            path = Path(item["path"])
            if not path.is_absolute():
                path = Path(item["path"]) if str(item["path"]).startswith("assets") else ASSETS_DIR / item["path"]
            if path.exists():
                doc = InputFile(path)
            else:
                await q.message.reply_text(f"🚫 لم أجد الملف في السيرفر: {path}")
                return
        elif "url" in item:
            doc = item["url"]  # رابط تنزيل مباشر
        else:
            await q.message.reply_text("⚠️ لا يوجد path أو url لهذا العنصر.")
            return

        caption = f"📘 {title}\n🛠 تواصل مع الإدارة: @{OWNER_USERNAME}"
        await q.message.reply_document(doc, caption=caption)
        return

    await q.message.reply_text("🤖 أمر غير معروف.")

# ======== تشغيل البوت (خيط جانبي) ========
def run_telegram_bot():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(on_button))

        log.info("🤖 Telegram bot starting (background thread)…")
        application.run_polling(stop_signals=None, close_loop=False)
    except Exception as e:
        log.exception("❌ Telegram thread crashed: %s", e)

# ======== Health/Web ========
async def health_handler(_request):
    return web.Response(text="OK")

def main():
    threading.Thread(target=run_telegram_bot, daemon=True).start()

    app = web.Application()
    for p in ["/healthz", "/healthz/", "/health", "/health/", "/"]:
        app.router.add_route("GET",  p, health_handler)
        app.router.add_route("HEAD", p, health_handler)

    log.info("🌐 Health server on 0.0.0.0:%s (paths: /healthz,/health,/)", PORT)
    web.run_app(app, host="0.0.0.0", port=PORT, handle_signals=False)

if __name__ == "__main__":
    main()
