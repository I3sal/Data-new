import subprocess
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import requests
import zipfile

API_TOKEN = '8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o'
ADMIN_ID = 8506955611 
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
allowed_users = [ADMIN_ID]

# دالة التحميل المباشر الذكية
def download_data():
    if not os.path.exists("data"):
        print("جاري التحميل...")
        # ضع رابط التحميل المباشر لملف ZIP الخاص بك هنا
        url = "رابط_تحميلك_المباشر_هنا" 
        try:
            r = requests.get(url, stream=True)
            with open("mydata.zip", "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            with zipfile.ZipFile("mydata.zip", 'r') as zip_ref:
                zip_ref.extractall(".")
            print("تم الفك بنجاح!")
        except Exception as e:
            print(f"Error: {e}")

download_data()

def get_main_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔍 Search User", callback_data="search_user"))
    return keyboard

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.answer("👋 Welcome!\nمرحباً بك في بوت البحث.\n\nChoose an option:", reply_markup=get_main_keyboard())

@dp.callback_query_handler(text="search_user")
async def ask_search(call: types.CallbackQuery):
    await call.message.answer("⌨️ Send username/email:\nأرسل اليوزر أو الإيميل:")

@dp.message_handler()
async def process_search(message: types.Message):
    if message.from_user.id not in allowed_users:
        await message.reply("🚫 Not authorized.")
        return

    query = message.text
    # البحث في كل ملفات البيانات
    process = subprocess.run(f"grep -rwh '{query}' . | head -n 5", 
                             shell=True, capture_output=True, text=True)
    
    results = process.stdout.strip().split('\n')
    
    if results and results[0]:
        response = "✅ **Results / النتائج:**\n\n"
        for line in results:
            user = line.split(':')[0].replace('@', '')
            link = f"https://x.com/{user}"
            response += f"👤 **Account:** `{user}`\n🔗 [Open on X]({link})\n📄 **Data:** `{line}`\n──────────────\n"
        await message.answer(response, parse_mode="Markdown")
    else:
        await message.answer("❌ No results found.")

if __name__ == '__main__':
    executor.start_polling(dp)
