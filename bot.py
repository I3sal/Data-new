import subprocess, os, re, requests, zipfile, logging, threading, json
from datetime import datetime
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import MessageNotModified, BotBlocked

# ══════════════════════════════════════════════
#  إعدادات
# ══════════════════════════════════════════════
API_TOKEN          = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID           = 8506955611
DATA_DIR           = "data_files"
ZIP_PATH           = "temp.zip"
MEDIAFIRE_URL      = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"
MAX_PER_DAY        = 2          # حد البحث اليومي للمستخدمين العاديين

# ══════════════════════════════════════════════
#  الحالة (كلها في مكان واحد)
# ══════════════════════════════════════════════
allowed_users : set[int]        = {ADMIN_ID}
pending_users : dict[int, dict] = {}          # uid -> {name, username}
user_lang     : dict[int, str]  = {}          # uid -> "ar"|"en"
user_mode     : dict[int, str]  = {}          # uid -> "full"|"split:X"|"email"
user_counts   : dict[int, dict] = {}          # uid -> {date, count}
welcome_msg   : list[str]       = ["👋 أهلاً بك في بوت البحث!"]  # قابل للتغيير
is_ready      = False
dl_lock       = threading.Lock()

# ══════════════════════════════════════════════
#  تسجيل
# ══════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp  = Dispatcher(bot)

# ══════════════════════════════════════════════
#  مساعدات
# ══════════════════════════════════════════════
def ar(uid): return user_lang.get(uid, "ar") == "ar"
def T(uid, a, e): return a if ar(uid) else e

def today(): return datetime.now().strftime("%Y-%m-%d")

def get_count(uid):
    e = user_counts.get(uid, {})
    return e.get("count", 0) if e.get("date") == today() else 0

def add_count(uid):
    t = today()
    e = user_counts.setdefault(uid, {"date": t, "count": 0})
    if e["date"] != t:
        e["date"], e["count"] = t, 0
    e["count"] += 1

def reset_all_counts():
    user_counts.clear()

def clean(q): return not any(c in q for c in ('"', ";", "&", "|", "`", "$", "\\", "\n"))

def is_admin(uid): return uid == ADMIN_ID
def allowed(uid): return uid in allowed_users

async def safe_send(uid, text, **kw):
    try: await bot.send_message(uid, text, **kw)
    except BotBlocked: pass
    except Exception as e: log.warning("safe_send %s: %s", uid, e)

# ══════════════════════════════════════════════
#  تحميل البيانات
# ══════════════════════════════════════════════
def resolve_mf(url):
    html = requests.get(url, timeout=30).text
    for pat in [r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']',
                r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']']:
        m = re.search(pat, html)
        if m: return m.group(1)
    raise ValueError("رابط MediaFire غير موجود")

def download_bg():
    global is_ready
    with dl_lock:
        if is_ready: return
        if os.path.isdir(DATA_DIR) and os.listdir(DATA_DIR):
            is_ready = True; return
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            link = resolve_mf(MEDIAFIRE_URL)
            log.info("بدء التحميل: %s", link)
            with requests.get(link, stream=True, timeout=180) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                done  = 0
                with open(ZIP_PATH, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk); done += len(chunk)
                        if total and done * 100 // total % 10 == 0:
                            log.info("التحميل %d%%", done * 100 // total)
            with zipfile.ZipFile(ZIP_PATH) as z: z.extractall(DATA_DIR)
            os.remove(ZIP_PATH)
            is_ready = True
            log.info("✅ البيانات جاهزة")
        except Exception as ex:
            log.exception("فشل التحميل: %s", ex)
            if os.path.exists(ZIP_PATH): os.remove(ZIP_PATH)

threading.Thread(target=download_bg, daemon=True).start()

# ══════════════════════════════════════════════
#  لوحات المفاتيح
# ══════════════════════════════════════════════
def kb_approve(uid):
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton("✅ قبول",  callback_data=f"APR_{uid}"),
          InlineKeyboardButton("❌ رفض",   callback_data=f"REJ_{uid}"))
    return k

