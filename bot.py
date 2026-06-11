import subprocess, os, re, requests, zipfile, logging, threading, json
from datetime import datetime
from collections import defaultdict
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import MessageNotModified, BotBlocked

# ══════════════════════════════════════════════
#  إعدادات
# ══════════════════════════════════════════════
API_TOKEN     = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID      = 8506955611
DATA_DIR      = "data_files"
ZIP_PATH      = "temp.zip"
MEDIAFIRE_URL = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"
MAX_PER_DAY   = 2

# ══════════════════════════════════════════════
#  الحالة
# ══════════════════════════════════════════════
allowed_users : set[int]        = {ADMIN_ID}
pending_users : dict[int, dict] = {}
user_lang     : dict[int, str]  = {}
user_mode     : dict[int, str]  = {}
user_counts   : dict[int, dict] = {}
welcome_msg   : list[str]       = ["👋 أهلاً بك في بوت البحث!"]
is_ready      = False
dl_lock       = threading.Lock()

# ── الفهرس السريع ─────────────────────────────
# كل سطر مخزون في قائمة ← البحث O(n) في RAM بدل grep على القرص
index_lines   : list[str] = []          # كل السطور
index_lock    = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp  = Dispatcher(bot)

# ══════════════════════════════════════════════
#  مساعدات
# ══════════════════════════════════════════════
def ar(uid):        return user_lang.get(uid,"ar") == "ar"
def T(uid,a,e):     return a if ar(uid) else e
def today():        return datetime.now().strftime("%Y-%m-%d")
def is_admin(uid):  return uid == ADMIN_ID
def allowed(uid):   return uid in allowed_users

def get_count(uid):
    e = user_counts.get(uid,{})
    return e.get("count",0) if e.get("date")==today() else 0

def add_count(uid):
    e = user_counts.setdefault(uid,{"date":today(),"count":0})
    if e["date"]!=today(): e["date"],e["count"]=today(),0
    e["count"]+=1

def reset_all_counts(): user_counts.clear()

def clean(q): return not any(c in q for c in ('"',";","&","|","`","$","\\","\n"))

async def safe_send(uid, text, **kw):
    try: await bot.send_message(uid, text, **kw)
    except (BotBlocked, Exception) as e: log.warning("safe_send %s: %s", uid, e)

def esc(s):
    """تهريب HTML"""
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

# ══════════════════════════════════════════════
#  تحميل البيانات + بناء الفهرس
# ══════════════════════════════════════════════
def resolve_mf(url):
    html = requests.get(url, timeout=30).text
    for pat in [r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']',
                r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']']:
        m = re.search(pat, html)
        if m: return m.group(1)
    raise ValueError("رابط MediaFire غير موجود")

def build_index():
    """قراءة كل الملفات وتخزينها في الذاكرة للبحث الفوري"""
    global index_lines
    lines = []
    for root, _, files in os.walk(DATA_DIR):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            lines.append(line)
            except Exception as ex:
                log.warning("خطأ في قراءة %s: %s", fpath, ex)
    with index_lock:
        index_lines = lines
    log.info("✅ الفهرس جاهز — %d سطر", len(lines))

