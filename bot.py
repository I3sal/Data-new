import subprocess
import os
import re
import requests
import zipfile
import logging
import threading
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import MessageNotModified

# ─── إعدادات ──────────────────────────────────────────────────────────────────
API_TOKEN            = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID             = 8506955611
DATA_DIR             = "data_files"
ZIP_PATH             = "temp.zip"
MEDIAFIRE_PAGE_URL   = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"
STATS_FILE           = "bot_stats.json"
MAX_SEARCHES_PER_DAY = 2
MAX_RESULTS          = 15

# ─── الحالة العامة ────────────────────────────────────────────────────────────
allowed_users: set        = {ADMIN_ID}
pending_users: dict       = {}
is_ready                  = False
download_lock             = threading.Lock()
user_mode: dict           = {}
user_lang: dict           = {}
user_search_count: dict   = {}
welcome_message           = "👋 أهلاً بك في البوت! اختر نوع البحث."

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
    except Exception:
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
        entry["date"]  = today
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
        # محاولة أخيرة: أي رابط تحميل مباشر
        match = re.search(r'"(https://download\d+\.mediafire\.com/[^"]+)"', html)
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
#  محرك البحث (grep)
# ═══════════════════════════════════════════════════════════════════════════════
def do_search(query: str, mode: str) -> list:
    """
    mode: "full" | "email" | "split_user" | "split_email" | "split_phone" | "split_id"
    يعيد قائمة بالنتائج (نصوص).
    """
    if not os.path.isdir(DATA_DIR) or not os.listdir(DATA_DIR):
        return []

    try:
        # بحث في جميع الملفات بغض النظر عن الامتداد
        cmd = [
            "grep", "-r", "-i", "-a",
            "--max-count=1",          # حد لكل ملف
            "-l",                     # أولاً: احصل على أسماء الملفات المطابقة
            query,
            DATA_DIR,
        ]
        # المرحلة 1: أسماء الملفات التي تحتوي على النص
        proc_files = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        matched_files = [f.strip() for f in proc_files.stdout.split("\n") if f.strip()]

        if not matched_files:
            return []

        # المرحلة 2: استخرج السطور الفعلية من الملفات المطابقة
        results = []
        for filepath in matched_files[:MAX_RESULTS]:
            try:
                cmd2 = ["grep", "-i", "-a", "-m", "3", query, filepath]
                proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=10)
                for line in proc2.stdout.split("\n"):
                    line = line.strip()
                    if line:
                        results.append(line)
                    if len(results) >= MAX_RESULTS:
                        break
            except Exception:
                continue
            if len(results) >= MAX_RESULTS:
                break

        return results[:MAX_RESULTS]

    except subprocess.TimeoutExpired:
        log.warning("انتهت مهلة البحث")
        return ["⏰ انتهت مهلة البحث (30 ثانية)"]
    except Exception as exc:
        log.exception("خطأ في البحث: %s", exc)
        return []


def get_data_status() -> dict:
    """معلومات تشخيصية عن ملفات البيانات."""
    if not os.path.isdir(DATA_DIR):
        return {"exists": False, "files": 0, "size_mb": 0, "sample_exts": []}
    all_files = []
    for root, _, files in os.walk(DATA_DIR):
        for f in files:
            all_files.append(os.path.join(root, f))
    exts = list({os.path.splitext(f)[1] or "(بدون امتداد)" for f in all_files[:50]})
    total_size = sum(os.path.getsize(f) for f in all_files if os.path.isfile(f))
    return {
        "exists":    True,
        "files":     len(all_files),
        "size_mb":   round(total_size / (1024 * 1024), 2),
        "sample_exts": exts[:6],
    }


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
    text = re.sub(
        r'(https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+)',
        r'<a href="\1">🔗 رابط تويتر</a>',
        text,
    )
    text = re.sub(
        r'(https?://[^\s<>]+)',
        r'<a href="\1">\1</a>',
        text,
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
    kb        = InlineKeyboardMarkup(row_width=1)
    count     = _get_search_count(uid)
    remaining = MAX_SEARCHES_PER_DAY - count
    status    = f"({remaining}/{MAX_SEARCHES_PER_DAY})" if remaining > 0 else "❌ انتهت الحد"

    kb.add(
        InlineKeyboardButton(
            t(uid, f"🔍 بحث كامل {status}", f"🔍 Full Search {status}"),
            callback_data="mode_full" if remaining > 0 else "no_action",
        ),
        InlineKeyboardButton(
            t(uid, f"🔎 بحث منفصل {status}", f"🔎 Split Search {status}"),
            callback_data="mode_split" if remaining > 0 else "no_action",
        ),
        InlineKeyboardButton(
            t(uid, f"📧 بحث إيميل {status}", f"📧 Email Search {status}"),
            callback_data="mode_email" if remaining > 0 else "no_action",
        ),
        InlineKeyboardButton(t(uid, "🌐 تغيير اللغة", "🌐 Change Language"), callback_data="change_lang"),
        InlineKeyboardButton(t(uid, "📖 تعليمات الاستخدام", "📖 How to Use"), callback_data="show_help"),
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
        InlineKeyboardButton("✅ قبول", callback_data=f"approve_{uid}"),
        InlineKeyboardButton("❌ رفض",  callback_data=f"reject_{uid}"),
    )
    return kb

def admin_panel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📊 الإحصائيات",              callback_data="admin_stats"),
        InlineKeyboardButton("🔄 تصفير العدادات",           callback_data="admin_reset"),
        InlineKeyboardButton("📝 تغيير رسالة الترحيب",      callback_data="admin_welcome"),
        InlineKeyboardButton("👥 المستخدمون المسموح لهم",   callback_data="admin_users"),
        InlineKeyboardButton("⏳ الطلبات المعلّقة",         callback_data="admin_pending"),
        InlineKeyboardButton("↩️ رجوع",                     callback_data="back_menu"),
    )
    return kb


