import asyncio
import os, re, requests, zipfile, logging, threading, subprocess
from datetime import datetime
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import MessageNotModified

# ── إعدادات ───────────────────────────────────────────────────────────────────
TOKEN         = os.environ.get("BOT_TOKEN", "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o")
ADMIN_ID      = int(os.environ.get("ADMIN_ID", "8506955611"))
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", os.environ.get("AI", ""))   # ضعه في Railway Variables
DATA_DIR      = "data_files"
ZIP_PATH      = "temp.zip"
MF_URL        = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"
MAX_DAY       = 2

# ── حالة ──────────────────────────────────────────────────────────────────────
allowed   : set[int]        = {ADMIN_ID}
pending   : dict[int, dict] = {}
mode_map  : dict[int, str]  = {}
counts    : dict[int, dict] = {}
ai_hist   : dict[int, list] = {}   # سجل محادثة AI لكل مستخدم
welcome   = ["👋 أهلاً بك! اختر نوع البحث."]
is_ready  = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TOKEN, parse_mode="HTML")
dp  = Dispatcher(bot)

# ── مساعدات ───────────────────────────────────────────────────────────────────
def today():            return datetime.now().strftime("%Y-%m-%d")
def adm(uid):           return uid == ADMIN_ID
def ok(uid):            return uid in allowed
def esc(s):             return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def day_count(uid):
    e = counts.get(uid,{}); return e.get("n",0) if e.get("d")==today() else 0
def inc(uid):
    e = counts.setdefault(uid,{"d":today(),"n":0})
    if e["d"]!=today(): e["d"],e["n"]=today(),0
    e["n"]+=1

async def try_edit(msg, text, kb=None):
    try: await msg.edit_text(text, reply_markup=kb)
    except MessageNotModified: pass

async def notify(uid, text, **kw):
    try: await bot.send_message(uid, text, **kw)
    except Exception as e: log.warning("notify %s: %s", uid, e)

# ── تحميل البيانات ─────────────────────────────────────────────────────────────
def resolve_mf():
    html = requests.get(MF_URL, timeout=30).text
    m = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', html) or \
        re.search(r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']', html)
    if not m: raise ValueError("لم يُعثر على رابط")
    return m.group(1)

