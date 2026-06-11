import subprocess
import os
import re
import requests
import zipfile
import logging
import threading
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ─── إعدادات ──────────────────────────────────────────────────────────────────
API_TOKEN          = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID           = 8506955611
DATA_DIR           = "data_files"
ZIP_PATH           = "temp.zip"
MEDIAFIRE_PAGE_URL = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"

# ─── الحالة ───────────────────────────────────────────────────────────────────
allowed_users: set[int] = {ADMIN_ID}
is_ready       = False
download_lock  = threading.Lock()

# وضع البحث لكل مستخدم: "full" | "split" | "email"
user_mode: dict[int, str] = {}
user_lang: dict[int, str] = {}

# ─── تسجيل ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
dp  = Dispatcher(bot)


# ─── تحميل في الخلفية ────────────────────────────────────────────────────────
def _resolve_mediafire_link(page_url: str) -> str:
    html  = requests.get(page_url, timeout=30).text
    match = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', html)
    if not match:
        match = re.search(r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']', html)
    if not match:
        raise ValueError("تعذّر العثور على رابط التحميل المباشر في صفحة MediaFire.")
    return match.group(1)


def download_background() -> None:
    global is_ready
    with download_lock:
        if is_ready:
            return
        if os.path.isdir(DATA_DIR) and os.listdir(DATA_DIR):
            log.info("المجلد موجود مسبقاً — تخطي التحميل.")
            is_ready = True
            return

        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            log.info("جارٍ تحليل رابط MediaFire …")
            direct_url = _resolve_mediafire_link(MEDIAFIRE_PAGE_URL)
            log.info("جارٍ تحميل الملف من %s …", direct_url)

            with requests.get(direct_url, stream=True, timeout=180) as r:
                r.raise_for_status()
                total      = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(ZIP_PATH, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded * 100 // total
                            if pct % 10 == 0:
                                log.info("التحميل: %d%%", pct)

            log.info("جارٍ فك الضغط …")
            with zipfile.ZipFile(ZIP_PATH, "r") as z:
                z.extractall(DATA_DIR)
            os.remove(ZIP_PATH)
            is_ready = True
            log.info("البيانات جاهزة في '%s'.", DATA_DIR)

        except Exception as exc:
            log.exception("فشل التحميل: %s", exc)
            if os.path.exists(ZIP_PATH):
                os.remove(ZIP_PATH)


threading.Thread(target=download_background, daemon=True).start()


# ─── مساعدات ─────────────────────────────────────────────────────────────────
def is_allowed(uid: int) -> bool:
    return uid in allowed_users

def t(uid: int, ar: str, en: str) -> str:
    return ar if user_lang.get(uid, "ar") == "ar" else en

def sanitize(query: str) -> bool:
    """يُعيد True إذا كان الاستعلام آمناً."""
    return not any(c in query for c in ("'", '"', ";", "&", "|", "`", "$", "\\", "\n"))


# ─── لوحات المفاتيح ───────────────────────────────────────────────────────────
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
        InlineKeyboardButton(
            t(uid, "🔍 بحث كامل (كل السطر)",           "🔍 Full Search (entire line)"),
            callback_data="mode_full"
        ),
        InlineKeyboardButton(
            t(uid, "🔎 بحث منفصل (حقل محدد)",          "🔎 Split Search (specific field)"),
            callback_data="mode_split"
        ),
        InlineKeyboardButton(
            t(uid, "📧 بحث إيميل + باسورد",             "📧 Email + Password Search"),
            callback_data="mode_email"
        ),
    )
    return kb

def split_field_keyboard(uid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(t(uid, "👤 اليوزر",    "👤 Username"), callback_data="field_user"),
        InlineKeyboardButton(t(uid, "📧 الإيميل",   "📧 Email"),    callback_data="field_email"),
        InlineKeyboardButton(t(uid, "📱 رقم الهاتف","📱 Phone"),    callback_data="field_phone"),
        InlineKeyboardButton(t(uid, "🆔 الآيدي",    "🆔 ID"),       callback_data="field_id"),
        InlineKeyboardButton(t(uid, "↩️ رجوع",      "↩️ Back"),     callback_data="back_menu"),
    )
    return kb