# ═══════════════════════════════════════════════════════════════════════════════
#  /status — تشخيص للإدمن
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        return
    s = get_data_status()
    if not s["exists"]:
        text = (
            "⚠️ <b>حالة البيانات</b>\n\n"
            "❌ مجلد البيانات غير موجود\n"
            f"📡 حالة التحميل: {'✅ جاهز' if is_ready else '⏳ لم يكتمل'}\n\n"
            "💡 أعد تشغيل البوت لبدء التحميل."
        )
    else:
        exts = ", ".join(s["sample_exts"]) if s["sample_exts"] else "غير معروف"
        text = (
            f"📊 <b>حالة البيانات</b>\n\n"
            f"{'✅' if is_ready else '⏳'} جاهز: {is_ready}\n"
            f"📁 عدد الملفات: {s['files']}\n"
            f"💾 الحجم: {s['size_mb']} MB\n"
            f"📄 الامتدادات: {exts}\n"
            f"👥 المستخدمون: {len(allowed_users)}\n"
            f"🔍 بحث اليوم: {sum(1 for e in user_search_count.values() if e.get('date') == _get_today())}"
        )
    await msg.answer(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════════
#  /help — تعليمات الاستخدام
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(commands=["help"])
async def cmd_help(msg: types.Message) -> None:
    uid = msg.from_user.id
    if not is_allowed(uid):
        await msg.answer("⛔ ليس لديك صلاحية. أرسل /start لطلب الوصول.")
        return
    text = t(
        uid,
        (
            "📖 <b>تعليمات الاستخدام</b>\n\n"
            "🔍 <b>أنواع البحث:</b>\n"
            "• <b>بحث كامل</b> — يبحث في كامل السطر (اسم، إيميل، رقم، ...)\n"
            "• <b>بحث منفصل</b> — تختار حقلاً محدداً (يوزر / إيميل / هاتف / آيدي)\n"
            "• <b>بحث إيميل</b> — مخصص للبحث بعنوان البريد الإلكتروني\n\n"
            "💡 <b>نصائح للحصول على نتائج:</b>\n"
            "• اكتب جزءاً من الكلمة وليس الكلمة كاملة\n"
            "• لا تضع @ في اليوزر عند البحث\n"
            "• جرب الاسم الأول فقط إذا لم تجد نتائج\n"
            "• البحث لا يفرّق بين الحروف الكبيرة والصغيرة\n\n"
            "📊 <b>الحصة اليومية:</b>\n"
            f"• {MAX_SEARCHES_PER_DAY} عمليات بحث يومياً لكل مستخدم\n"
            "• تُجدَّد الحصة تلقائياً كل يوم\n\n"
            "⌨️ <b>أوامر سريعة:</b>\n"
            "• اكتب <code>menu</code> لفتح القائمة\n"
            "• /start لإعادة البدء\n"
            "• /help لعرض هذه التعليمات"
        ),
        (
            "📖 <b>How to Use</b>\n\n"
            "🔍 <b>Search Types:</b>\n"
            "• <b>Full Search</b> — searches the entire line\n"
            "• <b>Split Search</b> — target a specific field (username / email / phone / ID)\n"
            "• <b>Email Search</b> — dedicated email address search\n\n"
            "💡 <b>Tips for better results:</b>\n"
            "• Type part of a word, not the full word\n"
            "• Don't include @ when searching usernames\n"
            "• Try first name only if no results found\n"
            "• Search is case-insensitive\n\n"
            "📊 <b>Daily Quota:</b>\n"
            f"• {MAX_SEARCHES_PER_DAY} searches per day per user\n"
            "• Quota resets automatically each day\n\n"
            "⌨️ <b>Quick Commands:</b>\n"
            "• Type <code>menu</code> to open the menu\n"
            "• /start to restart\n"
            "• /help to show this guide"
        ),
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=main_menu(uid))


# ═══════════════════════════════════════════════════════════════════════════════
#  /start  — نظام الموافقة
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message) -> None:
    uid   = msg.from_user.id
    name  = msg.from_user.full_name
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "لا يوجد"

    if is_admin(uid):
        user_lang.setdefault(uid, "ar")
        await msg.answer(
            f"👋 أهلاً {name}!\n\nاختر اللغة / Choose language:",
            reply_markup=lang_keyboard(),
        )
        return

    if is_allowed(uid):
        user_lang.setdefault(uid, "ar")
        await msg.answer(welcome_message, reply_markup=lang_keyboard())
        return

    if uid not in pending_users:
        pending_users[uid] = {"name": name, "username": uname}
        await msg.answer(
            "⏳ تم إرسال طلب الوصول إلى المشرف.\n"
            "سيتم إشعارك فور الموافقة.\n\n"
            f"🆔 معرّفك: <code>{uid}</code>",
            parse_mode="HTML",
        )
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
    info  = pending_users.pop(target_id, {})
    name  = info.get("name", str(target_id))
    uname = info.get("username", "")

    if action == "approve":
        allowed_users.add(target_id)
        user_lang[target_id] = "ar"
        try:
            await call.message.edit_text(f"✅ تمت الموافقة على {name} ({uname})", parse_mode="HTML")
        except MessageNotModified:
            pass
        try:
            await bot.send_message(target_id, welcome_message, reply_markup=lang_keyboard())
        except Exception:
            pass
    else:
        try:
            await call.message.edit_text(f"❌ تم رفض {name} ({uname})", parse_mode="HTML")
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
    total_users    = len(allowed_users)
    pending        = len(pending_users)
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
        await call.message.edit_text("✅ تم تصفير العدادات اليومية.", reply_markup=admin_panel_kb())
    except MessageNotModified:
        pass
    await call.answer()