def dl_thread():
    global is_ready
    if is_ready: return
    if os.path.isdir(DATA_DIR) and os.listdir(DATA_DIR):
        is_ready = True; log.info("البيانات موجودة ✅"); return
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        url = resolve_mf()
        log.info("تحميل: %s", url)
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            total, done = int(r.headers.get("content-length",0)), 0
            with open(ZIP_PATH,"wb") as f:
                for chunk in r.iter_content(1<<20):
                    f.write(chunk); done+=len(chunk)
                    if total and done*100//total%10==0:
                        log.info("  %d%%", done*100//total)
        with zipfile.ZipFile(ZIP_PATH) as z: z.extractall(DATA_DIR)
        os.remove(ZIP_PATH)
        is_ready = True; log.info("✅ جاهز")
    except Exception as ex:
        log.exception("فشل: %s", ex)
        if os.path.exists(ZIP_PATH): os.remove(ZIP_PATH)

threading.Thread(target=dl_thread, daemon=True).start()

# ── البحث ─────────────────────────────────────────────────────────────────────
def do_grep(q, n=15):
    try:
        p = subprocess.Popen(["grep","-rFih","--",q,DATA_DIR],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        res = []
        for raw in p.stdout:
            line = raw.decode("utf-8","ignore").strip()
            if line: res.append(line)
            if len(res) >= n: break
        p.kill(); p.wait()
        return res
    except Exception as ex:
        log.exception(ex); return []

def search_full(q):  return do_grep(q)

def search_split(q, field):
    fidx = {"user":0,"email":1,"password":2,"phone":3,"id":4}
    idx  = fidx.get(field,0)
    raw  = do_grep(q, 60)
    out  = []
    for line in raw:
        parts = re.split(r"[:|]", line)
        if len(parts)>idx and q.lower() in parts[idx].lower():
            out.append(line)
        if len(out)>=15: break
    return out

def search_email(q):
    pat = re.compile(r"([\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,})[:|](\S+)")
    raw = do_grep(q, 60)
    out = []
    for line in raw:
        m = pat.search(line)
        if m: out.append(f"📧 {m.group(1)}\n🔑 {m.group(2)}")
        if len(out)>=15: break
    return out

# ── الذكاء الاصطناعي ───────────────────────────────────────────────────────────
def ask_claude(uid: int, user_msg: str) -> str:
    """إرسال رسالة لـ Claude والحصول على رد"""
    if not ANTHROPIC_KEY:
        return "⚠️ لم يتم إعداد الذكاء الاصطناعي. أضف ANTHROPIC_KEY في Railway Variables."

    # سجل المحادثة (آخر 10 رسائل فقط لتوفير التكلفة)
    history = ai_hist.setdefault(uid, [])
    history.append({"role": "user", "content": user_msg})
    if len(history) > 20:
        history = history[-20:]
        ai_hist[uid] = history

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "system": (
                    "أنت مساعد ذكي داخل بوت تيليغرام. "
                    "أجب بالعربية دائماً ما لم يتحدث المستخدم بلغة أخرى. "
                    "كن مختصراً ومفيداً. لا تتجاوز 300 كلمة في الرد."
                ),
                "messages": history,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        reply = data["content"][0]["text"]
        # أضف رد المساعد للسجل
        history.append({"role": "assistant", "content": reply})
        return reply
    except requests.exceptions.Timeout:
        return "⚠️ انتهت مهلة الاتصال بالذكاء الاصطناعي، حاول مجدداً."
    except Exception as ex:
        log.exception("Claude error: %s", ex)
        return "⚠️ حدث خطأ في الذكاء الاصطناعي."

# ── لوحات مفاتيح ──────────────────────────────────────────────────────────────
def kb_req(uid):
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton("✅ قبول", callback_data=f"OK_{uid}"),
          InlineKeyboardButton("❌ رفض",  callback_data=f"NO_{uid}"))
    return k

def kb_main(uid):
    left = 999 if adm(uid) else MAX_DAY - day_count(uid)
    suf  = " (∞)" if adm(uid) else f" ({max(left,0)}/{MAX_DAY})"
    on   = left > 0
    k = InlineKeyboardMarkup(row_width=1)
    k.add(
        InlineKeyboardButton(f"🔍 بحث كامل{suf}",        callback_data="M_full"  if on else "nop"),
        InlineKeyboardButton(f"🔎 بحث منفصل{suf}",       callback_data="M_split" if on else "nop"),
        InlineKeyboardButton(f"📧 إيميل + باسورد{suf}",  callback_data="M_email" if on else "nop"),
        InlineKeyboardButton("🤖 محادثة AI",              callback_data="M_ai"),
        InlineKeyboardButton("🌐 اللغة",                  callback_data="LANG"),
    )
    if adm(uid):
        k.add(InlineKeyboardButton("⚙️ بانل الإدمن", callback_data="ADMIN"))
    return k

def kb_split():
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton("👤 اليوزر",  callback_data="F_user"),
          InlineKeyboardButton("📧 الإيميل", callback_data="F_email"),
          InlineKeyboardButton("📱 الهاتف",  callback_data="F_phone"),
          InlineKeyboardButton("🆔 الآيدي",  callback_data="F_id"),
          InlineKeyboardButton("↩️ رجوع",    callback_data="BACK"))
    return k

def kb_back():
    k = InlineKeyboardMarkup()
    k.add(InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="BACK"))
    return k

def kb_ai(uid):
    k = InlineKeyboardMarkup(row_width=1)
    k.add(InlineKeyboardButton("🗑️ مسح المحادثة", callback_data="AI_clear"),
          InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="BACK"))
    return k

def kb_lang():
    k = InlineKeyboardMarkup(row_width=2)
    k.add(InlineKeyboardButton("🇸🇦 العربية", callback_data="LG_ar"),
          InlineKeyboardButton("🇬🇧 English",  callback_data="LG_en"))
    return k

def kb_admin():
    k = InlineKeyboardMarkup(row_width=1)
    k.add(InlineKeyboardButton("📊 إحصائيات",           callback_data="A_stats"),
          InlineKeyboardButton("🔄 تصفير العدادات",      callback_data="A_reset"),
          InlineKeyboardButton("📝 رسالة الترحيب",       callback_data="A_welcome"),
          InlineKeyboardButton("👥 المستخدمون",           callback_data="A_users"),
          InlineKeyboardButton("⏳ الطلبات المعلّقة",     callback_data="A_pending"),
          InlineKeyboardButton("↩️ رجوع",                callback_data="BACK"))
    return k