def download_bg():
    global is_ready
    with dl_lock:
        if is_ready: return
        # إذا الملفات موجودة فقط ابنِ الفهرس
        if os.path.isdir(DATA_DIR) and os.listdir(DATA_DIR):
            log.info("الملفات موجودة — جارٍ بناء الفهرس …")
            build_index()
            is_ready = True
            return
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            log.info("جارٍ تحليل رابط MediaFire …")
            link = resolve_mf(MEDIAFIRE_URL)
            log.info("جارٍ التحميل …")
            with requests.get(link, stream=True, timeout=300) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length",0))
                done  = 0
                with open(ZIP_PATH,"wb") as f:
                    for chunk in r.iter_content(1<<20):
                        f.write(chunk); done+=len(chunk)
                        if total and done*100//total%10==0:
                            log.info("التحميل %d%%", done*100//total)
            log.info("فك الضغط …")
            with zipfile.ZipFile(ZIP_PATH) as z: z.extractall(DATA_DIR)
            os.remove(ZIP_PATH)
            log.info("جارٍ بناء الفهرس …")
            build_index()
            is_ready = True
        except Exception as ex:
            log.exception("فشل: %s", ex)
            if os.path.exists(ZIP_PATH): os.remove(ZIP_PATH)

threading.Thread(target=download_bg, daemon=True).start()

# ══════════════════════════════════════════════
#  البحث في الفهرس (فوري — بدون grep)
# ══════════════════════════════════════════════
def search_full(q: str, n=15) -> list[str]:
    q_low = q.lower()
    with index_lock:
        return [l for l in index_lines if q_low in l.lower()][:n]

def search_split(q: str, field: str, n=15) -> list[str]:
    fidx = {"user":0,"email":1,"password":2,"phone":3,"id":4}
    idx  = fidx.get(field, 0)
    q_low = q.lower()
    res = []
    with index_lock:
        for line in index_lines:
            parts = re.split(r"[:|]", line)
            if len(parts) > idx and q_low in parts[idx].lower():
                res.append(line)
            if len(res) >= n: break
    return res

def search_email(q: str, n=15) -> list[str]:
    pat   = re.compile(r"([\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,})[:|]([\S]+)")
    q_low = q.lower()
    res   = []
    with index_lock:
        for line in index_lines:
            if q_low not in line.lower(): continue
            m = pat.search(line)
            if m: res.append(f"📧 {m.group(1)}\n🔑 {m.group(2)}")
            if len(res) >= n: break
    return res

# ══════════════════════════════════════════════
#  لوحات المفاتيح
# ══════════════════════════════════════════════
def kb_approve(uid):
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton("✅ قبول", callback_data=f"APR_{uid}"),
          InlineKeyboardButton("❌ رفض",  callback_data=f"REJ_{uid}"))
    return k

def kb_main(uid):
    lim  = 9999 if is_admin(uid) else MAX_PER_DAY
    left = lim - get_count(uid)
    suf  = f" (∞)" if is_admin(uid) else f" ({left}/{lim})"
    ok   = left > 0
    k = InlineKeyboardMarkup(row_width=1)
    k.add(
        InlineKeyboardButton(T(uid,f"🔍 بحث كامل{suf}",       f"🔍 Full Search{suf}"),
                             callback_data="MODE_full"  if ok else "nop"),
        InlineKeyboardButton(T(uid,f"🔎 بحث منفصل{suf}",      f"🔎 Split Search{suf}"),
                             callback_data="MODE_split" if ok else "nop"),
        InlineKeyboardButton(T(uid,f"📧 إيميل + باسورد{suf}", f"📧 Email+Pass{suf}"),
                             callback_data="MODE_email" if ok else "nop"),
        InlineKeyboardButton(T(uid,"🌐 تغيير اللغة","🌐 Language"), callback_data="LANG"),
        InlineKeyboardButton(T(uid,"📖 تعليمات الاستخدام","📖 Help"),   callback_data="HELP"),
    )
    if is_admin(uid):
        k.add(InlineKeyboardButton("⚙️ بانل الإدمن", callback_data="ADMIN"))
    return k

def kb_split(uid):
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton(T(uid,"👤 اليوزر","👤 Username"),  callback_data="FLD_user"),
          InlineKeyboardButton(T(uid,"📧 الإيميل","📧 Email"),    callback_data="FLD_email"),
          InlineKeyboardButton(T(uid,"📱 الهاتف","📱 Phone"),     callback_data="FLD_phone"),
          InlineKeyboardButton(T(uid,"🆔 الآيدي","🆔 ID"),        callback_data="FLD_id"),
          InlineKeyboardButton(T(uid,"↩️ رجوع","↩️ Back"),       callback_data="BACK"))
    return k

def kb_back(uid):
    k = InlineKeyboardMarkup()
    k.add(InlineKeyboardButton(T(uid,"↩️ رجوع للقائمة","↩️ Back"), callback_data="BACK"))
    return k

def kb_lang():
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton("🇸🇦 العربية", callback_data="SETLANG_ar"),
          InlineKeyboardButton("🇬🇧 English",  callback_data="SETLANG_en"))
    return k