def kb_main(uid):
    lim  = MAX_PER_DAY if not is_admin(uid) else 9999
    left = lim - get_count(uid)
    suf  = T(uid, f" ({left}/{lim})", f" ({left}/{lim})")
    ok   = left > 0
    nop  = "nop"
    k = InlineKeyboardMarkup(row_width=1)
    k.add(
        InlineKeyboardButton(T(uid, f"🔍 بحث كامل{suf}",       f"🔍 Full Search{suf}"),
                             callback_data="MODE_full"  if ok else nop),
        InlineKeyboardButton(T(uid, f"🔎 بحث منفصل{suf}",      f"🔎 Split Search{suf}"),
                             callback_data="MODE_split" if ok else nop),
        InlineKeyboardButton(T(uid, f"📧 إيميل + باسورد{suf}", f"📧 Email+Pass{suf}"),
                             callback_data="MODE_email" if ok else nop),
        InlineKeyboardButton(T(uid, "🌐 تغيير اللغة", "🌐 Change Language"),
                             callback_data="LANG"),
    )
    if is_admin(uid):
        k.add(InlineKeyboardButton("⚙️ بانل الإدمن", callback_data="ADMIN"))
    return k

def kb_split(uid):
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton(T(uid,"👤 اليوزر","👤 Username"), callback_data="FLD_user"),
          InlineKeyboardButton(T(uid,"📧 الإيميل","📧 Email"),   callback_data="FLD_email"),
          InlineKeyboardButton(T(uid,"📱 الهاتف","📱 Phone"),    callback_data="FLD_phone"),
          InlineKeyboardButton(T(uid,"🆔 الآيدي","🆔 ID"),       callback_data="FLD_id"),
          InlineKeyboardButton(T(uid,"↩️ رجوع","↩️ Back"),      callback_data="BACK"))
    return k

def kb_back(uid):
    k = InlineKeyboardMarkup()
    k.add(InlineKeyboardButton(T(uid,"↩️ رجوع للقائمة","↩️ Back"), callback_data="BACK"))
    return k

def kb_admin():
    k = InlineKeyboardMarkup(row_width=1)
    k.add(InlineKeyboardButton("📊 إحصائيات",             callback_data="ADM_stats"),
          InlineKeyboardButton("🔄 تصفير العدادات",        callback_data="ADM_reset"),
          InlineKeyboardButton("📝 تغيير رسالة الترحيب",   callback_data="ADM_setwelcome"),
          InlineKeyboardButton("👥 المستخدمون",             callback_data="ADM_users"),
          InlineKeyboardButton("⏳ الطلبات المعلّقة",       callback_data="ADM_pending"),
          InlineKeyboardButton("↩️ رجوع",                  callback_data="BACK"))
    return k

def kb_lang():
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton("🇸🇦 العربية", callback_data="SETLANG_ar"),
          InlineKeyboardButton("🇬🇧 English",  callback_data="SETLANG_en"))
    return k

# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    uid   = msg.from_user.id
    name  = msg.from_user.full_name
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "—"
    user_lang.setdefault(uid, "ar")

    if allowed(uid):
        await msg.answer(f"{welcome_msg[0]}\n\n{T(uid,'اختر نوع البحث:','Choose search type:')}",
                         reply_markup=kb_main(uid))
        return

    if uid not in pending_users:
        pending_users[uid] = {"name": name, "username": uname}
        await msg.answer(
            f"⏳ تم إرسال طلب الوصول للمشرف.\n🆔 معرّفك: <code>{uid}</code>")
        await safe_send(ADMIN_ID,
            f"🔔 <b>طلب وصول جديد</b>\n👤 {name}\n🔗 {uname}\n🆔 <code>{uid}</code>",
            reply_markup=kb_approve(uid))
    else:
        await msg.answer(f"⏳ طلبك قيد الانتظار.\n🆔 معرّفك: <code>{uid}</code>")

# ══════════════════════════════════════════════
#  قبول / رفض
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("APR_") or c.data.startswith("REJ_"))
async def cb_approve(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔", show_alert=True)
    action, uid = call.data[:3], int(call.data[4:])
    info = pending_users.pop(uid, {})
    n, u = info.get("name","؟"), info.get("username","")
    if action == "APR":
        allowed_users.add(uid)
        user_lang.setdefault(uid, "ar")
        try: await call.message.edit_text(f"✅ تمت الموافقة على {n} ({u})")
        except MessageNotModified: pass
        # أرسل الخيارات فوراً للمستخدم
        await safe_send(uid,
            f"✅ تمت الموافقة على طلبك!\n\n{welcome_msg[0]}\n\n"
            f"اختر نوع البحث:",
            reply_markup=kb_main(uid))
    else:
        try: await call.message.edit_text(f"❌ تم رفض {n} ({u})")
        except MessageNotModified: pass
        await safe_send(uid, "❌ تم رفض طلبك من المشرف.")
    await call.answer()

# ══════════════════════════════════════════════
#  BACK — رجوع للقائمة
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "BACK")
async def cb_back(call: types.CallbackQuery):
    uid = call.from_user.id
    if not allowed(uid): return await call.answer("⛔", show_alert=True)
    user_mode.pop(uid, None)
    try:
        await call.message.edit_text(
            T(uid, "اختر نوع البحث:", "Choose search type:"),
            reply_markup=kb_main(uid))
    except MessageNotModified: pass
    await call.answer()