@dp.callback_query_handler(lambda c: c.data == "admin_welcome")
async def cb_admin_welcome(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    # ضع المشرف في وضع تغيير الرسالة
    user_mode[call.from_user.id] = "set_welcome"
    await call.message.answer(
        "📝 أرسل رسالة الترحيب الجديدة:\n"
        f"(الحالية: <code>{welcome_message}</code>)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("↩️ إلغاء", callback_data="admin_panel")
        ),
    )
    await call.answer()


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
        text  = f"⏳ <b>الطلبات المعلّقة ({len(pending_users)})</b>\n\n" + "\n".join(lines)
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


@dp.callback_query_handler(lambda c: c.data == "show_help")
async def cb_show_help(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True)
        return
    text = t(
        uid,
        (
            "📖 <b>تعليمات الاستخدام</b>\n\n"
            "🔍 <b>أنواع البحث:</b>\n"
            "• <b>بحث كامل</b> — يبحث في كامل السطر\n"
            "• <b>بحث منفصل</b> — تختار حقلاً (يوزر / إيميل / هاتف / آيدي)\n"
            "• <b>بحث إيميل</b> — مخصص للبريد الإلكتروني\n\n"
            "💡 <b>نصائح للحصول على نتائج:</b>\n"
            "• اكتب <b>جزءاً</b> من الكلمة وليس الكلمة كاملة\n"
            "• لا تضع @ في اليوزر عند البحث\n"
            "• جرب الاسم الأول فقط إذا لم تجد نتائج\n"
            "• البحث لا يفرّق بين الكبير والصغير\n\n"
            f"📊 الحصة اليومية: {MAX_SEARCHES_PER_DAY} عمليات بحث\n"
            "⌨️ اكتب <code>menu</code> لفتح القائمة في أي وقت"
        ),
        (
            "📖 <b>How to Use</b>\n\n"
            "🔍 <b>Search Types:</b>\n"
            "• <b>Full Search</b> — searches the entire line\n"
            "• <b>Split Search</b> — pick a field (username / email / phone / ID)\n"
            "• <b>Email Search</b> — dedicated email search\n\n"
            "💡 <b>Tips for better results:</b>\n"
            "• Type <b>part</b> of a word, not the full word\n"
            "• Don't include @ when searching usernames\n"
            "• Try first name only if no results found\n"
            "• Search is case-insensitive\n\n"
            f"📊 Daily quota: {MAX_SEARCHES_PER_DAY} searches\n"
            "⌨️ Type <code>menu</code> to open the menu anytime"
        ),
    )
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb(uid))
    except MessageNotModified:
        pass
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  اختيار وضع البحث
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("mode_"))
async def cb_mode(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True)
        return

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

    field, ar_name, en_name = FIELD_MAP[call.data]
    user_mode[uid] = f"split_{field}"

    try:
        await call.message.edit_text(
            t(uid, f"🔍 أرسل {ar_name} للبحث:", f"🔍 Send {en_name} to search:"),
            reply_markup=_back_kb(uid),
        )
    except MessageNotModified:
        pass
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  معالج الرسائل النصية — البحث الفعلي
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler()
async def handle_text(msg: types.Message) -> None:
    uid  = msg.from_user.id
    text = msg.text.strip() if msg.text else ""

    if not text:
        return

    # ─── أوامر سريعة ──────────────────────────────────────────────────────────
    if text.lower() in ("menu", "نوت", "قائمة", "/menu"):
        if is_allowed(uid):
            await msg.answer(
                t(uid, "اختر نوع البحث:", "Choose search type:"),
                reply_markup=main_menu(uid),
            )
        return

    # ─── تغيير رسالة الترحيب (المشرف فقط) ────────────────────────────────────
    if is_admin(uid) and user_mode.get(uid) == "set_welcome":
        global welcome_message
        welcome_message = text
        user_mode.pop(uid, None)
        await msg.answer(
            f"✅ تم تحديث رسالة الترحيب:\n<code>{welcome_message}</code>",
            parse_mode="HTML",
            reply_markup=main_menu(uid),
        )
        return

    # ─── تحقق من الصلاحية ─────────────────────────────────────────────────────
    if not is_allowed(uid):
        await msg.answer("⛔ ليس لديك صلاحية. أرسل /start لطلب الوصول.")
        return

    mode = user_mode.get(uid)
    if not mode or mode == "split_pending":
        await msg.answer(
            t(uid, "اختر نوع البحث أولاً:", "Choose a search type first:"),
            reply_markup=main_menu(uid),
        )
        return

    # ─── تحقق من الحد اليومي ──────────────────────────────────────────────────
    if _get_search_count(uid) >= MAX_SEARCHES_PER_DAY:
        await msg.answer(
            t(uid, "❌ انتهت حصتك اليومية من البحث.", "❌ Daily search limit reached."),
            reply_markup=main_menu(uid),
        )
        return

    # ─── تنظيف المدخل ─────────────────────────────────────────────────────────
    if not sanitize(text):
        await msg.answer(
            t(uid, "⚠️ الاستعلام يحتوي على رموز غير مسموح بها.", "⚠️ Query contains invalid characters.")
        )
        return

    # ─── التحقق من جاهزية البيانات ────────────────────────────────────────────
    if not is_ready:
        await msg.answer(
            t(uid, "⏳ البيانات لا تزال تُحمَّل، يرجى الانتظار قليلاً...",
                   "⏳ Data is still loading, please wait a moment...")
        )
        return

    # ─── تنفيذ البحث ──────────────────────────────────────────────────────────
    wait_msg = await msg.answer(t(uid, "🔍 جارٍ البحث...", "🔍 Searching..."))

    results   = do_search(text, mode)
    count     = _increment_search(uid)
    remaining = MAX_SEARCHES_PER_DAY - count

    quota_line = t(
        uid,
        f"\n\n🔢 متبقي: {remaining}/{MAX_SEARCHES_PER_DAY} بحث اليوم",
        f"\n\n🔢 Remaining: {remaining}/{MAX_SEARCHES_PER_DAY} searches today",
    )

    if not results:
        await wait_msg.edit_text(
            t(uid, f"❌ لم يتم العثور على نتائج.{quota_line}",
                   f"❌ No results found.{quota_line}"),
            reply_markup=main_menu(uid),
        )
        user_mode.pop(uid, None)
        return

    # ─── تنسيق النتائج ────────────────────────────────────────────────────────
    header      = t(uid, f"✅ النتائج ({len(results)}):", f"✅ Results ({len(results)}):")
    result_body = "\n".join(f"<code>{r}</code>" for r in results)
    result_body = _format_links(result_body)
    full_text   = f"{header}\n\n{result_body}{quota_line}"

    # اقتطاع عند حد تيليجرام (4096 حرف)
    if len(full_text) > 4000:
        full_text = full_text[:3900] + "\n...(مقطوع)" + quota_line

    try:
        await wait_msg.edit_text(full_text, parse_mode="HTML", reply_markup=main_menu(uid))
    except Exception:
        await msg.answer(full_text, parse_mode="HTML", reply_markup=main_menu(uid))

    user_mode.pop(uid, None)


# ═══════════════════════════════════════════════════════════════════════════════
#  تشغيل البوت
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info("🚀 تشغيل البوت …")
    executor.start_polling(dp, skip_updates=True)