# ─── /start ───────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message) -> None:
    if not is_allowed(msg.from_user.id):
        return
    await msg.answer("🌐 اختر اللغة / Choose language:", reply_markup=lang_keyboard())


# ─── اختيار اللغة ────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("lang_"))
async def cb_language(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        return
    user_lang[uid] = call.data.split("_")[1]
    await call.message.edit_text(
        t(uid, "✅ تم اختيار العربية.\n\nاختر نوع البحث:", "✅ English selected.\n\nChoose search type:"),
        reply_markup=main_menu(uid),
    )
    await call.answer()


# ─── القائمة الرئيسية (رجوع) ─────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "back_menu")
async def cb_back_menu(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        return
    user_mode.pop(uid, None)
    await call.message.edit_text(
        t(uid, "اختر نوع البحث:", "Choose search type:"),
        reply_markup=main_menu(uid),
    )
    await call.answer()


# ─── اختيار وضع البحث ────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("mode_"))
async def cb_mode(call: types.CallbackQuery) -> None:
    uid  = call.from_user.id
    if not is_allowed(uid):
        return
    mode = call.data.split("_")[1]  # full | split | email

    if mode == "split":
        user_mode[uid] = "split_pending"
        await call.message.edit_text(
            t(uid, "اختر الحقل الذي تريد البحث فيه:", "Choose the field to search in:"),
            reply_markup=split_field_keyboard(uid),
        )
    elif mode == "full":
        user_mode[uid] = "full"
        await call.message.edit_text(
            t(uid, "🔍 وضع البحث الكامل.\nأرسل الكلمة أو النص للبحث:",
                   "🔍 Full Search mode.\nSend the keyword to search:"),
            reply_markup=_back_kb(uid),
        )
    elif mode == "email":
        user_mode[uid] = "email"
        await call.message.edit_text(
            t(uid, "📧 وضع بحث الإيميل + الباسورد.\nأرسل الإيميل أو جزء منه للبحث:",
                   "📧 Email + Password mode.\nSend an email or part of it to search:"),
            reply_markup=_back_kb(uid),
        )
    await call.answer()


def _back_kb(uid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(t(uid, "↩️ رجوع", "↩️ Back"), callback_data="back_menu"))
    return kb


# ─── اختيار الحقل في البحث المنفصل ──────────────────────────────────────────
FIELD_LABELS = {
    "field_user":  ("user",  "اليوزر",    "Username"),
    "field_email": ("email", "الإيميل",   "Email"),
    "field_phone": ("phone", "رقم الهاتف","Phone"),
    "field_id":    ("id",    "الآيدي",    "ID"),
}

@dp.callback_query_handler(lambda c: c.data.startswith("field_"))
async def cb_field(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if not is_allowed(uid):
        return
    key, ar_label, en_label = FIELD_LABELS[call.data]
    user_mode[uid] = f"split:{key}"
    label = t(uid, ar_label, en_label)
    await call.message.edit_text(
        t(uid, f"🔎 بحث في حقل [{label}].\nأرسل قيمة البحث:",
               f"🔎 Searching in [{label}] field.\nSend the search value:"),
        reply_markup=_back_kb(uid),
    )
    await call.answer()


# ─── أوامر المشرف ─────────────────────────────────────────────────────────────
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
    log.info("المشرف أضاف المستخدم %d", new_id)
    await msg.reply(f"✅ تمت إضافة المستخدم {new_id}.")

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
    await msg.reply(f"✅ تمت إزالة المستخدم {uid}.")

@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message) -> None:
    if not is_allowed(msg.from_user.id):
        return
    uid = msg.from_user.id
    if is_ready:
        await msg.reply(t(uid, "✅ البيانات جاهزة للبحث.", "✅ Data is ready for search."))
    else:
        await msg.reply(t(uid, "⏳ البيانات لا تزال تُحمَّل …", "⏳ Data is still downloading …"))


# ─── منطق البحث ───────────────────────────────────────────────────────────────
def grep_full(query: str, max_lines: int = 15) -> list[str]:
    """بحث في كامل محتوى كل سطر."""
    r = subprocess.run(
        ["grep", "-rFih", "--", query, DATA_DIR],
        capture_output=True, text=True, timeout=30
    )
    return r.stdout.strip().splitlines()[:max_lines]


def grep_split(query: str, field: str, max_lines: int = 15) -> list[str]:
    """
    بحث منفصل: يفترض أن السطر مفصول بـ ':' أو '|' أو '\t'.
    يُطابق الحقل المطلوب ويعرض السطر كاملاً.
    field: user | email | phone | id
    """
    # ترتيب الأعمدة المفترض (قابل للتعديل): user:email:password:phone:id
    field_index = {"user": 0, "email": 1, "password": 2, "phone": 3, "id": 4}
    idx = field_index.get(field, 0)

    r = subprocess.run(
        ["grep", "-rFih", "--", query, DATA_DIR],
        capture_output=True, text=True, timeout=30
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
    """
    بحث إيميل + باسورد:
    يبحث عن الاستعلام ويستخرج الأزواج email:password أو email|password.
    """
    r = subprocess.run(
        ["grep", "-rFih", "--", query, DATA_DIR],
        capture_output=True, text=True, timeout=30
    )
    results = []
    email_pass_re = re.compile(
        r"([\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,})[:|]([\S]+)"
    )
    for line in r.stdout.splitlines():
        m = email_pass_re.search(line)
        if m:
            results.append(f"📧 {m.group(1)}\n🔑 {m.group(2)}")
        if len(results) >= max_lines:
            break
    return results


# ─── معالج الرسائل النصية (البحث) ────────────────────────────────────────────
@dp.message_handler()
async def handle_text(msg: types.Message) -> None:
    uid = msg.from_user.id
    if not is_allowed(uid):
        return

    # إذا لم يختر المستخدم وضعاً بعد
    if uid not in user_mode:
        await msg.reply(
            t(uid, "اختر نوع البحث أولاً من القائمة:", "Choose search type from the menu:"),
            reply_markup=main_menu(uid),
        )
        return

    if not is_ready:
        await msg.reply(t(uid,
            "⚠️ البيانات لا تزال تُحمَّل، انتظر قليلاً ثم أعد المحاولة.",
            "⚠️ Data is still loading, please wait and try again."))
        return

    query = msg.text.strip().lstrip("@")
    if not query:
        return

    if not sanitize(query):
        await msg.reply(t(uid,
            "⚠️ الاستعلام يحتوي على رموز غير مسموح بها.",
            "⚠️ Query contains disallowed characters."))
        return

    mode = user_mode.get(uid, "full")

    try:
        if mode == "full":
            lines = grep_full(query)

        elif mode.startswith("split:"):
            field = mode.split(":")[1]
            lines = grep_split(query, field)

        elif mode == "email":
            lines = grep_email_pass(query)

        else:
            lines = grep_full(query)

        if not lines:
            await msg.reply(
                t(uid, "❌ لم يُعثر على نتائج.", "❌ No results found."),
                reply_markup=_back_kb(uid),
            )
            return

        count  = len(lines)
        output = "\n\n".join(lines) if mode == "email" else "\n".join(lines)

        # تقطيع الرسالة إذا تجاوزت حد تيليغرام (4096 حرف)
        header = t(uid, f"✅ النتائج ({count}):", f"✅ Results ({count}):")
        full_text = f"{header}\n\n<code>{output}</code>"

        if len(full_text) <= 4096:
            await msg.answer(full_text, parse_mode="HTML", reply_markup=_back_kb(uid))
        else:
            # إرسال على دفعات
            await msg.answer(header, parse_mode="HTML")
            chunk, chunks = [], []
            for line in lines:
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


# ─── نقطة الدخول ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
