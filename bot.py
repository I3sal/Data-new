import subprocess
import os
import re
import requests
import zipfile
import logging
import threading
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import MessageNotModified

# ─── إعدادات ──────────────────────────────────────────────────────────────────
API_TOKEN          = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID           = 8506955611
DATA_DIR           = "data_files"
ZIP_PATH           = "temp.zip"
MEDIAFIRE_PAGE_URL = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"
STATS_FILE         = "bot_stats.json"
MAX_SEARCHES_PER_DAY = 2

# ─── الحالة العامة ────────────────────────────────────────────────────────────
allowed_users: set[int]        = {ADMIN_ID}
pending_users: dict[int, dict] = {}
is_ready       = False
download_lock  = threading.Lock()
user_mode: dict[int, str] = {}
user_lang: dict[int, str] = {}
user_search_count: dict[int, dict] = {}  # uid -> {date: "YYYY-MM-DD", count: N}
welcome_message = "👋 أهلاً بك في البوت! اختر نوع البحث."

# ─── تسجيل ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
dp  = Dispatcher(bot)


# ═══════════════════════════════════════════════════════════════════════════════
#  إدارة الإحصائيات
# ═══════════════════════════════════════════════════════════════════════════════
def _get_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def _load_stats() -> dict:
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def _save_stats(stats: dict) -> None:
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

def _increment_search(uid: int) -> int:
    today = _get_today()
    if uid not in user_search_count:
        user_search_count[uid] = {"date": today, "count": 0}
    
    entry = user_search_count[uid]
    if entry["date"] != today:
        entry["date"] = today
        entry["count"] = 0
    
    entry["count"] += 1
    return entry["count"]

def _get_search_count(uid: int) -> int:
    today = _get_today()
    entry = user_search_count.get(uid, {})
    if entry.get("date") != today:
        return 0
    return entry.get("count", 0)

def _reset_counts() -> None:
    global user_search_count
    user_search_count = {}
    log.info("تم تصفير العدادات")

# ═══════════════════════════════════════════════════════════════════════════════
#  تحميل البيانات في الخلفية
# ═══════════════════════════════════════════════════════════════════════════════
def _resolve_mediafire_link(page_url: str) -> str:
    html  = requests.get(page_url, timeout=30).text
    match = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', html)
    if not match:
        match = re.search(r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']', html)
    if not match:
        raise ValueError("تعذّر العثور على رابط التحميل.")
    return match.group(1)