# ── /start ─────────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["start"])
async def h_start(msg: types.Message):
    uid   = msg.from_user.id
    name  = msg.from_user.full_name
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "—"

    if ok(uid):
        return await msg.answer(welcome[0], reply_markup=kb_main(uid))

    if uid not in pending:
        pending[uid] = {"name": name, "username": uname}
        await msg.answer(f"⏳ تم إرسال طلبك.\n🆔 معرّفك: <code>{uid}</code>")
        await notify(ADMIN_ID,
            f"🔔 <b>طلب جديد</b>\n👤 {esc(name)}\n🔗 {esc(uname)}\n🆔 <code>{uid}</code>",
            reply_markup=kb_req(uid))
    else:
        await msg.answer(f"⏳ طلبك قيد الانتظار.\n🆔 معرّفك: <code>{uid}</code>")

# ── قبول / رفض ────────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("OK_") or c.data.startswith("NO_"))
async def h_req(call: types.CallbackQuery):
    if not adm(call.from_user.id): return await call.answer("⛔",show_alert=True)
    act = call.data[:2]
    uid = int(call.data[3:])
    inf = pending.pop(uid, {})
    n, u = esc(inf.get("name","؟")), esc(inf.get("username","—"))
    if act == "OK":
        allowed.add(uid)
        await try_edit(call.message, f"✅ قُبل: {n} ({u})")
        await notify(uid, f"✅ تمت الموافقة!\n\n{welcome[0]}", reply_markup=kb_main(uid))
    else:
        await try_edit(call.message, f"❌ رُفض: {n} ({u})")
        await notify(uid, "❌ تم رفض طلبك.")
    await call.answer()

# ── BACK ───────────────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "BACK")
async def h_back(call: types.CallbackQuery):
    uid = call.from_user.id
    if not ok(uid): return await call.answer("⛔",show_alert=True)
    mode_map.pop(uid, None)
    await try_edit(call.message, "اختر نوع البحث:", kb_main(uid))
    await call.answer()

# ── NOP ────────────────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "nop")
async def h_nop(call: types.CallbackQuery):
    await call.answer("❌ انتهت حصتك اليومية", show_alert=True)

# ── LANG ───────────────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "LANG")
async def h_lang(call: types.CallbackQuery):
    if not ok(call.from_user.id): return await call.answer("⛔",show_alert=True)
    await try_edit(call.message, "🌐 اختر اللغة:", kb_lang())
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("LG_"))
async def h_setlang(call: types.CallbackQuery):
    await try_edit(call.message, "اختر نوع البحث:", kb_main(call.from_user.id))
    await call.answer()

# ── AI ─────────────────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "M_ai")
async def h_ai_mode(call: types.CallbackQuery):
    uid = call.from_user.id
    if not ok(uid): return await call.answer("⛔",show_alert=True)
    mode_map[uid] = "ai"
    await try_edit(call.message,
        "🤖 <b>وضع الذكاء الاصطناعي</b>\n\n"
        "أرسل أي سؤال وسأجيبك!\n"
        "المحادثة تتذكر سياقها تلقائياً.",
        kb_ai(uid))
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "AI_clear")
async def h_ai_clear(call: types.CallbackQuery):
    uid = call.from_user.id
    if not ok(uid): return await call.answer("⛔",show_alert=True)
    ai_hist.pop(uid, None)
    await try_edit(call.message,
        "🤖 <b>وضع الذكاء الاصطناعي</b>\n\n"
        "✅ تم مسح المحادثة. أرسل سؤالاً جديداً:",
        kb_ai(uid))
    await call.answer()

# ── MODE ───────────────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("M_"))
async def h_mode(call: types.CallbackQuery):
    uid = call.from_user.id
    if not ok(uid): return await call.answer("⛔",show_alert=True)
    if not adm(uid) and day_count(uid) >= MAX_DAY:
        return await call.answer("❌ انتهت حصتك اليومية",show_alert=True)
    m = call.data[2:]
    if m == "split":
        mode_map[uid] = "split_pending"
        await try_edit(call.message, "اختر الحقل:", kb_split())
    elif m == "full":
        mode_map[uid] = "full"
        await try_edit(call.message, "🔍 أرسل الكلمة:", kb_back())
    elif m == "email":
        mode_map[uid] = "email"
        await try_edit(call.message, "📧 أرسل الإيميل:", kb_back())
    await call.answer()