def kb_admin():
    k = InlineKeyboardMarkup(row_width=1)
    k.add(InlineKeyboardButton("📊 إحصائيات",            callback_data="ADM_stats"),
          InlineKeyboardButton("🔄 تصفير العدادات",       callback_data="ADM_reset"),
          InlineKeyboardButton("📝 تغيير رسالة الترحيب",  callback_data="ADM_setwelcome"),
          InlineKeyboardButton("👥 المستخدمون",            callback_data="ADM_users"),
          InlineKeyboardButton("⏳ الطلبات المعلّقة",      callback_data="ADM_pending"),
          InlineKeyboardButton("↩️ رجوع",                 callback_data="BACK"))
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
        return await msg.answer(
            f"{welcome_msg[0]}\n\n{T(uid,'اختر نوع البحث:','Choose search type:')}",
            reply_markup=kb_main(uid))

    if uid not in pending_users:
        pending_users[uid] = {"name": name, "username": uname}
        await msg.answer(f"⏳ تم إرسال طلب الوصول للمشرف.\n🆔 معرّفك: <code>{uid}</code>")
        await safe_send(ADMIN_ID,
            f"🔔 <b>طلب وصول جديد</b>\n\n"
            f"👤 الاسم: {name}\n🔗 اليوزر: {uname}\n🆔 الآيدي: <code>{uid}</code>",
            reply_markup=kb_approve(uid))
    else:
        await msg.answer(f"⏳ طلبك قيد الانتظار.\n🆔 معرّفك: <code>{uid}</code>")

# ══════════════════════════════════════════════
#  قبول / رفض من زر التنبيه الفوري
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data.startswith("APR_") or c.data.startswith("REJ_"))
async def cb_approve(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔", show_alert=True)
    action = call.data[:3]
    target = int(call.data[4:])
    info   = pending_users.pop(target, {})
    n, u   = info.get("name","؟"), info.get("username","")

    if action == "APR":
        allowed_users.add(target)
        user_lang.setdefault(target, "ar")
        try: await call.message.edit_text(f"✅ تمت الموافقة على {esc(n)} ({esc(u)})")
        except MessageNotModified: pass
        await safe_send(target,
            f"✅ تمت الموافقة على طلبك!\n\n{welcome_msg[0]}\n\nاختر نوع البحث:",
            reply_markup=kb_main(target))
    else:
        try: await call.message.edit_text(f"❌ تم رفض {esc(n)} ({esc(u)})")
        except MessageNotModified: pass
        await safe_send(target, "❌ تم رفض طلبك من المشرف.")
    await call.answer()

# ══════════════════════════════════════════════
#  BACK
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "BACK")
async def cb_back(call: types.CallbackQuery):
    uid = call.from_user.id
    if not allowed(uid): return await call.answer("⛔", show_alert=True)
    user_mode.pop(uid, None)
    try:
        await call.message.edit_text(
            T(uid,"اختر نوع البحث:","Choose search type:"),
            reply_markup=kb_main(uid))
    except MessageNotModified: pass
    await call.answer()

# ══════════════════════════════════════════════
#  NOP
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "nop")
async def cb_nop(call: types.CallbackQuery):
    uid = call.from_user.id
    await call.answer(T(uid,"❌ انتهت حصتك اليومية","❌ Daily limit reached"), show_alert=True)

# ══════════════════════════════════════════════
#  LANG
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
            T(uid,"✅ تم. اختر نوع البحث:","✅ Done. Choose search type:"),
            reply_markup=kb_main(uid))
    except MessageNotModified: pass
    await call.answer()