def download_background() -> None:
    global is_ready
    with download_lock:
        if is_ready:
            return
        if os.path.isdir(DATA_DIR) and os.listdir(DATA_DIR):
            log.info("المجلد موجود — تخطي التحميل.")
            is_ready = True
            return
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            log.info("جارٍ تحليل رابط MediaFire …")
            direct_url = _resolve_mediafire_link(MEDIAFIRE_PAGE_URL)
            log.info("جارٍ التحميل …")
            with requests.get(direct_url, stream=True, timeout=180) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                done  = 0
                with open(ZIP_PATH, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if total and done * 100 // total % 10 == 0:
                            log.info("التحميل: %d%%", done * 100 // total)
            log.info("جارٍ فك الضغط …")
            with zipfile.ZipFile(ZIP_PATH, "r") as z:
                z.extractall(DATA_DIR)
            os.remove(ZIP_PATH)
            is_ready = True
            log.info("✅ البيانات جاهزة.")
        except Exception as exc:
            log.exception("فشل التحميل: %s", exc)
            if os.path.exists(ZIP_PATH):
                os.remove(ZIP_PATH)


threading.Thread(target=download_background, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════════
#  مساعدات عامة
# ═══════════════════════════════════════════════════════════════════════════════
def is_allowed(uid: int) -> bool:
    return uid in allowed_users

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def t(uid: int, ar: str, en: str) -> str:
    return ar if user_lang.get(uid, "ar") == "ar" else en

def sanitize(q: str) -> bool:
    return not any(c in q for c in ("'", '"', ";", "&", "|", "`", "$", "\\", "\n"))

def _back_kb(uid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t(uid, "↩️ رجوع للقائمة", "↩️ Back"), callback_data="back_menu"))
    return kb

def _format_links(text: str) -> str:
    """تحويل روابط Twitter و URLs إلى روابط قابلة للنقر."""
    # تحويل روابط Twitter
    text = re.sub(
        r'(https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+)',
        r'<a href="\1">🔗 رابط تويتر</a>',
        text
    )
    # تحويل URLs العامة
    text = re.sub(
        r'(https?://[^\s<>]+)',
        r'<a href="\1">\1</a>',
        text
    )
    return text


# ═══════════════════════════════════════════════════════════════════════════════
#  لوحات المفاتيح
# ═══════════════════════════════════════════════════════════════════════════════
def lang_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
        InlineKeyboardButton("🇬🇧 English",  callback_data="lang_en"),
    )
    return kb

def main_menu(uid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    count = _get_search_count(uid)
    remaining = MAX_SEARCHES_PER_DAY - count
    status = f"({remaining}/{MAX_SEARCHES_PER_DAY})" if remaining > 0 else "❌ انتهت الحد"
    
    kb.add(
        InlineKeyboardButton(
            t(uid, f"🔍 بحث كامل {status}",              f"🔍 Full Search {status}"),
            callback_data="mode_full" if remaining > 0 else "no_action"
        ),
        InlineKeyboardButton(
            t(uid, f"🔎 بحث منفصل {status}",            f"🔎 Split Search {status}"),
            callback_data="mode_split" if remaining > 0 else "no_action"
        ),
        InlineKeyboardButton(
            t(uid, f"📧 بحث إيميل {status}",            f"📧 Email Search {status}"),
            callback_data="mode_email" if remaining > 0 else "no_action"
        ),
        InlineKeyboardButton(t(uid, "🌐 تغيير اللغة",    "🌐 Change Language"),  callback_data="change_lang"),
    )
    if is_admin(uid):
        kb.add(InlineKeyboardButton("⚙️ بانل الإدمن", callback_data="admin_panel"))
    return kb

def split_field_kb(uid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(t(uid, "👤 اليوزر",     "👤 Username"), callback_data="field_user"),
        InlineKeyboardButton(t(uid, "📧 الإيميل",    "📧 Email"),    callback_data="field_email"),
        InlineKeyboardButton(t(uid, "📱 رقم الهاتف", "📱 Phone"),    callback_data="field_phone"),
        InlineKeyboardButton(t(uid, "🆔 الآيدي",     "🆔 ID"),       callback_data="field_id"),
        InlineKeyboardButton(t(uid, "↩️ رجوع",       "↩️ Back"),     callback_data="back_menu"),
    )
    return kb

def approve_kb(uid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ قبول",  callback_data=f"approve_{uid}"),
        InlineKeyboardButton("❌ رفض",   callback_data=f"reject_{uid}"),
    )
    return kb

def admin_panel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"),
        InlineKeyboardButton("🔄 تصفير العدادات", callback_data="admin_reset"),
        InlineKeyboardButton("📝 تغيير رسالة الترحيب", callback_data="admin_welcome"),
        InlineKeyboardButton("👥 المستخدمون المسموح لهم", callback_data="admin_users"),
        InlineKeyboardButton("⏳ الطلبات المعلّقة", callback_data="admin_pending"),
        InlineKeyboardButton("↩️ رجوع", callback_data="back_menu"),
    )
    return kb


# ═══════════════════════════════════════════════════════════════════════════════
#  /start  — نظام الموافقة
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message) -> None:
    uid  = msg.from_user.id
    name = msg.from_user.full_name
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "لا يوجد"

    # المشرف يبدأ مباشرة
    if is_admin(uid):
        user_lang.setdefault(uid, "ar")
        await msg.answer(
            f"👋 أهلاً {name}!\n\nاختر اللغة / Choose language:",
            reply_markup=lang_keyboard(),
        )
        return

    # مستخدم مسموح له مسبقاً
    if is_allowed(uid):
        user_lang.setdefault(uid, "ar")
        await msg.answer(welcome_message, reply_markup=lang_keyboard())
        return

    # طلب جديد أو معلّق
    if uid not in pending_users:
        pending_users[uid] = {"name": name, "username": uname}
        await msg.answer(
            "⏳ تم إرسال طلب الوصول إلى المشرف.\n"
            "سيتم إشعارك فور الموافقة.\n\n"
            f"🆔 معرّفك: <code>{uid}</code>",
            parse_mode="HTML",
        )
        # أرسل طلب الموافقة للمشرف
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>طلب وصول جديد</b>\n\n"
            f"👤 الاسم: {name}\n"
            f"🔗 اليوزر: {uname}\n"
            f"🆔 الآيدي: <code>{uid}</code>",
            parse_mode="HTML",
            reply_markup=approve_kb(uid),
        )
    else:
        await msg.answer(
            "⏳ طلبك قيد الانتظار.\n"
            f"🆔 معرّفك: <code>{uid}</code>",
            parse_mode="HTML",
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  أزرار القبول / الرفض
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("approve_") or c.data.startswith("reject_"))
async def cb_approve_reject(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔ غير مصرح.", show_alert=True)
        return

    action, target_id = call.data.split("_", 1)
    target_id = int(target_id)
    info = pending_users.pop(target_id, {})
    name  = info.get("name", str(target_id))
    uname = info.get("username", "")

    if action == "approve":
        allowed_users.add(target_id)
        user_lang[target_id] = "ar"
        try:
            await call.message.edit_text(
                f"✅ تمت الموافقة على {name} ({uname})",
                parse_mode="HTML",
            )
        except MessageNotModified:
            pass
        try:
            await bot.send_message(target_id, welcome_message, reply_markup=lang_keyboard())
        except Exception:
            pass
    else:
        try:
            await call.message.edit_text(
                f"❌ تم رفض {name} ({uname})",
                parse_mode="HTML",
            )
        except MessageNotModified:
            pass
        try:
            await bot.send_message(target_id, "❌ تم رفض طلبك من قِبل المشرف.")
        except Exception:
            pass

    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  بانل الإدمن
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "admin_panel")
async def cb_admin_panel(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        await call.message.edit_text(
            "⚙️ <b>بانل التحكم</b>\n\nاختر الخيار:",
            parse_mode="HTML",
            reply_markup=admin_panel_kb(),
        )
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_stats")
async def cb_admin_stats(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    total_users = len(allowed_users)
    pending = len(pending_users)
    today_searches = sum(1 for e in user_search_count.values() if e.get("date") == _get_today())
    text = (
        f"📊 <b>الإحصائيات</b>\n\n"
        f"👥 عدد المستخدمين: {total_users}\n"
        f"⏳ الطلبات المعلّقة: {pending}\n"
        f"🔍 عمليات البحث اليوم: {today_searches}\n"
        f"📅 التاريخ: {_get_today()}"
    )
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_kb())
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_reset")
async def cb_admin_reset(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    _reset_counts()
    try:
        await call.message.edit_text(
            "✅ تم تصفير العدادات اليومية.",
            reply_markup=admin_panel_kb(),
        )
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_welcome")
async def cb_admin_welcome(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    msg = await call.message.answer(
        "📝 أرسل رسالة الترحيب الجديدة:\n"
        f"(الحالية: <code>{welcome_message}</code>)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("↩️ إلغاء", callback_data="admin_panel")
        ),
    )

@dp.callback_query_handler(lambda c: c.data == "admin_users")
async def cb_admin_users(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    ids = "\n".join(f"• <code>{u}</code>" for u in allowed_users)
    try:
        await call.message.edit_text(
            f"👥 <b>المستخدمون المسموح لهم ({len(allowed_users)})</b>\n\n{ids}",
            parse_mode="HTML",
            reply_markup=admin_panel_kb(),
        )
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_pending")
async def cb_admin_pending(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    if not pending_users:
        text = "✅ لا توجد طلبات معلّقة."
    else:
        lines = [f"• <code>{uid}</code> — {d['name']}" for uid, d in pending_users.items()]
        text = f"⏳ <b>الطلبات المعلّقة ({len(pending_users)})</b>\n\n" + "\n".join(lines)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_kb())
    except MessageNotModified:
        pass
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  اختيار اللغة
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("lang_") or c.data == "change_lang")
async def cb_language(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True)
        return

    if call.data == "change_lang":
        try:
            await call.message.edit_text("🌐 اختر اللغة / Choose language:", reply_markup=lang_keyboard())
        except MessageNotModified:
            pass
        await call.answer()
        return

    user_lang[uid] = call.data.split("_")[1]
    try:
        await call.message.edit_text(
            t(uid, "✅ تم اختيار العربية.\n\nاختر نوع البحث:", "✅ English selected.\n\nChoose search type:"),
            reply_markup=main_menu(uid),
        )
    except MessageNotModified:
        pass
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  رجوع للقائمة الرئيسية
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "back_menu")
async def cb_back_menu(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True)
        return
    user_mode.pop(uid, None)
    try:
        await call.message.edit_text(
            t(uid, "اختر نوع البحث:", "Choose search type:"),
            reply_markup=main_menu(uid),
        )
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "no_action")
async def cb_no_action(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    await call.answer(t(uid, "❌ انتهت الحد اليومي للبحث", "❌ Daily limit reached"), show_alert=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  اختيار وضع البحث
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("mode_"))
async def cb_mode(call: types.CallbackQuery) -> None:
    uid  = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True)
        return
    
    # تحقق من الحد اليومي
    if _get_search_count(uid) >= MAX_SEARCHES_PER_DAY:
        await call.answer(t(uid, "❌ انتهت حصتك اليومية", "❌ Daily limit reached"), show_alert=True)
        return
    
    mode = call.data.split("_")[1]

    if mode == "split":
        user_mode[uid] = "split_pending"
        try:
            await call.message.edit_text(
                t(uid, "اختر الحقل الذي تريد البحث فيه:", "Choose the field to search in:"),
                reply_markup=split_field_kb(uid),
            )
        except MessageNotModified:
            pass
    elif mode == "full":
        user_mode[uid] = "full"
        try:
            await call.message.edit_text(
                t(uid, "🔍 أرسل الكلمة أو النص للبحث:", "🔍 Send the keyword to search:"),
                reply_markup=_back_kb(uid),
            )
        except MessageNotModified:
            pass
    elif mode == "email":
        user_mode[uid] = "email"
        try:
            await call.message.edit_text(
                t(uid, "📧 أرسل الإيميل أو جزء منه:", "📧 Send the email or part of it:"),
                reply_markup=_back_kb(uid),
            )
        except MessageNotModified:
            pass
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  اختيار حقل البحث المنفصل
# ═══════════════════════════════════════════════════════════════════════════════
FIELD_MAP = {
    "field_user":  ("user",  "اليوزر",     "Username"),
    "field_email": ("email", "الإيميل",    "Email"),
    "field_phone": ("phone", "رقم الهاتف", "Phone"),
    "field_id":    ("id",    "الآيدي",     "ID"),
}