# ── حقل البحث ──────────────────────────────────────────────────────────────────
FMAP = {"F_user":"user","F_email":"email","F_phone":"phone","F_id":"id"}
FAR  = {"user":"اليوزر","email":"الإيميل","phone":"الهاتف","id":"الآيدي"}

@dp.callback_query_handler(lambda c: c.data in FMAP)
async def h_field(call: types.CallbackQuery):
    uid = call.from_user.id
    if not ok(uid): return await call.answer("⛔",show_alert=True)
    key = FMAP[call.data]
    mode_map[uid] = f"split:{key}"
    await try_edit(call.message, f"🔎 بحث في [{FAR[key]}]. أرسل القيمة:", kb_back())
    await call.answer()

# ── ADMIN ──────────────────────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data == "ADMIN")
async def h_admin(call: types.CallbackQuery):
    if not adm(call.from_user.id): return await call.answer("⛔",show_alert=True)
    await try_edit(call.message, "⚙️ <b>بانل التحكم</b>", kb_admin())
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("A_"))
async def h_admin_act(call: types.CallbackQuery):
    uid = call.from_user.id
    if not adm(uid): return await call.answer("⛔",show_alert=True)
    act = call.data[2:]

    if act == "stats":
        ts = sum(1 for e in counts.values() if e.get("d")==today())
        ai_users = len(ai_hist)
        await try_edit(call.message,
            f"📊 <b>إحصائيات</b>\n\n"
            f"👥 مستخدمون: {len(allowed)}\n"
            f"⏳ معلّقون: {len(pending)}\n"
            f"🔍 بحث اليوم: {ts}\n"
            f"🤖 محادثات AI نشطة: {ai_users}\n"
            f"💾 البيانات: {'✅ جاهزة' if is_ready else '⏳ تحميل'}\n"
            f"📅 {today()}", kb_admin())

    elif act == "reset":
        counts.clear()
        await try_edit(call.message, "✅ تم تصفير العدادات.", kb_admin())

    elif act == "welcome":
        mode_map[uid] = "set_welcome"
        await try_edit(call.message,
            f"📝 أرسل رسالة الترحيب الجديدة:\n\nالحالية: <code>{esc(welcome[0])}</code>",
            InlineKeyboardMarkup().add(
                InlineKeyboardButton("↩️ إلغاء", callback_data="ADMIN")))

    elif act == "users":
        ids = "\n".join(f"• <code>{u}</code>" for u in sorted(allowed))
        await try_edit(call.message,
            f"👥 <b>المستخدمون ({len(allowed)})</b>\n\n{ids}", kb_admin())

    elif act == "pending":
        if not pending:
            await try_edit(call.message, "✅ لا توجد طلبات معلّقة.", kb_admin())
        else:
            await try_edit(call.message,
                f"⏳ <b>الطلبات المعلّقة ({len(pending)})</b>", kb_admin())
            for puid, inf in list(pending.items()):
                await notify(uid,
                    f"👤 <b>{esc(inf.get('name','؟'))}</b>\n"
                    f"🔗 {esc(inf.get('username','—'))}\n"
                    f"🆔 <code>{puid}</code>",
                    reply_markup=kb_req(puid))

    await call.answer()

# ── أوامر نصية ─────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["adduser"])
async def h_adduser(msg: types.Message):
    if not adm(msg.from_user.id): return
    p = msg.text.split()
    if len(p)!=2 or not p[1].lstrip("-").isdigit():
        return await msg.reply("الاستخدام: /adduser [id]")
    uid = int(p[1])
    allowed.add(uid); pending.pop(uid,None)
    await msg.reply(f"✅ تمت إضافة <code>{uid}</code>.")
    await notify(uid, welcome[0], reply_markup=kb_main(uid))

