import os
import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional

from aiohttp import web

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,  # ← البديل الصحيح في PTB 20
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)

# ========= إعدادات عامة =========
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("courses-bot")

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "").lstrip("@").strip()
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "").strip()  # مثل: @mychannel

CATALOG_PATH = Path("assets/catalog.json")

# عناوين الأقسام + أيقونات لواجهة أجمل
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

CATALOG: Dict[str, Any] = {}


# ========= تحميل الكتالوج =========
def load_catalog() -> Dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # تنظيف بيانات بسيطة
    for k, v in list(data.items()):
        if not isinstance(v, list):
            log.warning("Section %s is not a list; skipping.", k)
            data.pop(k, None)
    return data


def count_summary(data: Dict[str, Any]) -> Dict[str, int]:
    out = {}
    for sec, items in data.items():
        total = 0
        for it in items:
            if "children" in it and isinstance(it["children"], list):
                total += len(it["children"])
            else:
                total += 1
        out[sec] = total
    return out


# ========= أزرار الواجهات =========
def main_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    for key in SECTION_ORDER:
        if key in CATALOG:
            rows.append([InlineKeyboardButton(SECTION_TITLES.get(key, key), callback_data=f"menu:{key}")])
    if OWNER_USERNAME:
        rows.append([InlineKeyboardButton("✉️ تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}")])
    return InlineKeyboardMarkup(rows)


def section_kb(section_key: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    items: List[Dict[str, Any]] = CATALOG.get(section_key, [])
    for idx, item in enumerate(items):
        title = item.get("title", f"Item {idx+1}")
        if "children" in item:
            rows.append([InlineKeyboardButton(f"{title} ▸", callback_data=f"submenu:{section_key}:{idx}")])
        else:
            path = item.get("path", "")
            rows.append([InlineKeyboardButton(title, callback_data=f"open:{path}")])

    rows.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="back:main")])
    return InlineKeyboardMarkup(rows)


def children_kb(section_key: str, item_idx: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    item = CATALOG.get(section_key, [])[item_idx]
    for cidx, child in enumerate(item.get("children", [])):
        ctitle = child.get("title", f"Child {cidx+1}")
        cpath = child.get("path", "")
        rows.append([InlineKeyboardButton(ctitle, callback_data=f"open:{cpath}")])
    rows.append([InlineKeyboardButton("↩️ رجوع للقسم", callback_data=f"back:section:{section_key}")])
    return InlineKeyboardMarkup(rows)


# ========= التحقق من الاشتراك =========
async def is_member(ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = await ctx.bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("membership check failed: %s", e)
        # عند فشل الاستعلام نسمح مؤقتاً
        return True


# ========= Handlers =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "مرحباً بك في مكتبة الدورات 📚\nاختر القسم:",
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    CATALOG = load_catalog()
    summary = count_summary(CATALOG)

    def fmt(sec, emoji):
        return f"{emoji} <b>{SECTION_TITLES.get(sec, sec)}</b>: <code>{summary.get(sec, 0)}</code>"

    text = (
        "تمت إعادة تحميل الكتالوج ✅\n"
        "حالة المحتوى:\n"
        f"{fmt('prog', '💻')}\n"
        f"{fmt('design', '🎨')}\n"
        f"{fmt('security', '🛡️')}\n"
        f"{fmt('languages', '🌐')}\n"
        f"{fmt('marketing', '💼')}\n"
        f"{fmt('maintenance', '🧰')}\n"
        f"{fmt('office', '🗂️')}"
    )
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML)


async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    data = q.data or ""
    chat_id = q.message.chat_id
    user_id = q.from_user.id

    if data.startswith("menu:"):
        sec = data.split(":", 1)[1]
        await q.edit_message_text(
            f"{SECTION_TITLES.get(sec, sec)} — اختر:",
            reply_markup=section_kb(sec),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("submenu:"):
        _, sec, idxs = data.split(":")
        idx = int(idxs)
        item_title = CATALOG.get(sec, [])[idx].get("title", "")
        await q.edit_message_text(
            f"{item_title} — اختر:",
            reply_markup=children_kb(sec, idx),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("back:"):
        parts = data.split(":")
        if len(parts) == 2 and parts[1] == "main":
            await q.edit_message_text("اختر القسم:", reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)
        elif len(parts) == 3 and parts[1] == "section":
            sec = parts[2]
            await q.edit_message_text(
                f"{SECTION_TITLES.get(sec, sec)} — اختر:",
                reply_markup=section_kb(sec),
                parse_mode=ParseMode.HTML,
            )
        return

    if data.startswith("open:"):
        path = data.split(":", 1)[1].strip()
        if not await is_member(context, user_id):
            await context.bot.send_message(
                chat_id,
                f"🔒 للتحميل يجب الاشتراك بالقناة أولاً: {REQUIRED_CHANNEL}",
            )
            return

        # إرسال الملف
        try:
            p = Path(path)
            if not p.is_file():
                raise FileNotFoundError(path)

            await context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(p),  # ← هنا التغيير
            )
        except FileNotFoundError:
            await context.bot.send_message(chat_id, f"لم أجد الملف في السيرفر: <code>{path}</code>", parse_mode=ParseMode.HTML)
        except Exception as e:
            log.exception("send file failed: %s", e)
            await context.bot.send_message(chat_id, "حدث خطأ أثناء الإرسال. جرّب لاحقاً.")
        return


# ========= Health server (Render) =========
async def health_handler(_request: web.Request):
    return web.Response(text="ok")

async def run_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/healthz", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    log.info("🌐 Health server on 0.0.0.0:10000 (paths: /healthz,/health,/)")


# ========= Main =========
def main():
    global CATALOG

    if not TOKEN:
        raise RuntimeError("ضع TELEGRAM_TOKEN في متغيرات البيئة على Render")

    CATALOG = load_catalog()
    log.info("📦 Catalog on start: %s", {k: count_summary(CATALOG).get(k, 0) for k in SECTION_ORDER})

    application = ApplicationBuilder().token(TOKEN).build()

    # أوامر
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("reload", cmd_reload))
    # أزرار
    application.add_handler(CallbackQueryHandler(cb_handler))

    # نشّط خادم الصحة بالخلفية
    loop = asyncio.get_event_loop()
    loop.create_task(run_health_server())

    log.info("🤖 Telegram bot starting…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
