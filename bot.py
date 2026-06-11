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

API_TOKEN            = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID             = 8506955611
DATA_DIR             = "data_files"
ZIP_PATH             = "temp.zip"
MEDIAFIRE_PAGE_URL   = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"
STATS_FILE           = "bot_stats.json"
MAX_SEARCHES_PER_DAY = 2
MAX_RESULTS          = 15

allowed_users        = {ADMIN_ID}
pending_users        = {}
is_ready             = False
download_lock        = threading.Lock()
user_mode            = {}
user_lang            = {}
user_search_count    = {}
welcome_message      = "👋 أهلاً بك في البوت! اختر نوع البحث."

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
dp  = Dispatcher(bot)

def _get_today():
    return datetime.now().strftime("%Y-%m-%d")

def _increment_search(uid):
    today = _get_today()
    if uid not in user_search_count:
        user_search_count[uid] = {"date": today, "count": 0}
    entry = user_search_count[uid]
    if entry["date"] != today:
        entry["date"]  = today
        entry["count"] = 0
    entry["count"] += 1
    return entry["count"]

def _get_search_count(uid):
    today = _get_today()
    entry = user_search_count.get(uid, {})
    if entry.get("date") != today:
        return 0
    return entry.get("count", 0)

def _reset_counts():
    global user_search_count
    user_search_count = {}

def _resolve_mediafire_link(page_url):
    html  = requests.get(page_url, timeout=30).text
    match = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', html)
    if not match:
        match = re.search(r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']', html)
    if not match:
        match = re.search(r'"(https://download\d+\.mediafire\.com/[^"]+)"', html)
    if not match:
        raise ValueError("تعذّر العثور على رابط التحميل.")
    return match.group(1)

def download_background():
    global is_ready
    with download_lock:
        if is_ready:
            return
        if os.path.isdir(DATA_DIR) and os.listdir(DATA_DIR):
            is_ready = True
            return
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            direct_url = _resolve_mediafire_link(MEDIAFIRE_PAGE_URL)
            with requests.get(direct_url, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(ZIP_PATH, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
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

def is_allowed(uid):
    return uid in allowed_users

def is_admin(uid):
    return uid == ADMIN_ID

def t(uid, ar, en):
    return ar if user_lang.get(uid, "ar") == "ar" else en

def sanitize(q):
    return not any(c in q for c in ("'", '"', ";", "&", "|", "`", "$", "\\", "\n"))

def _back_kb(uid):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t(uid, "↩️ رجوع للقائمة", "↩️ Back"), callback_data="back_menu"))
    return kb

def _format_links(text):
    text = re.sub(r'(https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+)', r'<a href="\1">🔗 رابط تويتر</a>', text)
    text = re.sub(r'(https?://[^\s<>]+)', r'<a href="\1">\1</a>', text)
    return text

def do_search(query, mode):
    if not os.path.isdir(DATA_DIR) or not os.listdir(DATA_DIR):
        return []
    try:
        cmd  = ["grep", "-r", "-i", "-a", "--include=*.txt", "-m", str(MAX_RESULTS), query, DATA_DIR]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = [l.strip() for l in proc.stdout.split("\n") if l.strip()]
        cleaned = []
        for line in lines:
            if ":" in line:
                parts = line.split(":", 1)
                cleaned.append(parts[1] if len(parts) > 1 else line)
            else:
                cleaned.append(line)
        return cleaned[:MAX_RESULTS]
    except subprocess.TimeoutExpired:
        return ["⏰ انتهت مهلة البحث"]
    except Exception:
        return []

def lang_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
           InlineKeyboardButton("🇬🇧 English",  callback_data="lang_en"))
    return kb

