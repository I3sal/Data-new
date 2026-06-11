import subprocess
import os
import re
import requests
import zipfile
import logging
import threading
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import MessageNotModified

# ─── إعدادات ──────────────────────────────────────────────────────────────────
API_TOKEN          = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID           = 8506955611
DATA_DIR           = "data_files"
ZIP_PATH           = "temp.zip"
MEDIAFIRE_PAGE_URL = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"

# ─── الحالة العامة ────────────────────────────────────────────────────────────
allowed_users: set[int]        = {ADMIN_ID}
pending_users: dict[int, dict] = {}   # uid -> {first_name, username}
is_ready       = False
download_lock  = threading.Lock()
user_mode: dict[int, str] = {}
user_lang: dict[int, str] = {}

# ─── تسجيل ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
dp  = Dispatcher(bot)


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

def t(uid: int, ar: str, en: str) -> str:
    return ar if user_lang.get(uid, "ar") == "ar" else en

def sanitize(q: str) -> bool:
    return not any(c in q for c in ("'", '"', ";", "&", "|", "`", "$", "\\", "\n"))

def _back_kb(uid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t(uid, "↩️ رجوع للقائمة", "↩️ Back"), callback_data="back_menu"))
    return kb


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
    kb.add(
        InlineKeyboardButton(t(uid, "🔍 بحث كامل",              "🔍 Full Search"),         callback_data="mode_full"),
        InlineKeyboardButton(t(uid, "🔎 بحث منفصل (حقل محدد)", "🔎 Split Search"),         callback_data="mode_split"),
        InlineKeyboardButton(t(uid, "📧 بحث إيميل + باسورد",   "📧 Email + Password"),     callback_data="mode_email"),
        InlineKeyboardButton(t(uid, "🌐 تغيير اللغة",           "🌐 Change Language"),       callback_data="change_lang"),
    )
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


# ═══════════════════════════════════════════════════════════════════════════════
#  /start  — نظام الموافقة
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message) -> None:
    uid  = msg.from_user.id
    name = msg.from_user.full_name
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "لا يوجد"

    # المشرف يبدأ مباشرة
    if uid == ADMIN_ID:
        user_lang.setdefault(uid, "ar")
        await msg.answer(
            f"👋 أهلاً {name}!\n\nاختر اللغة / Choose language:",
            reply_markup=lang_keyboard(),
        )
        return

    # مستخدم مسموح له مسبقاً
    if uid in allowed_users:
        await _show_welcome(msg)
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
            "⏳ طلبك قيد الانتظار، سيتم إشعارك فور الموافقة.\n"
            f"🆔 معرّفك: <code>{uid}</code>",
            parse_mode="HTML",
        )


