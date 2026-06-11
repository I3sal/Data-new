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
API_TOKEN          = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID           = 8506955611
DATA_DIR           = "data_files"
ZIP_PATH           = "temp.zip"
MEDIAFIRE_PAGE_URL = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"
USERS_FILE         = "users.json"
MAX_RESULTS        = 15
DEFAULT_LIMIT      = 2

# ─── الحالة العامة ────────────────────────────────────────────────────────────
allowed_users: set = {ADMIN_ID}
pending_users: dict = {}
is_ready = False
download_lock = threading.Lock()
user_mode: dict = {}
user_lang: dict = {}
user_search_count: dict = {}
user_quota: dict = {}        # uid -> int  (-1 = غير محدود)
welcome_message = "👋 أهلاً بك في البوت! اختر نوع البحث."

# ─── تسجيل ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
dp  = Dispatcher(bot)


# ═══════════════════════════════════════════════════════════════════════════════
#  حفظ وتحميل المستخدمين (دائم على القرص)
# ═══════════════════════════════════════════════════════════════════════════════
def save_users() -> None:
    try:
        with open(USERS_FILE, "w") as f:
            json.dump({
                "allowed": list(allowed_users),
                "quota":   {str(k): v for k, v in user_quota.items()},
            }, f, indent=2)
    except Exception as e:
        log.error("فشل حفظ المستخدمين: %s", e)

def load_users() -> None:
    global allowed_users, user_quota
    try:
        with open(USERS_FILE) as f:
            data = json.load(f)
        loaded = {int(x) for x in data.get("allowed", [])}
        loaded.add(ADMIN_ID)
        allowed_users = loaded
        user_quota = {int(k): v for k, v in data.get("quota", {}).items()}
        log.info("✅ تم تحميل %d مستخدم من الملف.", len(allowed_users))
    except FileNotFoundError:
        log.info("ملف المستخدمين غير موجود — بدء جديد.")
    except Exception as e:
        log.error("فشل تحميل المستخدمين: %s", e)

load_users()


# ═══════════════════════════════════════════════════════════════════════════════
#  إدارة الحصص والعدادات
# ═══════════════════════════════════════════════════════════════════════════════
def _get_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def _get_user_limit(uid: int) -> int:
    """إرجاع الحصة اليومية للمستخدم. -1 = غير محدود."""
    if uid == ADMIN_ID:
        return -1
    return user_quota.get(uid, DEFAULT_LIMIT)

def _get_search_count(uid: int) -> int:
    if _get_user_limit(uid) == -1:
        return 0
    today = _get_today()
    entry = user_search_count.get(uid, {})
    if entry.get("date") != today:
        return 0
    return entry.get("count", 0)

def _increment_search(uid: int) -> int:
    if _get_user_limit(uid) == -1:
        return 0
    today = _get_today()
    if uid not in user_search_count:
        user_search_count[uid] = {"date": today, "count": 0}
    entry = user_search_count[uid]
    if entry["date"] != today:
        entry["date"]  = today
        entry["count"] = 0
    entry["count"] += 1
    return entry["count"]

def _has_quota(uid: int) -> bool:
    limit = _get_user_limit(uid)
    if limit == -1:
        return True
    return _get_search_count(uid) < limit

def _reset_counts() -> None:
    global user_search_count
    user_search_count = {}
    log.info("تم تصفير العدادات")

def _status_label(uid: int) -> tuple:
    """إرجاع (نص_الحالة, has_remaining)."""
    limit = _get_user_limit(uid)
    if limit == -1:
        return "♾️", True
    count     = _get_search_count(uid)
    remaining = limit - count
    if remaining > 0:
        return f"({remaining}/{limit})", True
    return "❌", False