def main_menu(uid):
    kb        = InlineKeyboardMarkup(row_width=1)
    count     = _get_search_count(uid)
    remaining = MAX_SEARCHES_PER_DAY - count
    status    = f"({remaining}/{MAX_SEARCHES_PER_DAY})" if remaining > 0 else "❌ انتهت الحد"
    kb.add(
        InlineKeyboardButton(t(uid, f"🔍 بحث كامل {status}", f"🔍 Full Search {status}"),
                             callback_data="mode_full" if remaining > 0 else "no_action"),
        InlineKeyboardButton(t(uid, f"🔎 بحث منفصل {status}", f"🔎 Split Search {status}"),
                             callback_data="mode_split" if remaining > 0 else "no_action"),
        InlineKeyboardButton(t(uid, f"📧 بحث إيميل {status}", f"📧 Email Search {status}"),
                             callback_data="mode_email" if remaining > 0 else "no_action"),
        InlineKeyboardButton(t(uid, "🌐 تغيير اللغة", "🌐 Change Language"), callback_data="change_lang"),
    )
    if is_admin(uid):
        kb.add(InlineKeyboardButton("⚙️ بانل الإدمن", callback_data="admin_panel"))
    return kb

def split_field_kb(uid):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(t(uid, "👤 اليوزر",     "👤 Username"), callback_data="field_user"),
        InlineKeyboardButton(t(uid, "📧 الإيميل",    "📧 Email"),    callback_data="field_email"),
        InlineKeyboardButton(t(uid, "📱 رقم الهاتف", "📱 Phone"),    callback_data="field_phone"),
        InlineKeyboardButton(t(uid, "🆔 الآيدي",     "🆔 ID"),       callback_data="field_id"),
        InlineKeyboardButton(t(uid, "↩️ رجوع",       "↩️ Back"),     callback_data="back_menu"),
    )
    return kb

def approve_kb(uid):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("✅ قبول", callback_data=f"approve_{uid}"),
           InlineKeyboardButton("❌ رفض",  callback_data=f"reject_{uid}"))
    return kb

def admin_panel_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📊 الإحصائيات",            callback_data="admin_stats"),
        InlineKeyboardButton("🔄 تصفير العدادات",         callback_data="admin_reset"),
        InlineKeyboardButton("📝 تغيير رسالة الترحيب",    callback_data="admin_welcome"),
        InlineKeyboardButton("👥 المستخدمون المسموح لهم", callback_data="admin_users"),
        InlineKeyboardButton("⏳ الطلبات المعلّقة",       callback_data="admin_pending"),
        InlineKeyboardButton("↩️ رجوع",                   callback_data="back_menu"),
    )
    return kb

@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
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
        await msg.answer(f"⏳ تم إرسال طلب الوصول.\n🆔 معرّفك: <code>{uid}</code>", parse_mode="HTML")
        await bot.send_message(ADMIN_ID,
            f"🔔 <b>طلب وصول جديد</b>\n👤 {name}\n🔗 {uname}\n🆔 <code>{uid}</code>",
            parse_mode="HTML", reply_markup=approve_kb(uid))
    else:
        await msg.answer(f"⏳ طلبك قيد الانتظار.\n🆔 معرّفك: <code>{uid}</code>", parse_mode="HTML")

