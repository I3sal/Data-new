import logging
import subprocess
import os
import requests
import zipfile
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- الإعدادات ---
API_TOKEN = '8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o'
ADMIN_ID = 8506955611
# تهيئة السجلات (Logging) لتتبع أخطاء البوت
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# --- دالة التحميل الذكية ---
def download_data():
    if not os.path.exists("data_files"):
        os.makedirs("data_files")
        url = "رابط_التحميل_المباشر_هنا"
        try:
            r = requests.get(url, stream=True)
            with open("temp.zip", "wb") as f:
                f.write(r.content)
            with zipfile.ZipFile("temp.zip", 'r') as z:
                z.extractall("data_files")
            os.remove("temp.zip")
        except Exception as e:
            logging.error(f"Download error: {e}")

download_data()

# --- لوحة التحكم ---
def main_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔍 Search (البحث)", callback_data="search"))
    kb.add(InlineKeyboardButton("ℹ️ Info (معلومات)", callback_data="info"))
    return kb

@dp.message_handler(commands=['start'])
async def start(msg: types.Message):
    await msg.answer("👋 مرحباً بك في البوت العملاق للبحث!\nWelcome to the Master Search Bot.", reply_markup=main_kb())

@dp.callback_query_handler(text="search")
async def search_ui(call: types.CallbackQuery):
    await call.message.answer("⌨️ أرسل الإيميل أو اليوزر للبحث (Search keyword):")

@dp.message_handler()
async def handle_search(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return await msg.reply("🚫 غير مصرح لك!")
    
    query = msg.text
    # بحث سريع ومتقدم عبر grep
    # -r: تكراري، -F: نص ثابت، -i: تجاهل الحالة، -h: إخفاء اسم الملف
    cmd = f"grep -rFih '{query}' data_files/ | head -n 10"
    process = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    results = process.stdout.strip().split('\n')
    if results and results[0]:
        resp = "✅ **النتائج المطابقة (Results Found):**\n\n"
        for line in results:
            account = line.split(':')[0].replace('@', '')
            link = f"https://x.com/{account}"
            resp += f"👤 ` {account} `\n🔗 [Open on X]({link})\n📄 ` {line} `\n──────────────\n"
        await msg.answer(resp, parse_mode="Markdown")
    else:
        await msg.answer("❌ لم يتم العثور على نتائج / No results found.")

if __name__ == '__main__':
    executor.start_polling(dp)