# ══════════════════════════════════════════════
#  NOP — زر معطّل (الحد اليومي)
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "nop")
async def cb_nop(call: types.CallbackQuery):
    uid = call.from_user.id
    await call.answer(T(uid,"❌ انتهت حصتك اليومية","❌ Daily limit reached"), show_alert=True)

# ══════════════════════════════════════════════
#  LANG — تغيير اللغة
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "LANG")
async def cb_lang_menu(call: types.CallbackQuery):
    uid = call.from_user.id
    if not allowed(uid): return await call.answer("⛔", show_alert=True)
    try: await call.message.edit_text("🌐 اختر اللغة / Choose language:", reply_markup=kb_lang())
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("SETLANG_"))
async def cb_setlang(call: types.CallbackQuery):
    uid = call.from_user.id
    if not allowed(uid): return await call.answer("⛔", show_alert=True)
    user_lang[uid] = call.data.split("_")[1]
    try:
        await call.message.edit_text(
            T(uid, "✅ تم. اختر نوع البحث:", "✅ Done. Choose search type:"),
            reply_markup=kb_main(uid))
    except MessageNotModified: pass
    await call.answer()

# ══════════════════════════════════════════════
#  MODE — اختيار نوع البحث
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("MODE_"))
async def cb_mode(call: types.CallbackQuery):
    uid  = call.from_user.id
    if not allowed(uid): return await call.answer("⛔", show_alert=True)
    if not is_admin(uid) and get_count(uid) >= MAX_PER_DAY:
        return await call.answer(T(uid,"❌ انتهت حصتك اليومية","❌ Daily limit reached"), show_alert=True)
    mode = call.data[5:]
    if mode == "split":
        user_mode[uid] = "split_pending"
        txt = T(uid, "اختر الحقل:", "Choose field:")
        kb  = kb_split(uid)
    elif mode == "full":
        user_mode[uid] = "full"
        txt = T(uid, "🔍 أرسل الكلمة للبحث:", "🔍 Send keyword:")
        kb  = kb_back(uid)
    else:  # email
        user_mode[uid] = "email"
        txt = T(uid, "📧 أرسل الإيميل للبحث:", "📧 Send email:")
        kb  = kb_back(uid)
    try: await call.message.edit_text(txt, reply_markup=kb)
    except MessageNotModified: pass
    await call.answer()

# ══════════════════════════════════════════════
#  FLD — اختيار الحقل في البحث المنفصل
# ══════════════════════════════════════════════
FIELDS = {"FLD_user":"user","FLD_email":"email","FLD_phone":"phone","FLD_id":"id"}
FIELD_AR = {"user":"اليوزر","email":"الإيميل","phone":"الهاتف","id":"الآيدي"}

@dp.callback_query_handler(lambda c: c.data in FIELDS)
async def cb_field(call: types.CallbackQuery):
    uid = call.from_user.id
    if not allowed(uid): return await call.answer("⛔", show_alert=True)
    key = FIELDS[call.data]
    user_mode[uid] = f"split:{key}"
    label = T(uid, FIELD_AR[key], key.title())
    try:
        await call.message.edit_text(
            T(uid, f"🔎 بحث في [{label}]. أرسل القيمة:",
                   f"🔎 Search in [{label}]. Send value:"),
            reply_markup=kb_back(uid))
    except MessageNotModified: pass
    await call.answer()

