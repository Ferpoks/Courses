import os
import json
import logging
from pathlib import Path
from typing import Dict, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,  # متوافق عبر الإصدارات
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ======================= إعدادات اللوج =======================
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("courses-bot")

# =================== مسارات وبيئة التشغيل ===================
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip()
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip()  # إن أردت تفعيل الاشتراك الإلزامي

PREFERRED = Path("assets/catalog.json")
FALLBACK = Path("catalog.json")
CATALOG_PATH = str(PREFERRED if PREFERRED.exists() else FALLBACK)

# =================== Health Server (stdlib) =================
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

def start_health_server():
    server = HTTPServer(("0.0.0.0", 10000), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("🌐 Health server on 0.0.0.0:10000")

# ======================= تحميل الكاتالوج ======================
def load_catalog() -> Dict[str, List[Dict[str, str]]]:
    path = Path(CATALOG_PATH)
    logger.info("📘 Using catalog file: %s", path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # تأكد من الأنواع
    for k, v in list(data.items()):
        if not isinstance(v, list):
            logger.warning("Catalog key %s is not a list; skipping.", k)
            data.pop(k, None)

    # إزالة "البرمجة بلغة C" من الأمن إن وُجدت بالخطأ (موجودة في قسم البرمجة)
    if "security" in data:
        data["security"] = [
            item for item in data["security"]
            if not item.get("path", "").lower().endswith(("security_language_programming_c.pdf", "c_programming.pdf"))
        ]

    counts = {k: len(v) for k, v in data.items()}
    logger.info("📦 Catalog on start: %s", counts)
    return data

CATALOG = load_catalog()

SECTION_META = {
    "prog": ("📘 كتب البرمجة", "prog"),
    "design": ("🎨 كتب التصميم", "design"),
    "security": ("🛡️ كتب الأمن", "security"),
    "languages": ("🗣️ كتب اللغات", "languages"),
    "marketing": ("📈 كتب التسويق", "marketing"),
    "maintenance": ("🛠️ كتب الصيانة", "maintenance"),
    "office": ("📂 كتب البرامج المكتبية", "office"),
}

# ======================== واجهات المستخدم ====================
def build_main_menu() -> InlineKeyboardMarkup:
    rows = []
    for key in ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]:
        title, _ = SECTION_META[key]
        rows.append([InlineKeyboardButton(title, callback_data=f"sec:{key}")])

    if OWNER_USERNAME:
        rows.append([InlineKeyboardButton("✉️ تواصل مع الإدارة", url=f"https://t.me/{OWNER_USERNAME}")])

    return InlineKeyboardMarkup(rows)

def build_section_menu(section_key: str) -> InlineKeyboardMarkup:
    items = CATALOG.get(section_key, [])
    rows = []
    for item in items:
        title = item.get("title", "بدون عنوان")
        path = item.get("path", "")
        rows.append([InlineKeyboardButton(title, callback_data=f"dl:{path}")])
    rows.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="back:menu")])
    return InlineKeyboardMarkup(rows)

# ===================== أدوات مطابقة الأسماء ===================
def _normalize(s: str) -> str:
    """يحذف المسافات والشرطات وكل ما ليس حرف/رقم، ويحوّل لحروف صغيرة."""
    return "".join(ch for ch in s.lower() if ch.isalnum())

def _variants(stem: str) -> List[str]:
    """ينتج أشكال متعددة للاسم لتقبّل الشرطات/المسافات/الأندرلاين."""
    base = stem
    v = {
        base,
        base.replace("-", " ").replace("_", " "),
        base.replace(" ", "-").replace("_", "-"),
        base.replace(" ", "_").replace("-", "_"),
        base.replace("-", ""),
        base.replace("_", ""),
        base.replace(" ", ""),
    }
    # نسخة مُطبّعة
    v.add(_normalize(base))
    return list(v)

