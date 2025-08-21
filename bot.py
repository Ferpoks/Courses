# bot.py
import os
import json
import asyncio
import logging
from threading import Thread
from pathlib import Path

from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ========= إعدادات عامة =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("courses-bot")

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("❌ ضع TELEGRAM_TOKEN في متغيرات البيئة على Render")

# قناة التحقق من الاشتراك (يمكن تعديلها من الإعدادات)
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@ferpokss").strip()  # مثال: @ferpokss
ADMIN_USERNAME   = os.getenv("ADMIN_USERNAME", "@ferpo_ksa").strip()    # زر تواصل مع الإدارة

PORT = int(os.getenv("PORT", "10000"))
CATALOG_PATH = Path("assets/catalog.json")

# أسماء الأقسام بالعربية (للواجهة) مقابل المفاتيح داخل catalog.json
CATEGORIES = {
    "prog":      "كتب البرمجة",
    "design":    "كتب التصميم",
    "security":  "كتب الأمن",
    "languages": "كتب اللغات",
    "marketing": "كتب التسويق",
    "maintenance": "كتب الصيانة",
    "office":    "كتب البرامج المكتبية",
}

# ========= تحميل الكاتالوج =========
def load_catalog() -> dict:
    if not CATALOG_PATH.exists():
        log.warning("catalog.json غير موجود: %s", CATALOG_PATH)
        return {k: [] for k in CATEGORIES.keys()}
    with CATALOG_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)  # في حال وجود خطأ صياغة سيرفع استثناء
    # تأكد من وجود جميع المفاتيح
    for k in CATEGORIES.keys():
        raw.setdefault(k, [])
    return raw

CATALOG = load_catalog()

def human_counts() -> str:
    lines = []
    for key, title in CATEGORIES.items():
        count = 0
        for item in CATALOG.get(key, []):
            if "children" in item:
                count += len(item["children"])
            else:
                count += 1
        lines.append(f"- {title}: {count}")
    return "\n".join(lines)

# ========= أدوات مساعدة لـ Telegram =========
async def safe_answer_callback(q):
    """نرد على الزر فوراً حتى لا يظهر 'Query is too old'."""
    try:
        await q.answer()
    except BadRequest as e:
        if "query is too old" in str(e).lower():
            return
        raise

async def safe_edit_text(message, **kwargs):
    """نتجنب استثناء 'Message is not modified' عند تحرير نفس النص."""
    try:
        return await message.edit_text(**kwargs)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        raise

async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """تحقق الاشتراك في القناة المطلوبة."""
    if not REQUIRED_CHANNEL:
        return True
    chat = REQUIRED_CHANNEL
    if not chat.startswith("@") and not chat.startswith("-100"):
        chat = "@" + chat
    try:
        member = await context.bot.get_chat_member(chat_id=chat, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except BadRequest as e:
        # مثلاً: Chat not found إذا كانت القناة خاصة أو البوت ليس أدمن
        log.warning("[membership] chat=%s user=%s error=%s", chat, user_id, e)
        return False
    except Exception as e:
        log.warning("[membership] unexpected: %s", e)
        return False

def chunks(lst, n):
    """تقسيم الأزرار لصفوف."""
    for i in range(0, len(lst), n):
        yield lst[i : i+n]

def main_menu_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(CATEGORIES[k], callback_data=f"cat:{k}")
        for k in CATEGORIES.keys()
    ]
    rows = [list(r) for r in chunks(buttons, 2)]
    rows.append([
        InlineKeyboardButton("🔄 تحديث القائمة", callback_data="reload"),
        InlineKeyboardButton("🛠️ تواصل مع الإدارة", url=f"https://t.me/{ADMIN_USERNAME.removeprefix('@')}"),
    ])
    return InlineKeyboardMarkup(rows)

def list_category_kb(cat_key: str) -> InlineKeyboardMarkup:
    items = CATALOG.get(cat_key, [])
    btns = []
    for idx, item in enumerate(items):
        title = item.get("title", f"Item {idx+1}")
        if item.get("children"):
            btns.append(InlineKeyboardButton(f"📂 {title}", callback_data=f"group:{cat_key}:{idx}"))
        else:
            path = item.get("path", "")
            btns.append(InlineKeyboardButton(f"📄 {title}", callback_data=f"file:{path}"))
    rows = [list(r) for r in chunks(btns, 1)]
    rows.append([InlineKeyboardButton("⬅️ رجوع للقائمة", callback_data="back")])
    rows.append([InlineKeyboardButton("🛠️ تواصل مع الإدارة", url=f"https://t.me/{ADMIN_USERNAME.removeprefix('@')}")])
    return InlineKeyboardMarkup(rows)

def list_children_kb(cat_key: str, parent_idx: int) -> InlineKeyboardMarkup:
    parent = CATALOG.get(cat_key, [])[parent_idx]
    children = parent.get("children", [])
    btns = []
    for ch in children:
        title = ch.get("title", "ملف")
        path  = ch.get("path", "")
        btns.append(InlineKeyboardButton(f"📄 {title}", callback_data=f"file:{path}"))
    rows = [list(r) for r in chunks(btns, 1)]
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data=f"cat:{cat_key}")])
    return InlineKeyboardMarkup(rows)