# ═══════════════════════════════════════════════════════════════════════════════
#  تحميل البيانات في الخلفية
# ═══════════════════════════════════════════════════════════════════════════════
def _resolve_mediafire_link(page_url: str) -> str:
    html  = requests.get(page_url, timeout=30).text
    match = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', html)
    if not match:
        match = re.search(r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']', html)
    if not match:
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
def do_search(query: str) -> list:
    if not os.path.isdir(DATA_DIR) or not os.listdir(DATA_DIR):
        return []
    try:
        proc_files = subprocess.run(
            ["grep", "-r", "-i", "-a", "--max-count=1", "-l", query, DATA_DIR],
            capture_output=True, text=True, timeout=30,
        )
        matched_files = [f.strip() for f in proc_files.stdout.split("\n") if f.strip()]
        if not matched_files:
            return []
        results = []
        for filepath in matched_files[:MAX_RESULTS]:
            try:
                proc2 = subprocess.run(
                    ["grep", "-i", "-a", "-m", "3", query, filepath],
                    capture_output=True, text=True, timeout=10,
                )
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
        return ["⏰ انتهت مهلة البحث (30 ثانية)"]
    except Exception as exc:
        log.exception("خطأ في البحث: %s", exc)
        return []

def get_data_status() -> dict:
    if not os.path.isdir(DATA_DIR):
        return {"exists": False, "files": 0, "size_mb": 0, "exts": []}
    all_files = []
    for root, _, files in os.walk(DATA_DIR):
        for f in files:
            all_files.append(os.path.join(root, f))
    exts = list({os.path.splitext(f)[1] or "(بدون امتداد)" for f in all_files[:50]})
    total_size = sum(os.path.getsize(f) for f in all_files if os.path.isfile(f))
    return {
        "exists":   True,
        "files":    len(all_files),
        "size_mb":  round(total_size / (1024 * 1024), 2),
        "exts":     exts[:6],
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

def _format_result(line: str) -> str:
    """
    تنسيق سطر النتيجة:
    - روابط Twitter/X → زر جميل
    - روابط عامة → قابلة للنقر
    - النص الأصلي → كود
    """
    # فصل الروابط عن النص
    twitter_pattern = r'(https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+)'
    url_pattern     = r'(https?://[^\s<>]+)'

    twitter_links = re.findall(twitter_pattern, line)
    clean_line    = re.sub(twitter_pattern, '', line).strip()
    clean_line    = re.sub(url_pattern,     '', clean_line).strip()

    parts = []
    if clean_line:
        parts.append(f"<code>{clean_line}</code>")
    for url in twitter_links:
        username = re.search(r'(?:twitter\.com|x\.com)/([^/?#\s]+)', url)
        label    = f"@{username.group(1)}" if username else "Twitter"
        parts.append(f'🐦 <a href="{url}">{label}</a>')
    # روابط عامة
    remaining_urls = re.findall(url_pattern, line)
    seen = set(twitter_links)
    for url in remaining_urls:
        if url not in seen:
            parts.append(f'🔗 <a href="{url}">{url}</a>')
            seen.add(url)
    return "\n".join(parts) if parts else f"<code>{line}</code>"


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
    kb           = InlineKeyboardMarkup(row_width=1)
    status, has  = _status_label(uid)
    action       = "mode_{}" if has else "no_action"

    kb.add(
        InlineKeyboardButton(
            t(uid, f"🔍 بحث كامل {status}", f"🔍 Full Search {status}"),
            callback_data=action.format("full") if has else "no_action",
        ),
        InlineKeyboardButton(
            t(uid, f"🔎 بحث منفصل {status}", f"🔎 Split Search {status}"),
            callback_data=action.format("split") if has else "no_action",
        ),
        InlineKeyboardButton(
            t(uid, f"📧 بحث إيميل {status}", f"📧 Email Search {status}"),
            callback_data=action.format("email") if has else "no_action",
        ),
        InlineKeyboardButton(t(uid, "🌐 تغيير اللغة",      "🌐 Change Language"), callback_data="change_lang"),
        InlineKeyboardButton(t(uid, "📖 تعليمات الاستخدام", "📖 How to Use"),     callback_data="show_help"),
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
    """لوحة مفاتيح القبول مع اختيار الحصة."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ 2/يوم",     callback_data=f"approve_{uid}_2"),
        InlineKeyboardButton("✅ 3/يوم",     callback_data=f"approve_{uid}_3"),
        InlineKeyboardButton("✅ 4/يوم",     callback_data=f"approve_{uid}_4"),
        InlineKeyboardButton("✅ ♾️ مفتوح",  callback_data=f"approve_{uid}_-1"),
        InlineKeyboardButton("❌ رفض",       callback_data=f"reject_{uid}"),
    )
    return kb

def quota_change_kb(target_uid: int) -> InlineKeyboardMarkup:
    """تغيير حصة مستخدم من بانل الإدمن."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("2️⃣ 2/يوم",    callback_data=f"chquota_{target_uid}_2"),
        InlineKeyboardButton("3️⃣ 3/يوم",    callback_data=f"chquota_{target_uid}_3"),
        InlineKeyboardButton("4️⃣ 4/يوم",    callback_data=f"chquota_{target_uid}_4"),
        InlineKeyboardButton("♾️ مفتوح",    callback_data=f"chquota_{target_uid}_-1"),
        InlineKeyboardButton("🗑️ إزالة",    callback_data=f"rmuser_{target_uid}"),
        InlineKeyboardButton("↩️ رجوع",      callback_data="admin_users"),
    )
    return kb

def admin_panel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📊 الإحصائيات",              callback_data="admin_stats"),
        InlineKeyboardButton("🔄 تصفير العدادات",           callback_data="admin_reset"),
        InlineKeyboardButton("📝 تغيير رسالة الترحيب",      callback_data="admin_welcome"),
        InlineKeyboardButton("👥 إدارة المستخدمين",         callback_data="admin_users"),
        InlineKeyboardButton("⏳ الطلبات المعلّقة",         callback_data="admin_pending"),
        InlineKeyboardButton("↩️ رجوع",                     callback_data="back_menu"),
    )
    return kb


