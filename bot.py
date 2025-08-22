# -*- coding: utf-8 -*-
"""
Courses Bot – Telegram
- قوائم محسّنة مع أيقونات وتخطيط شبكي
- يتحمّل أسماء ملفات عربية/إنجليزية ويبحث بمرونة
- يضيف "شرح الإكسل خطوة بخطوة" لقسم office
- يحذف "البرمجة بلغة C" من قسم security
- يدعم /reload و /start
- خادم صحة على /healthz لـ Render
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================
# إعدادات عامة
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("courses-bot")

REPO_ROOT = Path(__file__).parent
ASSETS_DIR = REPO_ROOT / "assets"
CATALOG_FILE = ASSETS_DIR / "catalog.json"

# استثناء: لا نريد هذا الملف في قسم الأمن إن وُجد
EXCLUDE_SECURITY_PATHS = {
    "assets/security/security_language_programming_c.pdf",
}

# أسماء محتملة لملف "شرح الإكسل خطوة بخطوة"
EXCEL_STEP_CANDIDATES = [
    "assets/office/excel_step_by_step.pdf",
    "assets/office/شرح_الإكسل_خطوة_بخطوة.pdf",
    "assets/office/شرح الإكسل خطوة بخطوة.pdf",
    "assets/office/الشرح الكامل خطوة بخطوة.pdf",
]

# عناوين الأقسام مع الأيقونات
SECTION_LABELS = {
    "prog":       "كتب البرمجة 💻",
    "design":     "كتب التصميم 🎨",
    "security":   "كتب الأمن 🛡️",
    "languages":  "كتب اللغات 🌐",
    "marketing":  "كتب التسويق 📈",
    "maintenance":"كتب الصيانة 🔧",
    "office":     "كتب البرامج المكتبية 🗂️",
}

# ذاكرة الكتالوج داخل الذاكرة
CATALOG: Dict[str, List[Dict[str, Any]]] = {}


# =========================
# أدوات مساعدة
# =========================

def slugify(s: str) -> str:
    """تحويل الاسم إلى سلاگ بسيط (ينفع للمقارنة وحتى مع العربية)."""
    # نحذف كل شيء غير حرف/رقم (من كل اليونيكود)
    s = re.sub(r"\s+", "", s, flags=re.UNICODE)
    s = re.sub(r"[^\w\u0600-\u06FF]", "", s, flags=re.UNICODE)  # أبقي العربية والحروف/الأرقام
    return s.casefold()


def resolve_path(loose_path: str) -> Optional[Path]:
    """
    يحاول إيجاد الملف على القرص حتى لو اختلفت حالة الأحرف أو وجود شرطات/مسافات
    أو اختلافات طفيفة في الاسم (عربي/إنجليزي).
    """
    p = REPO_ROOT / loose_path
    if p.exists():
        return p

    folder = (REPO_ROOT / loose_path).parent
    name = (REPO_ROOT / loose_path).name

    # تطابق حساس جزئيًا بطريقة الـ slug
    try:
        candidates = list(folder.iterdir())
    except FileNotFoundError:
        return None

    target_slug = slugify(name)
    for f in candidates:
        if slugify(f.name) == target_slug:
            return f

    for f in candidates:
        if slugify(f.stem) == slugify(Path(name).stem):
            return f

    # تطابق case-insensitive
    for f in candidates:
        if f.name.casefold() == name.casefold():
            return f

    # تطابق جزئي: الهدف ضمن اسم الملف
    for f in candidates:
        if target_slug and target_slug in slugify(f.name):
            return f

    return None


def pretty_grid(buttons: List[InlineKeyboardButton], cols: int = 2) -> List[List[InlineKeyboardButton]]:
    """ترتيب الأزرار على شبكة بعدد أعمدة ثابت."""
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for b in buttons:
        row.append(b)
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


# =========================
# تحميل/تكوين الكتالوج
# =========================

def _auto_scan_assets() -> Dict[str, List[Dict[str, Any]]]:
    """مسح تلقائي لمجلد assets لتكوين كتالوج بسيط عند فشل القراءة."""
    catalog: Dict[str, List[Dict[str, Any]]] = {}
    for section_dir in ASSETS_DIR.iterdir():
        if not section_dir.is_dir():
            continue
        section = section_dir.name  # prog/design/...
        items: List[Dict[str, Any]] = []
        for f in section_dir.rglob("*.pdf"):
            rel = f.relative_to(REPO_ROOT).as_posix()
            items.append({
                "title": f.stem.replace("_", " "),
                "path": rel,
            })
        if items:
            catalog[section] = items
    return catalog


def _ensure_excel_step_item(catalog: Dict[str, List[Dict[str, Any]]]) -> None:
    """إضافة عنصر 'شرح الإكسل خطوة بخطوة' لقسم office إن لم يكن موجودًا وكان الملف موجودًا."""
    office = catalog.setdefault("office", [])
    have = any(slugify(i.get("title", "")) in (slugify("شرح الإكسل خطوة بخطوة"), slugify("excel step by step"))
               or any(slugify(i.get("path", "")) == slugify(Path(c).as_posix()) for c in EXCEL_STEP_CANDIDATES)
               for i in office)

    if not have:
        # ابحث عن أول اسم موجود فعليًا على القرص من المرشحين:
        for candidate in EXCEL_STEP_CANDIDATES:
            rp = resolve_path(candidate)
            if rp:
                office.append({
                    "title": "شرح الإكسل خطوة بخطوة",
                    "path": rp.relative_to(REPO_ROOT).as_posix(),
                })
                break


def _filter_security_items(catalog: Dict[str, List[Dict[str, Any]]]) -> None:
    """حذف 'البرمجة بلغة C' من قسم الأمن إن ظهر بالكاتالوج."""
    items = catalog.get("security", [])
    cleaned: List[Dict[str, Any]] = []
    for it in items:
        # عنصر فرعي (children)
        if "children" in it:
            cleaned.append(it)
            continue

        title = it.get("title", "")
        path = it.get("path", "")
        if path in EXCLUDE_SECURITY_PATHS or "لغة C" in title or "C " == title.strip():
            # تجاهل هذا العنصر
            continue
        cleaned.append(it)
    catalog["security"] = cleaned


def load_catalog() -> Dict[str, List[Dict[str, Any]]]:
    """
    يحاول قراءة catalog.json؛ وإن فشل (JSON غير صالح أو غير موجود) يعمل مسح تلقائي.
    ثم يطبّق تعديلاتنا (إضافة شرح الإكسل – حذف C من الأمن).
    """
    data: Dict[str, List[Dict[str, Any]]] = {}
    if CATALOG_FILE.exists():
        try:
            with CATALOG_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)  # قد يرمي JSONDecodeError
        except Exception as e:
            log.error("فشل قراءة catalog.json (%s) – سيتم المسح التلقائي.", e)
            data = _auto_scan_assets()
    else:
        data = _auto_scan_assets()

    # تأكد من وجود الأقسام كمصفوفات
    for k, v in list(data.items()):
        if not isinstance(v, list):
            data[k] = []

    # تطبيق التعديلات المطلوبة
    _ensure_excel_step_item(data)
    _filter_security_items(data)

    # ترتيب الأقسام حسب الترتيب الذي نريده
    ordered: Dict[str, List[Dict[str, Any]]] = {}
    for key in ["prog", "design", "security", "languages", "marketing", "maintenance", "office"]:
        if key in data:
            ordered[key] = data[key]
    # أضف أي أقسام أخرى إن وُجدت
    for k in data:
        if k not in ordered:
            ordered[k] = data[k]

    log.info("📦 Catalog loaded: %s", {k: len(v) for k, v in ordered.items()})
    return ordered


# =========================
# بناء القوائم
# =========================

def build_main_menu() -> InlineKeyboardMarkup:
    buttons: List[InlineKeyboardButton] = []
    for key in CATALOG.keys():
        label = SECTION_LABELS.get(key, key)
        buttons.append(InlineKeyboardButton(label, callback_data=f"sec:{key}"))
    # زر للتواصل (إن رغبت) – يمكنك تعديله أو حذفه
    buttons.append(InlineKeyboardButton("✉️ تواصل مع الإدارة", url="https://t.me/"))
    return InlineKeyboardMarkup(pretty_grid(buttons, cols=2))


def build_section_menu(section: str) -> InlineKeyboardMarkup:
    items = CATALOG.get(section, [])
    buttons: List[InlineKeyboardButton] = []

    for idx, it in enumerate(items):
        if "children" in it:
            title = it.get("title", "مجموعة")
            buttons.append(InlineKeyboardButton(f"📚 {title}", callback_data=f"grp:{section}:{idx}"))
        else:
            title = it.get("title", "ملف")
            buttons.append(InlineKeyboardButton(title, callback_data=f"itm:{section}:{idx}"))

    # رجوع
    rows = pretty_grid(buttons, cols=2)
    rows.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="back:root")])
    return InlineKeyboardMarkup(rows)


def build_children_menu(section: str, group_idx: int) -> InlineKeyboardMarkup:
    group = CATALOG.get(section, [])[group_idx]
    children = group.get("children", [])
    buttons = [InlineKeyboardButton(ch.get("title", f"ملف {i+1}"), callback_data=f"sub:{section}:{group_idx}:{i}")
               for i, ch in enumerate(children)]
    rows = pretty_grid(buttons, cols=2)
    rows.append([InlineKeyboardButton("↩️ رجوع للقسم", callback_data=f"sec:{section}")])
    return InlineKeyboardMarkup(rows)


# =========================
# التعامل مع الأوامر
# =========================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (
        "مرحبًا بك في مكتبة الدورات 📚\n"
        "اختر القسم:"
    )
    await update.effective_message.reply_text(txt, reply_markup=build_main_menu())


async def cmd_reload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    global CATALOG
    CATALOG = load_catalog()
    counts = " | ".join(f"{SECTION_LABELS.get(k,k)}: {len(v)}" for k, v in CATALOG.items())
    await update.effective_message.reply_text(f"تمت إعادة تحميل الكتالوج ✅\nالمحتوى: {counts}",
                                              reply_markup=build_main_menu())


# =========================
# التعامل مع الأزرار
# =========================

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if data == "back:root":
        await query.edit_message_text("اختر القسم:", reply_markup=build_main_menu())
        return

    if data.startswith("sec:"):
        section = data.split(":", 1)[1]
        await query.edit_message_text(SECTION_LABELS.get(section, section),
                                      reply_markup=build_section_menu(section))
        return

    if data.startswith("grp:"):
        _, section, idx = data.split(":")
        await query.edit_message_text("اختر من المجموعة:",
                                      reply_markup=build_children_menu(section, int(idx)))
        return

    if data.startswith("itm:"):
        _, section, idx = data.split(":")
        await send_item(query, section, int(idx))
        return

    if data.startswith("sub:"):
        _, section, gidx, cidx = data.split(":")
        await send_child_item(query, section, int(gidx), int(cidx))
        return


async def send_item(query, section: str, idx: int) -> None:
    item = CATALOG.get(section, [])[idx]
    title = item.get("title", "ملف")
    path = item.get("path", "")

    rp = resolve_path(path)
    if not rp:
        await query.edit_message_text(f"لم أجد الملف في السيرفر: \n<code>{path}</code>",
                                      parse_mode="HTML",
                                      reply_markup=build_section_menu(section))
        return

    await query.edit_message_text("جارٍ الإرسال…")
    try:
        await query.message.reply_document(
            document=FSInputFile(rp),
            filename=rp.name,
            caption=title,
        )
    except Exception as e:
        await query.message.reply_text(f"تعذّر إرسال الملف:\n{e}")

    await query.message.reply_text(SECTION_LABELS.get(section, section),
                                   reply_markup=build_section_menu(section))


async def send_child_item(query, section: str, group_idx: int, child_idx: int) -> None:
    group = CATALOG.get(section, [])[group_idx]
    child = group.get("children", [])[child_idx]
    title = child.get("title", "ملف")
    path = child.get("path", "")

    rp = resolve_path(path)
    if not rp:
        await query.edit_message_text(f"لم أجد الملف في السيرفر: \n<code>{path}</code>",
                                      parse_mode="HTML",
                                      reply_markup=build_children_menu(section, group_idx))
        return

    await query.edit_message_text("جارٍ الإرسال…")
    try:
        await query.message.reply_document(
            document=FSInputFile(rp),
            filename=rp.name,
            caption=title,
        )
    except Exception as e:
        await query.message.reply_text(f"تعذّر إرسال الملف:\n{e}")

    await query.message.reply_text("اختر من المجموعة:",
                                   reply_markup=build_children_menu(section, group_idx))


# =========================
# خادم صحة Render
# =========================

async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="ok")

async def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.add_routes([web.get("/", health_handler),
                    web.get("/health", health_handler),
                    web.get("/healthz", health_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("🌐 Health server on 0.0.0.0:%s (paths: /healthz,/health,/)", port)


# =========================
# التشغيل
# =========================

def get_token() -> str:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or ""
    if not token:
        raise SystemExit("❌ ضع TELEGRAM_TOKEN في متغيرات البيئة على Render")
    return token


def build_app() -> Application:
    return (
        ApplicationBuilder()
        .token(get_token())
        .build()
    )


async def main_async():
    global CATALOG
    CATALOG = load_catalog()

    app = build_app()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CallbackQueryHandler(on_callback))

    # شغّل خادم الصحة
    await start_health_server()

    log.info("🤖 Telegram bot starting…")
    await app.initialize()
    await app.start()
    log.info("telegram.ext.Application: Application started")
    await app.updater.start_polling()
    # ابقِ العملية
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