# ══════════════════════════════════════════════
#  HELP
# ══════════════════════════════════════════════
@dp.callback_query_handler(lambda c: c.data == "HELP")
async def cb_help(call: types.CallbackQuery):
    uid = call.from_user.id
    if not allowed(uid): return await call.answer("⛔", show_alert=True)
    text = T(uid,
        "📖 <b>تعليمات الاستخدام</b>\n\n"
        "• <b>بحث كامل</b>: يبحث في كامل السطر\n"
        "• <b>بحث منفصل</b>: يبحث في حقل محدد (يوزر / إيميل / هاتف / ID)\n"
        "• <b>إيميل + باسورد</b>: يستخرج الإيميل والباسورد من النتائج\n\n"
        "💡 يمكنك الكتابة مباشرة بدون اختيار نوع (يبحث كاملاً)\n"
        f"⏳ الحد اليومي: {MAX_PER_DAY} بحث (يتجدد كل يوم)\n\n"
        "📌 الأوامر:\n/start — فتح القائمة\n/status — حالة البيانات",
        "📖 <b>How to Use</b>\n\n"
        "• <b>Full Search</b>: searches entire line\n"
        "• <b>Split Search</b>: searches specific field\n"
        "• <b>Email+Pass</b>: extracts email:password pairs\n\n"
        f"⏳ Daily limit: {MAX_PER_DAY} searches\n\n"
        "📌 Commands:\n/start — open menu\n/status — data status"
    )
    try: await call.message.edit_text(text, reply_markup=kb_back(uid))
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
        return await call.answer(T(uid,"❌ انتهت حصتك","❌ Daily limit reached"), show_alert=True)
    mode = call.data[5:]
    if mode == "split":
        user_mode[uid] = "split_pending"
        txt, kb = T(uid,"اختر الحقل:","Choose field:"), kb_split(uid)
    elif mode == "full":
        user_mode[uid] = "full"
        txt, kb = T(uid,"🔍 أرسل الكلمة:","🔍 Send keyword:"), kb_back(uid)
    else:
        user_mode[uid] = "email"
        txt, kb = T(uid,"📧 أرسل الإيميل:","📧 Send email:"), kb_back(uid)
    try: await call.message.edit_text(txt, reply_markup=kb)
    except MessageNotModified: pass
    await call.answer()

# ══════════════════════════════════════════════
#  FLD — اختيار الحقل
# ══════════════════════════════════════════════
FIELDS   = {"FLD_user":"user","FLD_email":"email","FLD_phone":"phone","FLD_id":"id"}
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
            T(uid,f"🔎 بحث في [{label}]. أرسل القيمة:",
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
        today_s = sum(1 for e in user_counts.values() if e.get("date")==today())
        txt = (f"📊 <b>الإحصائيات</b>\n\n"
               f"👥 المستخدمون: {len(allowed_users)}\n"
               f"⏳ المعلّقون: {len(pending_users)}\n"
               f"🔍 بحث اليوم: {today_s}\n"
               f"💾 الفهرس: {len(index_lines):,} سطر\n"
               f"📅 {today()}")
        try: await call.message.edit_text(txt, reply_markup=kb_admin())
        except MessageNotModified: pass

    elif act == "reset":
        reset_all_counts()
        try: await call.message.edit_text("✅ تم تصفير العدادات.", reply_markup=kb_admin())
        except MessageNotModified: pass

    elif act == "setwelcome":
        user_mode[uid] = "set_welcome"
        try:
            await call.message.edit_text(
                f"📝 أرسل رسالة الترحيب الجديدة.\n\nالحالية:\n<code>{esc(welcome_msg[0])}</code>",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("↩️ إلغاء", callback_data="ADMIN")))
        except MessageNotModified: pass

    elif act == "users":
        ids = "\n".join(f"• <code>{u}</code>" for u in sorted(allowed_users))
        try: await call.message.edit_text(
            f"👥 <b>المستخدمون ({len(allowed_users)})</b>\n\n{ids}", reply_markup=kb_admin())
        except MessageNotModified: pass

    elif act == "pending":
        if not pending_users:
            txt = "✅ لا توجد طلبات معلّقة."
            try: await call.message.edit_text(txt, reply_markup=kb_admin())
            except MessageNotModified: pass
        else:
            # إرسال كل طلب برسالة منفصلة مع أزرار قبول/رفض
            await call.message.edit_text(
                f"⏳ <b>الطلبات المعلّقة ({len(pending_users)})</b>\nاضغط للقبول أو الرفض:",
                reply_markup=kb_admin())
            for puid, info in list(pending_users.items()):
                n = info.get("name","؟")
                u = info.get("username","—")
                await safe_send(uid,
                    f"👤 <b>{esc(n)}</b>\n🔗 {esc(u)}\n🆔 <code>{puid}</code>",
                    reply_markup=kb_approve(puid))

    await call.answer()