async def _show_welcome(msg: types.Message) -> None:
    uid  = msg.from_user.id
    name = msg.from_user.full_name
    user_lang.setdefault(uid, "ar")
    await msg.answer(
        f"👋 أهلاً {name}! مرحباً بك.\n\nاختر اللغة / Choose language:",
        reply_markup=lang_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  أزرار القبول / الرفض (للمشرف فقط)
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("approve_") or c.data.startswith("reject_"))
async def cb_approve_reject(call: types.CallbackQuery) -> None:
    if call.from_user.id != ADMIN_ID:
        await call.answer("⛔ غير مصرح.", show_alert=True)
        return

    action, target_id = call.data.split("_", 1)
    target_id = int(target_id)
    info = pending_users.pop(target_id, {})
    name  = info.get("name", str(target_id))
    uname = info.get("username", "")

    if action == "approve":
        allowed_users.add(target_id)
        try:
            await call.message.edit_text(
                f"✅ تمت الموافقة على {name} ({uname}) — <code>{target_id}</code>",
                parse_mode="HTML",
            )
        except MessageNotModified:
            pass
        try:
            await bot.send_message(
                target_id,
                "✅ تمت الموافقة على طلبك!\n\nاختر اللغة / Choose language:",
                reply_markup=lang_keyboard(),
            )
        except Exception:
            pass
    else:
        try:
            await call.message.edit_text(
                f"❌ تم رفض {name} ({uname}) — <code>{target_id}</code>",
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
            t(uid,
              "✅ تم اختيار العربية.\n\nاختر نوع البحث:",
              "✅ English selected.\n\nChoose search type:"),
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


# ═══════════════════════════════════════════════════════════════════════════════
#  اختيار وضع البحث
# ═══════════════════════════════════════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("mode_"))
async def cb_mode(call: types.CallbackQuery) -> None:
    uid  = call.from_user.id
    if not is_allowed(uid):
        await call.answer("⛔", show_alert=True)
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
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await msg.reply("الاستخدام: /adduser [user_id]")
        return
    new_id = int(parts[1])
    allowed_users.add(new_id)
    pending_users.pop(new_id, None)
    await msg.reply(f"✅ تمت إضافة المستخدم <code>{new_id}</code>.", parse_mode="HTML")

@dp.message_handler(commands=["removeuser"])
async def cmd_removeuser(msg: types.Message) -> None:
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await msg.reply("الاستخدام: /removeuser [user_id]")
        return
    uid = int(parts[1])
    allowed_users.discard(uid)
    await msg.reply(f"✅ تمت إزالة المستخدم <code>{uid}</code>.", parse_mode="HTML")

@dp.message_handler(commands=["users"])
async def cmd_users(msg: types.Message) -> None:
    if msg.from_user.id != ADMIN_ID:
        return
    ids = "\n".join(f"• <code>{u}</code>" for u in allowed_users)
    await msg.reply(f"👥 المستخدمون المسموح لهم:\n{ids}", parse_mode="HTML")

@dp.message_handler(commands=["pending"])
async def cmd_pending(msg: types.Message) -> None:
    if msg.from_user.id != ADMIN_ID:
        return
    if not pending_users:
        await msg.reply("لا يوجد طلبات معلّقة.")
        return
    lines = [f"• <code>{uid}</code> — {d['name']} {d['username']}" for uid, d in pending_users.items()]
    await msg.reply("⏳ الطلبات المعلّقة:\n" + "\n".join(lines), parse_mode="HTML")

@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message) -> None:
    if not is_allowed(msg.from_user.id):
        return
    uid = msg.from_user.id
    state = t(uid, "✅ جاهزة" if is_ready else "⏳ جارٍ التحميل …",
                   "✅ Ready"  if is_ready else "⏳ Downloading …")
    await msg.reply(t(uid, f"حالة البيانات: {state}", f"Data status: {state}"))


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
#  معالج النصوص (البحث)
# ═══════════════════════════════════════════════════════════════════════════════
@dp.message_handler()
async def handle_text(msg: types.Message) -> None:
    uid = msg.from_user.id
    if not is_allowed(uid):
        return
    if uid not in user_mode:
        lang = user_lang.get(uid, "ar")
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

    query = msg.text.strip().lstrip("@")
    if not query:
        return
    if not sanitize(query):
        await msg.reply(t(uid,
            "⚠️ الاستعلام يحتوي على رموز غير مسموح بها.",
            "⚠️ Query contains disallowed characters."))
        return

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

        processed_lines = []
        for line in lines:
            p = line.split(':')
            if p and " " not in p[0] and "." not in p[0]:
                processed_lines.append(f"{line}\n🔗 https://x.com/{p[0].strip().replace('@','')}")
            else:
                processed_lines.append(line)

        sep    = "\n\n"
        output = sep.join(processed_lines)
        header = t(uid, f"✅ النتائج ({len(lines)}):", f"✅ Results ({len(lines)}):")

        full_text = f"{header}\n\n<code>{output}</code>"
        if len(full_text) <= 4096:
            await msg.answer(full_text, parse_mode="HTML", reply_markup=_back_kb(uid))
        else:
            await msg.answer(header, parse_mode="HTML")
            chunk: list[str] = []
            chunks: list[list[str]] = []
            for line in processed_lines:
                chunk.append(line)
                if len("\n".join(chunk)) > 3800:
                    chunks.append(chunk[:-1])
                    chunk = [line]
            chunks.append(chunk)
            for i, c in enumerate(chunks):
                kb = _back_kb(uid) if i == len(chunks) - 1 else None
                await msg.answer(f"<code>{chr(10).join(c)}</code>", parse_mode="HTML", reply_markup=kb)

    except subprocess.TimeoutExpired:
        await msg.reply(t(uid, "⚠️ انتهت مهلة البحث.", "⚠️ Search timed out."))
    except Exception as exc:
        log.exception("خطأ في البحث: %s", exc)
        await msg.reply(t(uid, "⚠️ حدث خطأ أثناء البحث.", "⚠️ An error occurred during search."))


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
