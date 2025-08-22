import os
import json
import logging
import asyncio
from pathlib import Path
from threading import Thread

from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)

# =========================
# إعدادات عامة + لوجز
# =========================
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("courses-bot")

ROOT = Path(__file__).parent
CATALOG_FILE = ROOT / "catalog.json"

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip().lstrip("@")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip()  # مثال: @your_channel
HEALTH_PORT = int(os.getenv("PORT", "10000"))  # Render يستخدم PORT إن وُجد

# =========================
# تحميل الكتالوج
# =========================
def load_catalog() -> dict:
    with open(CATALOG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


CATALOG = load_catalog()
log.info("📦 Catalog on start: %s", {k: len(v) for k, v in CATALOG.items()})

# =========================
# أدوات
# =========================
def count_items(node) -> int:
    """يحسب عدد الملفات (يشمل الأطفال)."""
    if isinstance(node, list):
        return sum(count_items(x) for x in node)
    if isinstance(node, dict):
        if "children" in node:
            return sum(count_items(c) for c in node["children"])
        return 1  # عنصر ورقي (ملف واحد)
    return 0


def nice_name(key: str) -> tuple[str, str]:
    """اسم عربي وإيموجي للقسم."""
    mapping = {
        "prog": ("كتب البرمجة", "💻"),
        "design": ("كتب التصميم", "🎨"),
        "security": ("كتب الأمن", "🛡️"),
        "languages": ("كتب اللغات", "🗣️"),
        "marketing": ("كتب التسويق", "📈"),
        "maintenance": ("كتب الصيانة", "🛠️"),
        "office": ("كتب البرامج المكتبية", "📂"),
    }
    return mapping.get(key, (key, "📚"))


def main_menu_markup() -> InlineKeyboardMarkup:
    rows = []
    for key in ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]:
        title, emoji = nice_name(key)
        total = count_items(CATALOG.get(key, []))
        btn = InlineKeyboardButton(f"{title} {emoji} · {total}", callback_data=f"cat:{key}")
        rows.append([btn])
    rows.append([InlineKeyboardButton("✉️ تواصل مع الإدارة", url="https://t.me/%s" % OWNER_USERNAME if OWNER_USERNAME else "https://t.me/")])
    return InlineKeyboardMarkup(rows)


def category_markup(cat_key: str) -> InlineKeyboardMarkup:
    items = CATALOG.get(cat_key, [])
    rows = []

    def add_item_button(title, payload):
        rows.append([InlineKeyboardButton(title, callback_data=payload)])

    for item in items:
        if "children" in item:
            # عنوان مجموعة فرعية
            title = f"📁 {item['title']}"
            add_item_button(title, f"grp:{cat_key}:{item['title']}")
        else:
            title = f"📄 {item['title']}"
            add_item_button(title, f"doc:{item['path']}")

    rows.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def group_markup(cat_key: str, group_title: str) -> InlineKeyboardMarkup:
    # ابحث عن المجموعة المطلوبة
    group = None
    for item in CATALOG.get(cat_key, []):
        if item.get("children") and item.get("title") == group_title:
            group = item
            break

    rows = []
    if group:
        for child in group["children"]:
            rows.append([
                InlineKeyboardButton(f"📄 {child['title']}", callback_data=f"doc:{child['path']}")
            ])

    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data=f"cat:{cat_key}")])
    return InlineKeyboardMarkup(rows)


async def ensure_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يتأكد من اشتراك المستخدم في القناة المطلوبة قبل التحميل (إن تم ضبطها)."""
    if not REQUIRED_CHANNEL:
        return True
    try:
        user_id = update.effective_user.id
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        status = member.status  # 'creator','administrator','member','restricted','left','kicked'
        ok = status in ("creator", "administrator", "member")
        if not ok:
            await update.effective_message.reply_text(
                f"🔒 للتحميل يجب الاشتراك أولاً في القناة {REQUIRED_CHANNEL} ثم أرسل /start",
            )
        return ok
    except Exception as e:
        log.warning("membership check failed: %s", e)
        # إن فشل الاستعلام نسمح مؤقتًا
        return True


# =========================
# Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = "مرحبًا بك في مكتبة الكورسات 📚"
    sub = "اختر القسم:"
    await update.message.reply_text(
        f"<b>{title}</b>\n{sub}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_markup(),
    )


async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # السماح فقط للمالك إن وُضع اسمه
    if OWNER_USERNAME and (update.effective_user.username or "").lower() != OWNER_USERNAME.lower():
        return
    global CATALOG
    CATALOG = load_catalog()
    counts = "\n".join(
        f"• {nice_name(k)[0]}: <b>{count_items(v)}</b>" for k, v in CATALOG.items()
    )
    await update.message.reply_text(
        f"تم إعادة تحميل الكتالوج ✅\nحالة المحتوى:\n{counts}",
        parse_mode=ParseMode.HTML,
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    # رجوع
    if data == "back":
        await query.edit_message_reply_markup(reply_markup=main_menu_markup())
        return

    # فتح قسم
    if data.startswith("cat:"):
        cat_key = data.split(":", 1)[1]
        title, emoji = nice_name(cat_key)
        await query.edit_message_text(
            text=f"كتب {title} {emoji} – اختر:",
            reply_markup=category_markup(cat_key),
        )
        return

    # فتح مجموعة فرعية داخل قسم
    if data.startswith("grp:"):
        _, cat_key, group_title = data.split(":", 2)
        await query.edit_message_text(
            text=f"📁 {group_title} – اختر:",
            reply_markup=group_markup(cat_key, group_title),
        )
        return

    # إرسال ملف
    if data.startswith("doc:"):
        path = data.split(":", 1)[1]
        if not await ensure_member(update, context):
            return

        try:
            file_path = ROOT / path
            if not file_path.exists():
                await query.message.reply_text(f"⚠️ لم أجد الملف في السيرفر:\n<code>{path}</code>", parse_mode=ParseMode.HTML)
                return

            await query.message.reply_document(
                document=FSInputFile(str(file_path)),
                caption=Path(path).name,
            )
        except Exception as e:
            log.exception("send file failed: %s", e)
            await query.message.reply_text("حدث خطأ أثناء الإرسال. جرّب لاحقًا.")
        return


# =========================
# Health server (Render)
# =========================
async def handle_health(_):
    return web.Response(text="ok")

def run_health_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/healthz", handle_health)
    web.run_app(app, host="0.0.0.0", port=HEALTH_PORT)

# =========================
# Main
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("ضع TELEGRAM_TOKEN في متغيرات البيئة على Render")

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reload", reload_cmd))
    application.add_handler(CallbackQueryHandler(on_button))

    # شغّل البوت في Thread، والخادم الصحي في الـ Main
    def _run_bot():
        log.info("🤖 Telegram bot starting…")
        application.run_polling(close_loop=False)

    Thread(target=_run_bot, daemon=True).start()
    log.info("🌐 Health server on 0.0.0.0:%s", HEALTH_PORT)
    run_health_server()


if __name__ == "__main__":
    main()