@dp.callback_query_handler(lambda c: c.data in FIELD_MAP)
async def cb_field(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True)
        return
    key, ar_lbl, en_lbl = FIELD_MAP[call.data]
    user_mode[uid] = f"split:{key}"
    label = t(uid, ar_lbl, en_lbl)
    try:
        await call.message.edit_text(
            t(uid, f"🔎 بحث في [{label}].\nأرسل قيمة البحث:",
                   f"🔎 Search in [{label}].\nSend the search value:"),
            reply_markup=_back_kb(uid),
        )
    except MessageNotModified:
        pass
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  أوامر المشرف
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(commands=["adduser"])
async def cmd_adduser(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await msg.reply("الاستخدام: /adduser [user_id]")
        return
    new_id = int(parts[1])
    allowed_users.add(new_id)
    user_lang[new_id] = "ar"
    pending_users.pop(new_id, None)
    await msg.reply(f"✅ تمت إضافة المستخدم <code>{new_id}</code>.", parse_mode="HTML")

@dp.message_handler(commands=["removeuser"])
async def cmd_removeuser(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await msg.reply("الاستخدام: /removeuser [user_id]")
        return
    uid = int(parts[1])
    allowed_users.discard(uid)
    await msg.reply(f"✅ تمت إزالة المستخدم <code>{uid}</code>.", parse_mode="HTML")

@dp.message_handler(commands=["admin"])
async def cmd_admin(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        return
    await msg.answer("⚙️ <b>بانل التحكم</b>", parse_mode="HTML", reply_markup=admin_panel_kb())

@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message) -> None:
    if not is_allowed(msg.from_user.id):
        return
    uid = msg.from_user.id
    if is_ready:
        await msg.reply(t(uid, "✅ البيانات جاهزة للبحث.", "✅ Data is ready."))
    else:
        await msg.reply(t(uid, "⏳ البيانات لا تزال تُحمَّل …", "⏳ Data is loading …"))


# ═══════════════════════════════════════════════════════════════════════════════
#  منطق البحث
# ═══════════════════════════════════════════════════════════════════════════════
def grep_full(query: str, max_lines: int = 15) -> list[str]:
    r = subprocess.run(
        ["grep", "-rFih", "--", query, DATA_DIR],
        capture_output=True, text=True, timeout=30,
    )
    return [l.strip() for l in r.stdout.splitlines() if l.strip()][:max_lines]


def grep_split(query: str, field: str, max_lines: int = 15) -> list[str]:
    field_index = {"user": 0, "email": 1, "password": 2, "phone": 3, "id": 4}
    idx = field_index.get(field, 0)
    r = subprocess.run(
        ["grep", "-rFih", "--", query, DATA_DIR],
        capture_output=True, text=True, timeout=30,
    )
    results = []
    for line in r.stdout.splitlines():
        parts = re.split(r"[:|]\t?", line)
        if len(parts) > idx and query.lower() in parts[idx].lower():
            results.append(line.strip())
        if len(results) >= max_lines:
            break
    return results


def grep_email_pass(query: str, max_lines: int = 15) -> list[str]:
    r = subprocess.run(
        ["grep", "-rFih", "--", query, DATA_DIR],
        capture_output=True, text=True, timeout=30,
    )
    pattern = re.compile(r"([\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,})[:|]([\S]+)")
    results = []
    for line in r.stdout.splitlines():
        m = pattern.search(line)
        if m:
            results.append(f"📧 {m.group(1)}\n🔑 {m.group(2)}")
        if len(results) >= max_lines:
            break
    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  معالج النصوص (البحث) — يعمل في الخاص والقروبات
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler()
async def handle_text(msg: types.Message) -> None:
    uid = msg.from_user.id

    # في القروبات، تجاهل المستخدمين غير المسموح لهم
    if msg.chat.type in ["group", "supergroup"] and not is_allowed(uid):
        return

    # في الخاص، تجاهل صامتاً
    if msg.chat.type == "private" and not is_allowed(uid):
        return

    # إذا لم يختر وضعاً بعد
    if uid not in user_mode:
        await msg.reply(
            t(uid, "اختر نوع البحث أولاً:", "Choose search type first:"),
            reply_markup=main_menu(uid),
        )
        return

    if not is_ready:
        await msg.reply(t(uid,
            "⏳ البيانات لا تزال تُحمَّل، انتظر قليلاً ثم أعد المحاولة.",
            "⏳ Data is still loading, please wait."))
        return

    # تحقق من الحد اليومي
    count = _get_search_count(uid)
    if count >= MAX_SEARCHES_PER_DAY:
        await msg.reply(t(uid,
            f"❌ انتهت حصتك اليومية ({MAX_SEARCHES_PER_DAY} بحث).",
            f"❌ Daily limit reached ({MAX_SEARCHES_PER_DAY} searches)."))
        return

    query = msg.text.strip().lstrip("@")
    if not query:
        return

    if not sanitize(query):
        await msg.reply(t(uid,
            "⚠️ الاستعلام يحتوي على رموز غير مسموح بها.",
            "⚠️ Query contains disallowed characters."))
        return

    # أزيد العداد
    _increment_search(uid)

    wait_msg = await msg.reply(t(uid, "🔄 جارٍ البحث …", "🔄 Searching …"))

    mode = user_mode.get(uid, "full")
    try:
        if mode == "full":
            lines = grep_full(query)
        elif mode.startswith("split:"):
            lines = grep_split(query, mode.split(":")[1])
        elif mode == "email":
            lines = grep_email_pass(query)
        else:
            lines = grep_full(query)

        await wait_msg.delete()

        if not lines:
            await msg.reply(
                t(uid, "❌ لم يُعثر على نتائج.", "❌ No results found."),
                reply_markup=_back_kb(uid),
            )
            return

        sep    = "\n\n" if mode == "email" else "\n"
        output = sep.join(lines)
        
        # معالجة الروابط
        output_formatted = _format_links(output)
        
        header = t(uid, f"✅ النتائج ({len(lines)}):", f"✅ Results ({len(lines)}):")

        # محاولة الإرسال مع الروابط
        try:
            full_text = f"{header}\n\n{output_formatted}"
            if len(full_text) <= 4096:
                await msg.answer(full_text, parse_mode="HTML", reply_markup=_back_kb(uid), disable_web_page_preview=False)
            else:
                # تقطيع
                await msg.answer(header, parse_mode="HTML")
                chunk: list[str] = []
                chunks: list[list[str]] = []
                for line in lines:
                    chunk.append(line)
                    if len(sep.join(chunk)) > 3600:
                        chunks.append(chunk[:-1])
                        chunk = [line]
                chunks.append(chunk)
                for i, c in enumerate(chunks):
                    formatted_chunk = sep.join(c)
                    formatted_chunk = _format_links(formatted_chunk)
                    kb = _back_kb(uid) if i == len(chunks) - 1 else None
                    await msg.answer(f"{formatted_chunk}", parse_mode="HTML", reply_markup=kb, disable_web_page_preview=False)
        except:
            # fallback: بدون formatting
            full_text = f"{header}\n\n<code>{output}</code>"
            if len(full_text) <= 4096:
                await msg.answer(full_text, parse_mode="HTML", reply_markup=_back_kb(uid))
            else:
                await msg.answer(header)
                chunk, chunks = [], []
                for line in lines:
                    chunk.append(line)
                    if len("\n".join(chunk)) > 3600:
                        chunks.append(chunk[:-1])
                        chunk = [line]
                chunks.append(chunk)
                for i, c in enumerate(chunks):
                    kb = _back_kb(uid) if i == len(chunks) - 1 else None
                    await msg.answer(f"<code>{chr(10).join(c)}</code>", parse_mode="HTML", reply_markup=kb)

    except subprocess.TimeoutExpired:
        await wait_msg.delete()
        await msg.reply(t(uid, "⚠️ انتهت مهلة البحث.", "⚠️ Search timed out."))
    except Exception as exc:
        log.exception("خطأ في البحث: %s", exc)
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await msg.reply(t(uid, "⚠️ حدث خطأ أثناء البحث.", "⚠️ An error occurred."))


# ═══════════════════════════════════════════════════════════════════════════════
#  معالج تغيير رسالة الترحيب
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(state=None)
async def handle_welcome_change(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        return
    # هذا معالج بسيط — يمكن تحسينه باستخدام FSM
    # الآن نتركه كما هو


# ═══════════════════════════════════════════════════════════════════════════════
#  نقطة الدخول
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