# ══════════════════════════════════════════════
#  أوامر نصية للمشرف
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
    await safe_send(new, f"{welcome_msg[0]}\n\nاختر نوع البحث:", reply_markup=kb_main(new))

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
    uid = msg.from_user.id
    if is_ready:
        await msg.reply(T(uid,f"✅ جاهز — {len(index_lines):,} سطر في الفهرس",
                              f"✅ Ready — {len(index_lines):,} lines indexed"))
    else:
        await msg.reply(T(uid,"⏳ جارٍ التحميل وبناء الفهرس …","⏳ Loading & indexing …"))

# ══════════════════════════════════════════════
#  معالج النصوص الوحيد
# ══════════════════════════════════════════════
@dp.message_handler()
async def handle_text(msg: types.Message):
    uid  = msg.from_user.id
    text = (msg.text or "").strip()
    user_lang.setdefault(uid, "ar")

    # غير مسموح → تجاهل
    if not allowed(uid): return

    # كلمة مفتاحية → القائمة
    if text.lower() in {"نوت","not","قائمة","menu","خيارات","options"}:
        user_mode.pop(uid, None)
        return await msg.answer(
            T(uid,"اختر نوع البحث:","Choose search type:"),
            reply_markup=kb_main(uid))

    # تغيير رسالة الترحيب
    if user_mode.get(uid) == "set_welcome" and is_admin(uid):
        welcome_msg[0] = text
        user_mode.pop(uid, None)
        return await msg.reply(f"✅ تم تغيير رسالة الترحيب.", reply_markup=kb_admin())

    # ── البحث ──────────────────────────────────
    if not is_ready:
        return await msg.reply(T(uid,
            "⏳ جارٍ تحميل البيانات وبناء الفهرس، انتظر قليلاً.",
            "⏳ Still loading data, please wait."))

    if not is_admin(uid) and get_count(uid) >= MAX_PER_DAY:
        return await msg.reply(T(uid,
            f"❌ انتهت حصتك اليومية ({MAX_PER_DAY} بحث).",
            f"❌ Daily limit reached ({MAX_PER_DAY} searches)."))

    query = text.lstrip("@")
    if not query: return
    if not clean(query):
        return await msg.reply(T(uid,"⚠️ رموز غير مسموح بها.","⚠️ Invalid characters."))

    if not is_admin(uid): add_count(uid)

    mode = user_mode.get(uid, "full")

    # البحث فوري في الذاكرة — بدون انتظار
    if   mode == "full":           lines = search_full(query)
    elif mode.startswith("split:"): lines = search_split(query, mode.split(":")[1])
    elif mode == "email":          lines = search_email(query)
    else:                          lines = search_full(query)

    if not lines:
        return await msg.reply(
            T(uid,"❌ لا توجد نتائج.","❌ No results."),
            reply_markup=kb_back(uid))

    sep    = "\n\n" if mode=="email" else "\n"
    header = T(uid,f"✅ النتائج ({len(lines)}):",f"✅ Results ({len(lines)}):")

    # تقطيع
    chunks, buf = [], []
    for line in lines:
        buf.append(line)
        if len(sep.join(buf)) > 3600:
            chunks.append(buf[:-1]); buf = [line]
    chunks.append(buf)

    if len(chunks) == 1:
        body = esc(sep.join(lines))
        await msg.answer(f"{header}\n\n<code>{body}</code>",
                         reply_markup=kb_back(uid))
    else:
        await msg.answer(header)
        for i, ch in enumerate(chunks):
            kb = kb_back(uid) if i==len(chunks)-1 else None
            await msg.answer(f"<code>{esc(sep.join(ch))}</code>", reply_markup=kb)

# ══════════════════════════════════════════════
#  تشغيل
# ══════════════════════════════════════════════
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
