import os
import json
import logging
from pathlib import Path
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("courses-bot")

ROOT = Path(__file__).parent.resolve()
PORT = int(os.getenv("PORT", "10000"))
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
OWNER = (os.getenv("OWNER_USERNAME") or "").lstrip("@").strip()
REQUIRED_CHANNEL = (os.getenv("REQUIRED_CHANNEL") or "").strip()

SECTION_TITLES = {
    "prog": "كتب البرمجة 💻",
    "design": "كتب التصميم 🎨",
    "security": "كتب الأمن 🛡️",
    "languages": "كتب اللغات 🌐",
    "marketing": "كتب التسويق 💼",
    "maintenance": "كتب الصيانة 🧰",
    "office": "كتب البرامج المكتبية 🗂️",
}
SECTION_ORDER = ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]


def find_catalog_path() -> Path:
    candidates = [ROOT / "assets" / "catalog.json", ROOT / "catalog.json"]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError("لم أجد catalog.json. ضع الملف في assets/catalog.json أو في الجذر.")


def load_catalog() -> dict:
    path = find_catalog_path()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # نظافة
    for k in list(data.keys()):
        if not isinstance(data[k], list):
            log.warning("Section %s is not a list; removing it.", k)
            data.pop(k, None)
    log.info("📘 Using catalog file: %s", path.relative_to(ROOT))
    return data


CATALOG = load_catalog()
log.info("📦 Catalog on start: %s", {k: len(v) for k, v in CATALOG.items()})


def count_items(section_key: str) -> int:
    total = 0
    for item in CATALOG.get(section_key, []):
        if isinstance(item, dict) and isinstance(item.get("children"), list):
            total += len(item["children"])
        else:
            total += 1
    return total


def main_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    for key in SECTION_ORDER:
        if key in CATALOG:
            title = SECTION_TITLES.get(key, key)
            total = count_items(key)
            rows.append([InlineKeyboardButton(f"{title} · {total}", callback_data=f"CAT|{key}")])
    if OWNER:
        rows.append([InlineKeyboardButton("✉️ تواصل مع الإدارة", url=f"https://t.me/{OWNER}")])
    return InlineKeyboardMarkup(rows)


def section_kb(section_key: str) -> InlineKeyboardMarkup:
    rows = []
    for idx, item in enumerate(CATALOG.get(section_key, [])):
        title = item.get("title", f"عنصر {idx+1}")
        if "children" in item:
            rows.append([InlineKeyboardButton(f"📁 {title}", callback_data=f"GRP|{section_key}|{idx}")])
        else:
            path = item.get("path", "")
            rows.append([InlineKeyboardButton(f"📄 {title}", callback_data=f"DOC|{path}")])
    rows.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="ROOT")])
    return InlineKeyboardMarkup(rows)


def group_kb(section_key: str, group_idx: int) -> InlineKeyboardMarkup:
    rows = []
    group = CATALOG.get(section_key, [])[group_idx]
    for cidx, child in enumerate(group.get("children", [])):
        ctitle = child.get("title", f"ملف {cidx+1}")
        cpath = child.get("path", "")
        rows.append([InlineKeyboardButton(f"📄 {ctitle}", callback_data=f"DOC|{cpath}")])
    rows.append([InlineKeyboardButton("↩️ رجوع للقسم", callback_data=f"CAT|{section_key}")])
    return InlineKeyboardMarkup(rows)


async def ensure_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        user_id = update.effective_user.id
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        ok = member.status in ("creator", "administrator", "member")
        if not ok:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"🔒 للتحميل اشترك أولًا في القناة: {REQUIRED_CHANNEL} ثم أرسل /start",
            )
        return ok
    except Exception as e:
        log.warning("membership check failed: %s", e)
        return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "مرحبًا بك في مكتبة الدورات والكتب 📚\nاختر القسم:",
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if OWNER and (update.effective_user.username or "").lower() != OWNER.lower():
        return
    global CATALOG
    CATALOG = load_catalog()
    summary = "\n".join(
        f"• {SECTION_TITLES.get(k, k)}: <b>{count_items(k)}</b>"
        for k in SECTION_ORDER if k in CATALOG
    )
    await update.effective_chat.send_message(
        f"تم إعادة تحميل الكتالوج ✅\n{summary}",
        parse_mode=ParseMode.HTML,
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "").strip()

    if data == "ROOT":
        await q.edit_message_text("اختر القسم:", reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)
        return

    if data.startswith("CAT|"):
        section_key = data.split("|", 1)[1]
        await q.edit_message_text(
            f"{SECTION_TITLES.get(section_key, section_key)} — اختر:",
            reply_markup=section_kb(section_key),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("GRP|"):
        _, sec, idxs = data.split("|")
        gidx = int(idxs)
        title = CATALOG.get(sec, [])[gidx].get("title", "مجموعة")
        await q.edit_message_text(
            f"📁 {title} — اختر:",
            reply_markup=group_kb(sec, gidx),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("DOC|"):
        path = data.split("|", 1)[1].strip()
        if not await ensure_member(update, context):
            return
        file_path = (ROOT / path).resolve()
        if not file_path.exists():
            await q.message.reply_text(
                f"⚠️ لم أجد الملف:\n<code>{path}</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            with open(file_path, "rb") as f:
                await q.message.reply_document(
                    document=InputFile(f, filename=file_path.name),
                    caption=None,
                )
        except Exception as e:
            log.exception("send file failed: %s", e)
            await q.message.reply_text("حدث خطأ أثناء الإرسال. جرّب لاحقًا.")
        return


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def run_health_server():
    srv = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info("🌐 Health server on 0.0.0.0:%s", PORT)
    srv.serve_forever()


def main():
    if not TOKEN:
        raise RuntimeError("❌ ضع TELEGRAM_TOKEN (أو BOT_TOKEN) في متغيرات البيئة على Render")
    Thread(target=run_health_server, daemon=True).start()
    # لا تستخدم .updater(None) — نتركه افتراضيًا كي يعمل run_polling()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CallbackQueryHandler(on_button))
    log.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()