# ═══════════════════════════════════════════════════════════════════════════════
#  /status و /help
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        return
    s    = get_data_status()
    exts = ", ".join(s["exts"]) if s.get("exts") else "غير معروف"
    today_searches = sum(1 for e in user_search_count.values() if e.get("date") == _get_today())
    text = (
        f"📊 <b>حالة البيانات</b>\n\n"
        f"{'✅' if is_ready else '⏳'} جاهز: {is_ready}\n"
        f"📁 ملفات: {s.get('files', 0)}\n"
        f"💾 الحجم: {s.get('size_mb', 0)} MB\n"
        f"📄 الامتدادات: {exts}\n\n"
        f"👥 مستخدمون: {len(allowed_users)}\n"
        f"🔍 بحث اليوم: {today_searches}"
    )
    await msg.answer(text, parse_mode="HTML")


@dp.message_handler(commands=["help"])
async def cmd_help(msg: types.Message) -> None:
    uid = msg.from_user.id
    if not is_allowed(uid):
        await msg.answer("⛔ ليس لديك صلاحية. أرسل /start لطلب الوصول.")
        return
    limit = _get_user_limit(uid)
    limit_text = "♾️ غير محدود" if limit == -1 else f"{limit} يومياً"
    text = t(
        uid,
        (
            f"📖 <b>تعليمات الاستخدام</b>\n\n"
            f"🔍 <b>أنواع البحث:</b>\n"
            f"• <b>بحث كامل</b> — يبحث في كامل السطر\n"
            f"• <b>بحث منفصل</b> — تختار حقلاً (يوزر/إيميل/هاتف/آيدي)\n"
            f"• <b>بحث إيميل</b> — مخصص للبريد الإلكتروني\n\n"
            f"💡 <b>نصائح:</b>\n"
            f"• اكتب <b>جزءاً</b> من الكلمة للحصول على نتائج\n"
            f"• لا تضع @ في اليوزر\n"
            f"• البحث لا يفرّق بين الكبير والصغير\n\n"
            f"📊 حصتك: <b>{limit_text}</b>\n"
            f"⌨️ اكتب <code>menu</code> لفتح القائمة"
        ),
        (
            f"📖 <b>How to Use</b>\n\n"
            f"🔍 <b>Search Types:</b>\n"
            f"• <b>Full Search</b> — entire line\n"
            f"• <b>Split Search</b> — pick a field\n"
            f"• <b>Email Search</b> — email lookup\n\n"
            f"💡 <b>Tips:</b>\n"
            f"• Type <b>part</b> of a word\n"
            f"• No @ before usernames\n"
            f"• Case-insensitive\n\n"
            f"📊 Your quota: <b>{limit_text}</b>\n"
            f"⌨️ Type <code>menu</code> anytime"
        ),
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=main_menu(uid))


