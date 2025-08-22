import os, json, logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# مسارات المشروع
ROOT = Path(__file__).parent.resolve()
CATALOG_PATH = ROOT / "assets" / "catalog.json"

# تحميل الكاتالوج من الملف (UTF-8)
def load_catalog():
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CATALOG = load_catalog()

# عناوين الأقسام بشكل أجمل
SECTION_TITLES = {
    "prog": "👨‍💻 كتب البرمجة",
    "design": "🎨 كتب التصميم",
    "security": "🛡️ كتب الأمن",
    "languages": "🗣️ كتب اللغات",
    "marketing": "📈 كتب التسويق",
    "maintenance": "🧰 الصيانة",
    "office": "📚 البرامج المكتبية",
}

def section_counts():
    counts = {}
    for k, items in CATALOG.items():
        total = 0
        for item in items:
            total += len(item.get("children", [])) if "children" in item else 1
        counts[k] = total
    return counts

# لوحة الأقسام الرئيسية
def root_keyboard():
    rows = []
    for key in ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]:
        title = SECTION_TITLES.get(key, key)
        rows.append([InlineKeyboardButton(title, callback_data=f"CAT|{key}|0")])
    return InlineKeyboardMarkup(rows)

# لوحة عناصر القسم (مع صفحات)
def category_keyboard(cat_key: str, page: int = 0, page_size: int = 8):
    items = CATALOG.get(cat_key, [])
    start = page * page_size
    slice_ = items[start : start + page_size]

    rows = []
    for idx, item in enumerate(slice_, start=start):
        if "children" in item:
            rows.append([InlineKeyboardButton(f"📁 {item['title']}", callback_data=f"GRP|{cat_key}|{idx}|0")])
        else:
            rows.append([InlineKeyboardButton(f"📄 {item['title']}", callback_data=f"ITEM|{cat_key}|{idx}")])

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("« الصفحة السابقة", callback_data=f"CAT|{cat_key}|{page-1}"))
    if start + page_size < len(items):
        nav.append(InlineKeyboardButton("الصفحة التالية »", callback_data=f"CAT|{cat_key}|{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="BACK|ROOT")])
    return InlineKeyboardMarkup(rows)

# لوحة عناصر مجموعة داخل القسم
def group_keyboard(cat_key: str, group_idx: int, page: int = 0, page_size: int = 8):
    group = CATALOG[cat_key][group_idx]
    children = group["children"]
    start = page * page_size
    slice_ = children[start : start + page_size]

    rows = []
    for idx, child in enumerate(slice_, start=start):
        rows.append([InlineKeyboardButton(f"📄 {child['title']}", callback_data=f"CHILD|{cat_key}|{group_idx}|{idx}")])

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("« الصفحة السابقة", callback_data=f"GRP|{cat_key}|{group_idx}|{page-1}"))
    if start + page_size < len(children):
        nav.append(InlineKeyboardButton("الصفحة التالية »", callback_data=f"GRP|{cat_key}|{group_idx}|{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 رجوع للأقسام", callback_data=f"CAT|{cat_key}|0")])
    return InlineKeyboardMarkup(rows)

# إرسال ملف
async def send_file(chat_id: int, context: ContextTypes.DEFAULT_TYPE, title: str, rel_path: str):
    abs_path = (ROOT / rel_path).resolve()
    if not abs_path.exists():
        await context.bot.send_message(
            chat_id,
            f"لم أجد الملف في السيرفر:\n<code>{rel_path}</code>",
            parse_mode="HTML",
        )
        return
    try:
        with open(abs_path, "rb") as f:
            # لا نستخدم FSInputFile لتفادي تعارض النسخ — فتح مباشر يعمل مع v13 و v20
            await context.bot.send_document(chat_id, document=f, filename=abs_path.name, caption=title)
    except Exception as e:
        await context.bot.send_message(chat_id, f"حدث خطأ أثناء الإرسال: {e}")

# الأوامر
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(update.effective_chat.id, "اختر القسم:", reply_markup=root_keyboard())

async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    CATALOG = load_catalog()
    c = section_counts()
    lines = [f"• {SECTION_TITLES.get(k,k)}: {v}" for k, v in c.items()]
    await context.bot.send_message(update.effective_chat.id, "تم إعادة تحميل الكاتالوج ✅\n" + "\n".join(lines))

# أزرار القوائم
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("|")
    kind = parts[0]

    if kind == "BACK" and parts[1] == "ROOT":
        await q.edit_message_text("اختر القسم:", reply_markup=root_keyboard())
        return

    if kind == "CAT":
        cat, page = parts[1], int(parts[2])
        await q.edit_message_text(SECTION_TITLES.get(cat, cat) + " — اختر:", reply_markup=category_keyboard(cat, page))
        return

    if kind == "GRP":
        cat, gidx, page = parts[1], int(parts[2]), int(parts[3])
        title = CATALOG[cat][gidx]["title"]
        await q.edit_message_text(f"{title} — اختر:", reply_markup=group_keyboard(cat, gidx, page))
        return

    if kind in ("ITEM", "CHILD"):
        if kind == "ITEM":
            cat, idx = parts[1], int(parts[2])
            item = CATALOG[cat][idx]
        else:
            cat, gidx, cidx = parts[1], int(parts[2]), int(parts[3])
            item = CATALOG[cat][gidx]["children"][cidx]
        await send_file(q.message.chat_id, context, item["title"], item["path"])
        return

# خادم بسيط للصحة لإرضاء Render
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    srv = HTTPServer(("0.0.0.0", port), HealthHandler)
    srv.serve_forever()

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("❌ ضع TELEGRAM_TOKEN (أو BOT_TOKEN) في متغيرات البيئة على Render")

    # شغّل سيرفر الصحة بخيط منفصل
    import threading
    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