@dp.callback_query_handler(lambda c: c.data.startswith("approve_") or c.data.startswith("reject_"))
async def cb_approve_reject(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
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
        except MessageNotModified: pass
        try:
            await bot.send_message(target_id, welcome_message, reply_markup=lang_keyboard())
        except Exception: pass
    else:
        try:
            await call.message.edit_text(f"❌ تم رفض {name} ({uname})", parse_mode="HTML")
        except MessageNotModified: pass
        try:
            await bot.send_message(target_id, "❌ تم رفض طلبك من قِبل المشرف.")
        except Exception: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_panel")
async def cb_admin_panel(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    try:
        await call.message.edit_text("⚙️ <b>بانل التحكم</b>\n\nاختر الخيار:", parse_mode="HTML", reply_markup=admin_panel_kb())
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_stats")
async def cb_admin_stats(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    today_searches = sum(1 for e in user_search_count.values() if e.get("date") == _get_today())
    text = (f"📊 <b>الإحصائيات</b>\n\n"
            f"👥 المستخدمون: {len(allowed_users)}\n"
            f"⏳ معلّقون: {len(pending_users)}\n"
            f"🔍 بحث اليوم: {today_searches}\n"
            f"📅 {_get_today()}")
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_kb())
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_reset")
async def cb_admin_reset(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    _reset_counts()
    try:
        await call.message.edit_text("✅ تم تصفير العدادات.", reply_markup=admin_panel_kb())
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_welcome")
async def cb_admin_welcome(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    user_mode[call.from_user.id] = "set_welcome"
    await call.message.answer(
        f"📝 أرسل رسالة الترحيب الجديدة:\n(الحالية: <code>{welcome_message}</code>)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("↩️ إلغاء", callback_data="admin_panel")))
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_users")
async def cb_admin_users(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    ids = "\n".join(f"• <code>{u}</code>" for u in allowed_users)
    try:
        await call.message.edit_text(f"👥 <b>المستخدمون ({len(allowed_users)})</b>\n\n{ids}", parse_mode="HTML", reply_markup=admin_panel_kb())
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin_pending")
async def cb_admin_pending(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True); return
    if not pending_users:
        text = "✅ لا توجد طلبات معلّقة."
    else:
        lines = [f"• <code>{uid}</code> — {d['name']}" for uid, d in pending_users.items()]
        text  = f"⏳ <b>الطلبات المعلّقة ({len(pending_users)})</b>\n\n" + "\n".join(lines)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_kb())
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("lang_") or c.data == "change_lang")
async def cb_language(call: types.CallbackQuery):
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True); return
    if call.data == "change_lang":
        try:
            await call.message.edit_text("🌐 اختر اللغة / Choose language:", reply_markup=lang_keyboard())
        except MessageNotModified: pass
        await call.answer(); return
    user_lang[uid] = call.data.split("_")[1]
    try:
        await call.message.edit_text(
            t(uid, "✅ تم اختيار العربية.\n\nاختر نوع البحث:", "✅ English selected.\n\nChoose search type:"),
            reply_markup=main_menu(uid))
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "back_menu")
async def cb_back_menu(call: types.CallbackQuery):
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True); return
    user_mode.pop(uid, None)
    try:
        await call.message.edit_text(t(uid, "اختر نوع البحث:", "Choose search type:"), reply_markup=main_menu(uid))
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "no_action")
async def cb_no_action(call: types.CallbackQuery):
    await call.answer(t(call.from_user.id, "❌ انتهت الحد اليومي للبحث", "❌ Daily limit reached"), show_alert=True)

@dp.callback_query_handler(lambda c: c.data.startswith("mode_"))
async def cb_mode(call: types.CallbackQuery):
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True); return
    if _get_search_count(uid) >= MAX_SEARCHES_PER_DAY:
        await call.answer(t(uid, "❌ انتهت حصتك اليومية", "❌ Daily limit reached"), show_alert=True); return
    mode = call.data.split("_")[1]
    if mode == "split":
        user_mode[uid] = "split_pending"
        try:
            await call.message.edit_text(t(uid, "اختر الحقل:", "Choose the field:"), reply_markup=split_field_kb(uid))
        except MessageNotModified: pass
    elif mode == "full":
        user_mode[uid] = "full"
        try:
            await call.message.edit_text(t(uid, "🔍 أرسل نص البحث:", "🔍 Send search keyword:"), reply_markup=_back_kb(uid))
        except MessageNotModified: pass
    elif mode == "email":
        user_mode[uid] = "email"
        try:
            await call.message.edit_text(t(uid, "📧 أرسل الإيميل:", "📧 Send the email:"), reply_markup=_back_kb(uid))
        except MessageNotModified: pass
    await call.answer()