@dp.message_handler(commands=["removeuser"])
async def h_removeuser(msg: types.Message):
    if not adm(msg.from_user.id): return
    p = msg.text.split()
    if len(p)!=2 or not p[1].lstrip("-").isdigit():
        return await msg.reply("الاستخدام: /removeuser [id]")
    allowed.discard(int(p[1]))
    await msg.reply(f"✅ تمت إزالة <code>{p[1]}</code>.")

@dp.message_handler(commands=["admin"])
async def h_admin_cmd(msg: types.Message):
    if not adm(msg.from_user.id): return
    await msg.answer("⚙️ <b>بانل التحكم</b>", reply_markup=kb_admin())

@dp.message_handler(commands=["status"])
async def h_status(msg: types.Message):
    if not ok(msg.from_user.id): return
    ai_ok = "✅ متصل" if ANTHROPIC_KEY else "❌ غير مفعّل"
    await msg.reply(
        f"البيانات: {'✅ جاهزة' if is_ready else '⏳ جارٍ التحميل'}\n"
        f"🤖 الذكاء الاصطناعي: {ai_ok}")

# ── معالج النصوص الوحيد ────────────────────────────────────────────────────────
@dp.message_handler()
async def h_text(msg: types.Message):
    uid  = msg.from_user.id
    text = (msg.text or "").strip()

    if not ok(uid): return

    # كلمات القائمة
    if text.lower() in {"نوت","not","قائمة","menu","start","ابدأ"}:
        mode_map.pop(uid,None)
        return await msg.answer(welcome[0], reply_markup=kb_main(uid))

    # تغيير رسالة الترحيب
    if mode_map.get(uid) == "set_welcome" and adm(uid):
        welcome[0] = text
        mode_map.pop(uid,None)
        return await msg.reply("✅ تم تغيير رسالة الترحيب.", reply_markup=kb_admin())

    # ── وضع AI ──────────────────────────────────────────────────────────────────
    if mode_map.get(uid) == "ai":
        wait = await msg.reply("🤖 جارٍ التفكير …")
        reply = await asyncio.get_event_loop().run_in_executor(
            None, ask_claude, uid, text)
        await wait.delete()
        return await msg.reply(reply, reply_markup=kb_ai(uid))

    # ── البحث ───────────────────────────────────────────────────────────────────
    if not is_ready:
        return await msg.reply("⏳ البيانات لا تزال تُحمَّل، انتظر قليلاً.")

    if not adm(uid) and day_count(uid) >= MAX_DAY:
        return await msg.reply(f"❌ انتهت حصتك اليومية ({MAX_DAY} بحث).")

    query = text.lstrip("@").strip()
    if not query: return
    if any(c in query for c in ('"',";","&","|","`","$","\\","\n")):
        return await msg.reply("⚠️ رموز غير مسموح بها.")

    # إذا لم يختر وضعاً → أظهر القائمة
    if uid not in mode_map:
        return await msg.answer("اختر نوع البحث أولاً:", reply_markup=kb_main(uid))

    if not adm(uid): inc(uid)

    mode = mode_map[uid]
    wait = await msg.reply("🔄 جارٍ البحث …")

    try:
        if   mode == "full":            lines = search_full(query)
        elif mode.startswith("split:"): lines = search_split(query, mode.split(":")[1])
        elif mode == "email":           lines = search_email(query)
        else:                           lines = search_full(query)
    except Exception as ex:
        log.exception(ex)
        await wait.delete()
        return await msg.reply("⚠️ خطأ أثناء البحث.")

    await wait.delete()

    if not lines:
        return await msg.reply("❌ لا توجد نتائج.", reply_markup=kb_back())

    sep  = "\n\n" if mode=="email" else "\n"
    head = f"✅ النتائج ({len(lines)}):"

    chunks, buf = [], []
    for line in lines:
        buf.append(line)
        if len(sep.join(buf)) > 3600:
            chunks.append(buf[:-1]); buf = [line]
    chunks.append(buf)

    if len(chunks) == 1:
        await msg.answer(f"{head}\n\n<code>{esc(sep.join(lines))}</code>", reply_markup=kb_back())
    else:
        await msg.answer(head)
        for i, ch in enumerate(chunks):
            k = kb_back() if i==len(chunks)-1 else None
            await msg.answer(f"<code>{esc(sep.join(ch))}</code>", reply_markup=k)

# ── تشغيل ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    executor.start_polling(dp, skip_updates=True)