# ══════════════════════════════════════════════
#  ADMIN — بانل الإدمن
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "ADMIN")
async def cb_admin(call: types.CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("⛔", show_alert=True)
    try: await call.message.edit_text("⚙️ <b>بانل التحكم</b>", reply_markup=kb_admin())
    except MessageNotModified: pass
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("ADM_"))
async def cb_admin_action(call: types.CallbackQuery):
    uid = call.from_user.id
    if not is_admin(uid): return await call.answer("⛔", show_alert=True)
    act = call.data[4:]

    if act == "stats":
        today_s = sum(1 for e in user_counts.values() if e.get("date") == today())
        txt = (f"📊 <b>الإحصائيات</b>\n\n"
               f"👥 المستخدمون: {len(allowed_users)}\n"
               f"⏳ المعلّقون: {len(pending_users)}\n"
               f"🔍 بحث اليوم: {today_s}\n"
               f"📅 {today()}")
        try: await call.message.edit_text(txt, reply_markup=kb_admin())
        except MessageNotModified: pass

    elif act == "reset":
        reset_all_counts()
        try: await call.message.edit_text("✅ تم تصفير العدادات.", reply_markup=kb_admin())
        except MessageNotModified: pass

    elif act == "setwelcome":
        try:
            await call.message.edit_text(
                f"📝 أرسل رسالة الترحيب الجديدة.\n\nالحالية:\n<code>{welcome_msg[0]}</code>",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("↩️ إلغاء", callback_data="ADMIN")))
        except MessageNotModified: pass
        user_mode[uid] = "set_welcome"

    elif act == "users":
        ids = "\n".join(f"• <code>{u}</code>" for u in sorted(allowed_users))
        try: await call.message.edit_text(
            f"👥 <b>المستخدمون ({len(allowed_users)})</b>\n\n{ids}", reply_markup=kb_admin())
        except MessageNotModified: pass

    elif act == "pending":
        if not pending_users:
            txt = "✅ لا توجد طلبات معلّقة."
        else:
            lines = [f"• <code>{u}</code> — {d['name']}" for u,d in pending_users.items()]
            txt = f"⏳ <b>المعلّقون ({len(pending_users)})</b>\n\n" + "\n".join(lines)
        try: await call.message.edit_text(txt, reply_markup=kb_admin())
        except MessageNotModified: pass

    await call.answer()

# ══════════════════════════════════════════════
#  أوامر المشرف النصية
# ══════════════════════════════════════════════
@dp.message_handler(commands=["adduser"])
async def cmd_adduser(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p)!=2 or not p[1].lstrip("-").isdigit():
        return await msg.reply("الاستخدام: /adduser [id]")
    new = int(p[1])
    allowed_users.add(new); user_lang.setdefault(new,"ar"); pending_users.pop(new,None)
    await msg.reply(f"✅ تمت إضافة <code>{new}</code>.")
    await safe_send(new, f"{welcome_msg[0]}\n\naختر نوع البحث:", reply_markup=kb_main(new))

@dp.message_handler(commands=["removeuser"])
async def cmd_removeuser(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p)!=2 or not p[1].lstrip("-").isdigit():
        return await msg.reply("الاستخدام: /removeuser [id]")
    allowed_users.discard(int(p[1]))
    await msg.reply(f"✅ تمت إزالة <code>{p[1]}</code>.")

@dp.message_handler(commands=["admin"])
async def cmd_admin_txt(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    await msg.answer("⚙️ <b>بانل التحكم</b>", reply_markup=kb_admin())

@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message):
    if not allowed(msg.from_user.id): return
    await msg.reply("✅ البيانات جاهزة" if is_ready else "⏳ جارٍ التحميل …")

# ══════════════════════════════════════════════
#  منطق grep
# ══════════════════════════════════════════════
def grep_full(q, n=15):
    r = subprocess.run(["grep","-rFih","--",q,DATA_DIR],
                       capture_output=True,text=True,timeout=30)
    return [l.strip() for l in r.stdout.splitlines() if l.strip()][:n]

def grep_split(q, field, n=15):
    fidx = {"user":0,"email":1,"password":2,"phone":3,"id":4}
    idx  = fidx.get(field, 0)
    r = subprocess.run(["grep","-rFih","--",q,DATA_DIR],
                       capture_output=True,text=True,timeout=30)
    res=[]
    for line in r.stdout.splitlines():
        parts = re.split(r"[:|]", line)
        if len(parts)>idx and q.lower() in parts[idx].lower():
            res.append(line.strip())
        if len(res)>=n: break
    return res

