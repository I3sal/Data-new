import subprocess, os, requests, zipfile, logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- الإعدادات ---
API_TOKEN = '8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o'
ADMIN_ID = 8506955611
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# قاعدة بيانات الذاكرة للمستخدمين (يمكنك تطويرها لملف JSON لاحقاً)
allowed_users = {ADMIN_ID}

# --- تحميل ذكي من ميديا فاير ---
def download_data():
    if not os.path.exists("data_files"):
        os.makedirs("data_files")
        try:
            url = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"
            page = requests.get(url).text
            direct_link = page.split('id="downloadButton" href="')[1].split('"')[0]
            r = requests.get(direct_link, stream=True)
            with open("temp.zip", "wb") as f: f.write(r.content)
            with zipfile.ZipFile("temp.zip", 'r') as z: z.extractall("data_files")
            os.remove("temp.zip")
            logging.info("تم تحميل وفك البيانات بنجاح.")
        except Exception as e:
            logging.error(f"Error: {e}")

download_data()

# --- القوائم (Keyboard) ---
def main_menu(lang="ar"):
    kb = InlineKeyboardMarkup(row_width=1)
    if lang == "ar":
        kb.add(InlineKeyboardButton("🔍 بحث عن بيانات", callback_data="search_ar"))
        kb.add(InlineKeyboardButton("ℹ️ معلومات", callback_data="info_ar"))
    else:
        kb.add(InlineKeyboardButton("🔍 Data Lookup", callback_data="search_en"))
        kb.add(InlineKeyboardButton("ℹ️ Info", callback_data="info_en"))
    return kb

# --- الأوامر ---
@dp.message_handler(commands=['start'])
async def start(msg: types.Message):
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("العربية 🇦🇷", callback_data="lang_ar"),
        InlineKeyboardButton("English 🇺🇸", callback_data="lang_en")
    )
    await msg.answer("مرحباً! اختر اللغة / Welcome! Choose Language:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('lang_'))
async def set_lang(call: types.CallbackQuery):
    lang = call.data.split('_')[1]
    await call.message.edit_text("تم الاختيار! / Selected!", reply_markup=main_menu(lang))

@dp.message_handler()
async def search_handler(msg: types.Message):
    # نظام الحماية: إذا لم يكن المستخدم في القائمة، يتم تجاهله
    if msg.from_user.id not in allowed_users:
        return await msg.reply("🚫 غير مصرح لك باستخدام البوت.")

    query = msg.text.strip().replace('@', '')
    # البحث الجذري
    cmd = f"find data_files/ -type f -exec grep -hF '{query}' {{}} + | head -n 5"
    process = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if process.stdout.strip():
        resp = f"✅ **النتائج المطابقة لـ {query}:**\n\n" + process.stdout.strip()
        await msg.answer(resp, parse_mode="Markdown")
    else:
        await msg.answer("❌ لم يتم العثور على نتائج.")

# أوامر الأدمن
@dp.message_handler(commands=['adduser'])
async def add_user(msg: types.Message):
    if msg.from_user.id != ADMIN_ID: return
    try:
        new_id = int(msg.get_args())
        allowed_users.add(new_id)
        await msg.reply(f"✅ تم إضافة المستخدم {new_id} بنجاح.")
    except: await msg.reply("⚠️ خطأ، استخدم `/adduser [ID]`")

if __name__ == '__main__':
    executor.start_polling(dp)