FIELD_MAP = {
    "field_user":  ("user",  "اليوزر",     "Username"),
    "field_email": ("email", "الإيميل",    "Email"),
    "field_phone": ("phone", "رقم الهاتف", "Phone"),
    "field_id":    ("id",    "الآيدي",     "ID"),
}

@dp.callback_query_handler(lambda c: c.data in FIELD_MAP)
async def cb_field(call: types.CallbackQuery):
    uid = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True); return
    field, ar_name, en_name = FIELD_MAP[call.data]
    user_mode[uid] = f"split_{field}"
    try:
        await call.message.edit_text(t(uid, f"🔍 أرسل {ar_name} للبحث:", f"🔍 Send {en_name} to search:"), reply_markup=_back_kb(uid))
    except MessageNotModified: pass
    await call.answer()

@dp.message_handler()
async def handle_text(msg: types.Message):
    uid  = msg.from_user.id
    text = msg.text.strip() if msg.text else ""
    if not text:
        return

    if text.lower() in ("menu", "نوت", "قائمة", "/menu"):
        if is_allowed(uid):
            await msg.answer(t(uid, "اختر نوع البحث:", "Choose search type:"), reply_markup=main_menu(uid))
        return

    if is_admin(uid) and user_mode.get(uid) == "set_welcome":
        global welcome_message
        welcome_message = text
        user_mode.pop(uid, None)
        await msg.answer(f"✅ تم تحديث رسالة الترحيب:\n<code>{welcome_message}</code>", parse_mode="HTML", reply_markup=main_menu(uid))
        return

    if not is_allowed(uid):
        await msg.answer("⛔ ليس لديك صلاحية. أرسل /start لطلب الوصول.")
        return

    mode = user_mode.get(uid)
    if not mode or mode == "split_pending":
        await msg.answer(t(uid, "اختر نوع البحث أولاً:", "Choose a search type first:"), reply_markup=main_menu(uid))
        return

    if _get_search_count(uid) >= MAX_SEARCHES_PER_DAY:
        await msg.answer(t(uid, "❌ انتهت حصتك اليومية.", "❌ Daily limit reached."), reply_markup=main_menu(uid))
        return

    if not sanitize(text):
        await msg.answer(t(uid, "⚠️ الاستعلام يحتوي على رموز غير مسموح بها.", "⚠️ Query contains invalid characters."))
        return

    if not is_ready:
        await msg.answer(t(uid, "⏳ البيانات لا تزال تُحمَّل...", "⏳ Data is still loading..."))
        return

    wait_msg  = await msg.answer(t(uid, "🔍 جارٍ البحث...", "🔍 Searching..."))
    results   = do_search(text, mode)
    count     = _increment_search(uid)
    remaining = MAX_SEARCHES_PER_DAY - count
    quota     = t(uid, f"\n\n🔢 متبقي: {remaining}/{MAX_SEARCHES_PER_DAY} بحث اليوم",
                       f"\n\n🔢 Remaining: {remaining}/{MAX_SEARCHES_PER_DAY} today")

    if not results:
        await wait_msg.edit_text(t(uid, f"❌ لا توجد نتائج.{quota}", f"❌ No results found.{quota}"), reply_markup=main_menu(uid))
        user_mode.pop(uid, None)
        return

    header    = t(uid, f"✅ النتائج ({len(results)}):", f"✅ Results ({len(results)}):")
    body      = _format_links("\n".join(f"<code>{r}</code>" for r in results))
    full_text = f"{header}\n\n{body}{quota}"

    if len(full_text) > 4000:
        full_text = full_text[:3900] + "\n...(مقطوع)" + quota

    try:
        await wait_msg.edit_text(full_text, parse_mode="HTML", reply_markup=main_menu(uid))
    except Exception:
        await msg.answer(full_text, parse_mode="HTML", reply_markup=main_menu(uid))

    user_mode.pop(uid, None)

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