# ========= الأوامر =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "مرحباً بك في مكتبة الكتب والدورات 📚\nاختر قسماً:",
        reply_markup=main_menu_kb(),
    )

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(f"ℹ️ حالة المحتوى:\n{human_counts()}")

async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    try:
        CATALOG = load_catalog()
        await update.effective_chat.send_message(
            "تم إعادة تحميل الكاتالوج ✅\n" + human_counts()
        )
    except Exception as e:
        log.exception("reload failed: %s", e)
        await update.effective_chat.send_message(f"فشل تحديث الكاتالوج ❌\n{e}")

# ========= أزرار الإنلاين =========
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)  # ← مهم جداً
    data = q.data or ""

    if data == "back":
        await safe_edit_text(q.message, text="اختر قسماً:", reply_markup=main_menu_kb())
        return

    if data == "reload":
        # حدّث ثم ارجع للقائمة
        try:
            global CATALOG
            CATALOG = load_catalog()
            await safe_edit_text(
                q.message,
                text="تم تحديث القائمة ✅\nاختر قسماً:",
                reply_markup=main_menu_kb(),
            )
        except Exception as e:
            await q.message.reply_text(f"فشل التحديث: {e}")
        return

    # فتح قسم
    if data.startswith("cat:"):
        cat_key = data.split(":", 1)[1]
        title = CATEGORIES.get(cat_key, "القسم")
        items = CATALOG.get(cat_key, [])
        if not items:
            await safe_edit_text(q.message, text=f"⚠️ لا يوجد عناصر في «{title}» حالياً.", reply_markup=main_menu_kb())
            return
        await safe_edit_text(q.message, text=f"{title} — اختر عنصراً:", reply_markup=list_category_kb(cat_key))
        return

    # مجموعة فرعية (أطفال)
    if data.startswith("group:"):
        _, cat_key, idx_s = data.split(":")
        idx = int(idx_s)
        parent = CATALOG.get(cat_key, [])[idx]
        title = parent.get("title", "مجموعة")
        await safe_edit_text(q.message, text=f"{CATEGORIES.get(cat_key, '')} / {title}:", reply_markup=list_children_kb(cat_key, idx))
        return

    # إرسال ملف
    if data.startswith("file:"):
        path = data.removeprefix("file:").strip()
        # تحقق اشتراك
        ok = await is_member(context, q.from_user.id)
        if not ok:
            url = f"https://t.me/{REQUIRED_CHANNEL.removeprefix('@')}"
            await q.message.reply_text(
                f"🚫 للوصول للملفات يلزم الانضمام إلى القناة:\n{REQUIRED_CHANNEL}\nثم أعد المحاولة.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 فتح القناة", url=url)]]),
            )
            return

        fs_path = Path(path)
        if not fs_path.exists():
            await q.message.reply_text(f"لم أجد الملف في السيرفر: {path} 🚫")
            return

        # إظهار حالة رفع
        await q.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
        # اسم الملف من آخر جزء في المسار
        display_name = fs_path.name
        try:
            await q.message.reply_document(
                document=InputFile(fs_path),
                caption="",  # بدون جُمل إضافية، بناءً على طلبك
                filename=display_name,
            )
        except (NetworkError, TimedOut):
            # إعادة محاولة واحدة
            await asyncio.sleep(1)
            await q.message.reply_document(
                document=InputFile(fs_path),
                caption="",
                filename=display_name,
            )
        return

# ========= Error Handler عام =========
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    text = str(err).lower() if err else ""
    # أخطاء نتجاهلها
    ignorable = (
        isinstance(err, BadRequest)
        and ("query is too old" in text or "not modified" in text)
    )
    if ignorable:
        log.warning("Ignored BadRequest: %s", err)
        return
    log.exception("Unhandled exception: %s", err)

# ========= خادم الصحّة لِـ Render =========
async def healthz(_request):
    return web.Response(text="ok")

def run_health_server():
    app = web.Application()
    app.router.add_get("/", healthz)
    app.router.add_get("/health", healthz)
    app.router.add_get("/healthz", healthz)
    # مهم: handle_signals=False لأننا نشغّله في Thread
    web.run_app(app, host="0.0.0.0", port=PORT, handle_signals=False)

# ========= التشغيل =========
def main():
    # شغّل خادم الصحّة في Thread منفصل
    Thread(target=run_health_server, daemon=True).start()
    log.info("🌐 Health server on 0.0.0.0:%s (paths: /healthz,/health,/)", PORT)

    # Telegram App
    application: Application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    # أوامر
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("check", cmd_check))
    application.add_handler(CommandHandler("reload", cmd_reload))
    application.add_handler(CallbackQueryHandler(on_button))

    # فلتر أي رسالة نصية لمساعدة المستخدم
    application.add_handler(MessageHandler(filters.COMMAND, cmd_start))

    # Error handler
    application.add_error_handler(on_error)

    log.info("🤖 Telegram bot starting…")
    application.run_polling(
        drop_pending_updates=True,   # ← يمنع التحديثات القديمة بعد الريستارت
        stop_signals=None,
        close_loop=False,
    )

if __name__ == "__main__":
    main()