# ----------- محلّل مسار ذكي: يحاول إيجاد الملف بأي طريقة -----------
def resolve_file(path: str) -> Path | None:
    p = Path(path)
    candidates = [p]

    # جرّب مع وبدون assets/
    if path.startswith("assets/"):
        candidates.append(Path(path.replace("assets/", "")))
    else:
        candidates.append(Path("assets") / path)

    # تجربة مباشرة
    for c in candidates:
        if c.exists():
            return c

    # ابحث بالاسم (اسم الملف فقط) داخل المشروع (assets/ ثم الجذر) بحساسية أحرف متجاهلة
    name = Path(path).name
    name_lower = name.lower()

    search_roots = [Path("assets"), Path(".")]
    # 1) مطابقة الاسم الكامل (case-insensitive) مع أي امتداد PDF (.pdf/.PDF/..)
    for base in search_roots:
        for found in base.rglob("*"):
            if found.is_file():
                if found.suffix.lower() == ".pdf" and found.name.lower() == name_lower:
                    logger.info("🔎 Resolved by exact name (ci): %s -> %s", path, found)
                    return found

    # 2) مطابقة الـ stem (بدون الامتداد) مع تجاهل الفواصل وحالة الأحرف
    target_stem = Path(name).stem
    target_norm = _normalize(target_stem)
    target_variants = set(_variants(target_stem))

    best: Path | None = None
    for base in search_roots:
        for found in base.rglob("*"):
            if not found.is_file():
                continue
            if found.suffix.lower() != ".pdf":
                # لو الامتداد كبير (.PDF) أو مختلف، نتجاهل الشرط ونفحص يدويًا
                if found.suffix.upper() != ".PDF":
                    continue
            stem = found.stem
            if (
                _normalize(stem) == target_norm
                or stem.lower() == target_stem.lower()
                or stem in target_variants
                or _normalize(stem) in target_variants
            ):
                logger.info("🔎 Resolved by stem/normalize: %s -> %s", path, found)
                # لو فيه أكثر من واحد، خذ الأقرب داخل assets/
                if best is None:
                    best = found
                else:
                    if "assets" in str(found) and "assets" not in str(best):
                        best = found
    if best:
        return best

    return None

# ======================== إرسال الملفات ======================
async def send_book(chat_id: int, path: str, context: ContextTypes.DEFAULT_TYPE):
    fs_path = resolve_file(path)
    if not fs_path:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ لم أجد الملف في السيرفر:\n<code>{path}</code>",
            parse_mode="HTML",
        )
        logger.warning("Missing file: %s", path)
        return

    try:
        with fs_path.open("rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(f, filename=fs_path.name),
            )
    except Exception as e:
        logger.exception("Failed to send %s: %s", fs_path, e)
        await context.bot.send_message(chat_id=chat_id, text=f"حدث خطأ أثناء الإرسال: {e}")

# ========================== Handlers =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "مرحباً بك في مكتبة الكورسات 📚\nاختر القسم:",
        reply_markup=build_main_menu(),
    )

async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CATALOG
    try:
        CATALOG = load_catalog()
        counts = "\n".join([f"• {SECTION_META.get(k, (k,''))[0]}: {len(v)}" for k, v in CATALOG.items()])
        await update.effective_chat.send_message(f"تم إعادة تحميل الكاتالوج ✅\nحالة المحتوى:\n{counts}")
    except Exception as e:
        logger.exception("Reload failed: %s", e)
        await update.effective_chat.send_message(f"فشل التحديث: {e}")

async def where_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استعرض ملفات الـ PDF الموجودة فعليًا داخل السيرفر لقسم معيّن.
       مثال: /where maintenance
    """
    if not context.args:
        await update.effective_chat.send_message("استخدم: /where maintenance (أو office, prog, …)")
        return
    sec = context.args[0].strip().lower()
    if sec not in SECTION_META:
        await update.effective_chat.send_message("القسم غير معروف. الأقسام: prog, design, security, languages, marketing, maintenance, office")
        return

    folder = Path("assets") / sec
    if not folder.exists():
        await update.effective_chat.send_message(f"لا يوجد مجلد لهذا القسم: {folder}")
        return

    files = sorted([p.name for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"])
    if not files:
        await update.effective_chat.send_message("لا توجد ملفات PDF داخل هذا القسم على السيرفر.")
        return

    # قص القائمة إن كانت طويلة
    text = "الملفات الموجودة:\n" + "\n".join(f"- {name}" for name in files[:100])
    await update.effective_chat.send_message(text)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if data.startswith("sec:"):
        section = data.split(":", 1)[1]
        title = SECTION_META.get(section, ("القسم", ""))[0]
        await query.edit_message_text(title, reply_markup=build_section_menu(section))
        return

    if data.startswith("dl:"):
        path = data.split(":", 1)[1]
        await send_book(update.effective_chat.id, path, context)
        return

    if data == "back:menu":
        await query.edit_message_text("اختر القسم:", reply_markup=build_main_menu())
        return

# =========================== Main ============================
def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN غير موجود في Environment Variables.")

    start_health_server()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(CommandHandler("where", where_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))

    logger.info("🤖 Telegram bot starting…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()