def grep_email(q, n=15):
    r = subprocess.run(["grep","-rFih","--",q,DATA_DIR],
                       capture_output=True,text=True,timeout=30)
    pat = re.compile(r"([\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,})[:|]([\S]+)")
    res=[]
    for line in r.stdout.splitlines():
        m = pat.search(line)
        if m: res.append(f"📧 {m.group(1)}\n🔑 {m.group(2)}")
        if len(res)>=n: break
    return res

# ══════════════════════════════════════════════
#  معالج النصوص الواحد والوحيد
# ══════════════════════════════════════════════
@dp.message_handler()
async def handle_text(msg: types.Message):
    uid  = msg.from_user.id
    text = msg.text.strip() if msg.text else ""
    user_lang.setdefault(uid, "ar")

    # ── المستخدمون غير المسموح لهم ──────────────
    if not allowed(uid):
        return  # تجاهل صامت

    # ── كلمة مفتاحية لفتح القائمة ───────────────
    if text.lower() in {"نوت","not","قائمة","menu","خيارات","options","start"}:
        user_mode.pop(uid, None)
        return await msg.answer(
            T(uid,"اختر نوع البحث:","Choose search type:"),
            reply_markup=kb_main(uid))

    # ── تغيير رسالة الترحيب (من بانل الإدمن) ────
    if user_mode.get(uid) == "set_welcome" and is_admin(uid):
        welcome_msg[0] = text
        user_mode.pop(uid, None)
        return await msg.reply(f"✅ تم تغيير رسالة الترحيب إلى:\n<code>{text}</code>",
                               reply_markup=kb_admin())

    # ── لم يختر وضع بحث بعد: ابدأ بحثاً كاملاً ──
    mode = user_mode.get(uid, "full")

    # لا تقبل البحث إذا لم تنته البيانات
    if not is_ready:
        return await msg.reply(T(uid,
            "⏳ البيانات لا تزال تُحمَّل، انتظر قليلاً.",
            "⏳ Data loading, please wait."))

    # الحد اليومي (الإدمن معفى)
    if not is_admin(uid) and get_count(uid) >= MAX_PER_DAY:
        return await msg.reply(T(uid,
            f"❌ انتهت حصتك اليومية ({MAX_PER_DAY} بحث).",
            f"❌ Daily limit reached ({MAX_PER_DAY} searches)."))

    query = text.lstrip("@")
    if not query: return
    if not clean(query):
        return await msg.reply(T(uid,"⚠️ رموز غير مسموح بها.","⚠️ Invalid characters."))

    # عدّاد
    if not is_admin(uid): add_count(uid)

    wait = await msg.reply(T(uid,"🔄 جارٍ البحث …","🔄 Searching …"))

    try:
        if   mode == "full":          lines = grep_full(query)
        elif mode.startswith("split:"): lines = grep_split(query, mode.split(":")[1])
        elif mode == "email":         lines = grep_email(query)
        else:                         lines = grep_full(query)
    except subprocess.TimeoutExpired:
        await wait.delete()
        return await msg.reply(T(uid,"⚠️ انتهت مهلة البحث.","⚠️ Timeout."))
    except Exception as ex:
        log.exception(ex)
        await wait.delete()
        return await msg.reply(T(uid,"⚠️ خطأ أثناء البحث.","⚠️ Search error."))

    await wait.delete()

    if not lines:
        return await msg.reply(
            T(uid,"❌ لا توجد نتائج.","❌ No results."),
            reply_markup=kb_back(uid))

    sep    = "\n\n" if mode=="email" else "\n"
    header = T(uid, f"✅ النتائج ({len(lines)}):", f"✅ Results ({len(lines)}):")

    # تجميع النص
    body = sep.join(lines)

    # إرسال — نقطيع إذا تجاوز 4096
    chunks = []
    buf = []
    for line in lines:
        buf.append(line)
        if len(sep.join(buf)) > 3600:
            chunks.append(buf[:-1])
            buf = [line]
    chunks.append(buf)

    if len(chunks) == 1:
        await msg.answer(f"{header}\n\n<code>{body}</code>",
                         reply_markup=kb_back(uid),
                         disable_web_page_preview=True)
    else:
        await msg.answer(header)
        for i, ch in enumerate(chunks):
            kb = kb_back(uid) if i == len(chunks)-1 else None
            await msg.answer(f"<code>{sep.join(ch)}</code>", reply_markup=kb,
                             disable_web_page_preview=True)

# ══════════════════════════════════════════════
#  تشغيل
# ══════════════════════════════════════════════
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