# ═══════════════════════════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message) -> None:
    uid   = msg.from_user.id
    name  = msg.from_user.full_name
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "لا يوجد"

    if is_admin(uid):
        user_lang.setdefault(uid, "ar")
        await msg.answer(f"👋 أهلاً {name}!\n\nاختر اللغة / Choose language:", reply_markup=lang_keyboard())
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
            f"👤 {name}\n🔗 {uname}\n🆔 <code>{uid}</code>\n\n"
            f"اختر الحصة اليومية للمستخدم:",
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
#  القبول / الرفض (مع اختيار الحصة)
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("approve_") or c.data.startswith("reject_"))
async def cb_approve_reject(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return

    parts     = call.data.split("_")
    action    = parts[0]
    target_id = int(parts[1])
    info      = pending_users.pop(target_id, {})
    name      = info.get("name", str(target_id))
    uname     = info.get("username", "")

    if action == "approve":
        quota = int(parts[2]) if len(parts) > 2 else DEFAULT_LIMIT
        allowed_users.add(target_id)
        user_quota[target_id] = quota
        user_lang[target_id]  = "ar"
        save_users()

        quota_text = "♾️ غير محدود" if quota == -1 else f"{quota} بحث/يوم"
        try:
            await call.message.edit_text(
                f"✅ تمت الموافقة على {name} ({uname})\n📊 الحصة: {quota_text}",
                parse_mode="HTML",
            )
        except MessageNotModified:
            pass
        try:
            await bot.send_message(
                target_id,
                f"{welcome_message}\n\n📊 حصتك: <b>{quota_text}</b>",
                parse_mode="HTML",
                reply_markup=lang_keyboard(),
            )
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
        await call.answer("⛔", show_alert=True); return
    try:
        await call.message.edit_text("⚙️ <b>بانل التحكم</b>\n\nاختر الخيار:", parse_mode="HTML", reply_markup=admin_panel_kb())
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_stats")
async def cb_admin_stats(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    today_searches = sum(1 for e in user_search_count.values() if e.get("date") == _get_today())
    text = (
        f"📊 <b>الإحصائيات</b>\n\n"
        f"👥 المستخدمون: {len(allowed_users)}\n"
        f"⏳ معلّقون: {len(pending_users)}\n"
        f"🔍 بحث اليوم: {today_searches}\n"
        f"📅 {_get_today()}"
    )
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_kb())
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_reset")
async def cb_admin_reset(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    _reset_counts()
    try:
        await call.message.edit_text("✅ تم تصفير العدادات اليومية.", reply_markup=admin_panel_kb())
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_welcome")
async def cb_admin_welcome(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    user_mode[call.from_user.id] = "set_welcome"
    await call.message.answer(
        f"📝 أرسل رسالة الترحيب الجديدة:\n(الحالية: <code>{welcome_message}</code>)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("↩️ إلغاء", callback_data="admin_panel")),
    )
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_users")
async def cb_admin_users(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    if len(allowed_users) == 0:
        text = "👥 لا يوجد مستخدمون."
    else:
        lines = []
        for u in allowed_users:
            lim = _get_user_limit(u)
            lim_str = "♾️" if lim == -1 else str(lim)
            tag = " (إدمن)" if u == ADMIN_ID else ""
            lines.append(f"• <code>{u}</code>{tag} — {lim_str}/يوم")
        text = f"👥 <b>المستخدمون ({len(allowed_users)})</b>\n\n" + "\n".join(lines)
        text += "\n\n💡 اضغط على الآيدي لتعديل الحصة:"

    kb = InlineKeyboardMarkup(row_width=1)
    for u in allowed_users:
        if u == ADMIN_ID:
            continue
        lim     = _get_user_limit(u)
        lim_str = "♾️" if lim == -1 else str(lim)
        kb.add(InlineKeyboardButton(f"🔧 {u} — {lim_str}/يوم", callback_data=f"manageuser_{u}"))
    kb.add(InlineKeyboardButton("↩️ رجوع", callback_data="admin_panel"))

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("manageuser_"))
async def cb_manage_user(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    target_uid = int(call.data.split("_")[1])
    lim        = _get_user_limit(target_uid)
    lim_str    = "♾️ غير محدود" if lim == -1 else f"{lim}/يوم"
    try:
        await call.message.edit_text(
            f"🔧 <b>إدارة المستخدم</b>\n\n🆔 <code>{target_uid}</code>\n📊 الحصة الحالية: {lim_str}\n\nاختر الإجراء:",
            parse_mode="HTML",
            reply_markup=quota_change_kb(target_uid),
        )
    except MessageNotModified:
        pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("chquota_"))
async def cb_change_quota(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    _, target_uid, quota_val = call.data.split("_")
    target_uid = int(target_uid)
    quota      = int(quota_val)
    user_quota[target_uid] = quota
    save_users()
    quota_str = "♾️ غير محدود" if quota == -1 else f"{quota}/يوم"
    await call.answer(f"✅ تم تغيير الحصة إلى {quota_str}", show_alert=True)
    try:
        await bot.send_message(
            target_uid,
            f"📊 تم تحديث حصتك اليومية إلى: <b>{quota_str}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
    # إعادة عرض قائمة المستخدمين
    await cb_admin_users(call)

@dp.callback_query_handler(lambda c: c.data.startswith("rmuser_"))
async def cb_remove_user(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    target_uid = int(call.data.split("_")[1])
    allowed_users.discard(target_uid)
    user_quota.pop(target_uid, None)
    save_users()
    await call.answer(f"✅ تم إزالة المستخدم {target_uid}", show_alert=True)
    try:
        await bot.send_message(target_uid, "❌ تم إلغاء وصولك من قِبل المشرف.")
    except Exception:
        pass
    await cb_admin_users(call)

@dp.callback_query_handler(lambda c: c.data == "admin_pending")
async def cb_admin_pending(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    if not pending_users:
        text = "✅ لا توجد طلبات معلّقة."
        kb   = InlineKeyboardMarkup().add(InlineKeyboardButton("↩️ رجوع", callback_data="admin_panel"))
    else:
        lines = [f"• <code>{uid}</code> — {d['name']}" for uid, d in pending_users.items()]
        text  = f"⏳ <b>الطلبات المعلّقة ({len(pending_users)})</b>\n\n" + "\n".join(lines)
        kb    = admin_panel_kb()
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
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
        await call.answer("⛔", show_alert=True); return

    if call.data == "change_lang":
        try:
            await call.message.edit_text("🌐 اختر اللغة / Choose language:", reply_markup=lang_keyboard())
        except MessageNotModified:
            pass
        await call.answer(); return

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
#  رجوع للقائمة
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "back_menu")
async def cb_back_menu(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True); return
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
        await call.answer("⛔", show_alert=True); return
    limit     = _get_user_limit(uid)
    lim_text  = "♾️ غير محدود" if limit == -1 else f"{limit} يومياً"
    text = t(
        uid,
        (
            f"📖 <b>تعليمات الاستخدام</b>\n\n"
            f"🔍 <b>أنواع البحث:</b>\n"
            f"• <b>بحث كامل</b> — يبحث في كامل السطر\n"
            f"• <b>بحث منفصل</b> — تختار حقلاً (يوزر/إيميل/هاتف/آيدي)\n"
            f"• <b>بحث إيميل</b> — مخصص للبريد الإلكتروني\n\n"
            f"💡 <b>نصائح:</b>\n"
            f"• اكتب <b>جزءاً</b> من الكلمة\n"
            f"• لا تضع @ في اليوزر\n"
            f"• البحث لا يفرّق بين الكبير والصغير\n\n"
            f"📊 حصتك: <b>{lim_text}</b>\n"
            f"⌨️ اكتب <code>menu</code> لفتح القائمة"
        ),
        (
            f"📖 <b>How to Use</b>\n\n"
            f"🔍 <b>Search Types:</b>\n"
            f"• <b>Full Search</b> — entire line\n"
            f"• <b>Split Search</b> — pick a field\n"
            f"• <b>Email Search</b> — email lookup\n\n"
            f"💡 <b>Tips:</b>\n"
            f"• Type <b>part</b> of a word\n"
            f"• No @ before usernames\n"
            f"• Case-insensitive\n\n"
            f"📊 Your quota: <b>{lim_text}</b>\n"
            f"⌨️ Type <code>menu</code> anytime"
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
        await call.answer("⛔", show_alert=True); return
    if not _has_quota(uid):
        await call.answer(t(uid, "❌ انتهت حصتك اليومية", "❌ Daily limit reached"), show_alert=True); return

    mode = call.data.split("_")[1]
    prompts = {
        "full":  t(uid, "🔍 أرسل الكلمة أو النص للبحث:", "🔍 Send the keyword to search:"),
        "split": t(uid, "اختر الحقل الذي تريد البحث فيه:", "Choose the field to search in:"),
        "email": t(uid, "📧 أرسل الإيميل أو جزء منه:", "📧 Send the email or part of it:"),
    }

    if mode == "split":
        user_mode[uid] = "split_pending"
        kb = split_field_kb(uid)
    else:
        user_mode[uid] = mode
        kb = _back_kb(uid)

    try:
        await call.message.edit_text(prompts.get(mode, ""), reply_markup=kb)
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
        await call.answer("⛔", show_alert=True); return
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
            await msg.answer(t(uid, "اختر نوع البحث:", "Choose search type:"), reply_markup=main_menu(uid))
        return

    # ─── تغيير رسالة الترحيب ──────────────────────────────────────────────────
    if is_admin(uid) and user_mode.get(uid) == "set_welcome":
        global welcome_message
        welcome_message = text
        user_mode.pop(uid, None)
        await msg.answer(
            f"✅ تم تحديث رسالة الترحيب:\n<code>{welcome_message}</code>",
            parse_mode="HTML", reply_markup=main_menu(uid),
        )
        return

    # ─── تحقق من الصلاحية ─────────────────────────────────────────────────────
    if not is_allowed(uid):
        await msg.answer("⛔ ليس لديك صلاحية. أرسل /start لطلب الوصول.")
        return

    mode = user_mode.get(uid)
    if not mode or mode == "split_pending":
        await msg.answer(t(uid, "اختر نوع البحث أولاً:", "Choose a search type first:"), reply_markup=main_menu(uid))
        return

    # ─── تحقق من الحصة ────────────────────────────────────────────────────────
    if not _has_quota(uid):
        await msg.answer(t(uid, "❌ انتهت حصتك اليومية.", "❌ Daily limit reached."), reply_markup=main_menu(uid))
        return

    # ─── تنظيف المدخل ─────────────────────────────────────────────────────────
    if not sanitize(text):
        await msg.answer(t(uid, "⚠️ الاستعلام يحتوي على رموز غير مسموح بها.", "⚠️ Query contains invalid characters."))
        return

    # ─── التحقق من البيانات ───────────────────────────────────────────────────
    if not is_ready:
        await msg.answer(t(uid, "⏳ البيانات لا تزال تُحمَّل...", "⏳ Data is still loading..."))
        return

    # ─── تنفيذ البحث ──────────────────────────────────────────────────────────
    wait_msg = await msg.answer(t(uid, "🔍 جارٍ البحث...", "🔍 Searching..."))
    results  = do_search(text)
    _increment_search(uid)

    limit     = _get_user_limit(uid)
    count     = _get_search_count(uid)
    remaining = (limit - count) if limit != -1 else -1
    if remaining == -1:
        quota_line = t(uid, "\n\n🔢 حصة: ♾️ غير محدود", "\n\n🔢 Quota: ♾️ Unlimited")
    else:
        quota_line = t(uid, f"\n\n🔢 متبقي: {remaining}/{limit} بحث اليوم",
                            f"\n\n🔢 Remaining: {remaining}/{limit} today")

    if not results:
        await wait_msg.edit_text(
            t(uid, f"❌ لم يتم العثور على نتائج.{quota_line}", f"❌ No results found.{quota_line}"),
            reply_markup=main_menu(uid),
        )
        user_mode.pop(uid, None)
        return

    # ─── تنسيق النتائج ────────────────────────────────────────────────────────
    header    = t(uid, f"✅ النتائج ({len(results)}):", f"✅ Results ({len(results)}):")
    body      = "\n\n".join(_format_result(r) for r in results)
    full_text = f"{header}\n\n{body}{quota_line}"

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
