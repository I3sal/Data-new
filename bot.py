import subprocess
from aiogram import Bot, Dispatcher, executor, types
import os
import requests
import zipfile

def download_data():
    if not os.path.exists("mydata"):
        print("جاري تحميل وفك ضغط البيانات...")
        url = "https://download1642.mediafire.com/5y0o4n32e18g4s1t3w5l0m2s4c1b0u5r6q1o7a2x9z8/i8x5x9844vl24o5/mydata.zip"
        response = requests.get(url)
        with open("mydata.zip", "wb") as f:
            f.write(response.content)
        with zipfile.ZipFile("mydata.zip", "r") as zip_ref:
            zip_ref.extractall(".")
        print("تم التحميل والفك بنجاح!")

download_data()
# التوكن الخاص بك (تم وضع علامات التنصيص)
API_TOKEN = '8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o'
ADMIN_ID = 8506955611 

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# قائمة المستخدمين المسموح لهم
allowed_users = [ADMIN_ID]

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.reply("أهلاً بك! يرجى إرسال رابط مجموعتك للتحقق.")

@dp.message_handler(commands=['u'])
async def search(message: types.Message):
    if message.from_user.id not in allowed_users:
        await message.reply("🚫 غير مصرح لك! يرجى مشاركة رابط القروب.")
        return

    query = message.get_args()
    if not query:
        await message.reply("⚠️ مثال: `/u username`")
        return

    # البحث عن كل النتائج مع حصرها بـ 10 فقط
    process = subprocess.run(f"grep -h '{query}' data/part_* | head -n 10", 
                             shell=True, capture_output=True, text=True)
    
    if process.stdout.strip():
        await message.reply(f"✅ **النتائج:**\n`{process.stdout.strip()}`", parse_mode="Markdown")
    else:
        await message.reply("❌ لا يوجد نتائج.")

@dp.message_handler()
async def accept_link(message: types.Message):
    if "t.me/" in message.text and message.from_user.id not in allowed_users:
        allowed_users.append(message.from_user.id)
        await message.reply("✅ تم قبولك! يمكنك الآن البحث.")
    elif message.from_user.id not in allowed_users:
        await message.reply("🚫 يرجى إرسال رابط القروب أولاً.")

if __name__ == '__main__':
    executor.start_polling(dp)
