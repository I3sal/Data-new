import subprocess
from aiogram import Bot, Dispatcher, executor, types
import os
import requests
import zipfile

# دالة التحميل (تأكد من استخدام رابط مباشر ثابت إذا أمكن)
def download_data():
    if not os.path.exists("data"): # تأكدنا من المجلد باسم data
        print("جاري التحميل وفك الضغط...")
        url = "ضع_رابطك_المباشر_هنا"
        try:
            response = requests.get(url)
            with open("mydata.zip", "wb") as f:
                f.write(response.content)
            with zipfile.ZipFile("mydata.zip", "r") as zip_ref:
                zip_ref.extractall(".")
            print("تم التحميل والفك!")
        except Exception as e:
            print(f"خطأ في التحميل: {e}")

download_data()

API_TOKEN = '8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o'
ADMIN_ID = 8506955611 
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

allowed_users = [ADMIN_ID]

# 1. خاصية الـ Start (سترات)
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.reply("👋 أهلاً بك في بوت البحث عن البيانات.\n\n"
                        "يرجى إرسال رابط مجموعتك للتحقق، أو استخدم /u [username] للبحث.")

# 2. خاصية البحث المطور مع التحقق من وجود اليوزر
@dp.message_handler(commands=['u'])
async def search(message: types.Message):
    if message.from_user.id not in allowed_users:
        await message.reply("🚫 غير مصرح لك! يرجى مشاركة رابط القروب أولاً.")
        return

    query = message.get_args()
    if not query:
        await message.reply("⚠️ مثال: `/u username`")
        return

    # استخدام grep مع العلم -w للبحث عن الكلمة كاملة (مطابقة دقيقة)
    process = subprocess.run(f"grep -whw '{query}' data/part_* | head -n 10", 
                             shell=True, capture_output=True, text=True)
    
    if process.stdout.strip():
        await message.reply(f"✅ **النتائج المطابقة لـ `{query}`:**\n\n`{process.stdout.strip()}`", parse_mode="Markdown")
    else:
        await message.reply(f"❌ لم يتم العثور على اليوزر `{query}` في قواعد البيانات.", parse_mode="Markdown")

# 3. الرد التلقائي والتحقق من المستخدم
@dp.message_handler()
async def auto_reply(message: types.Message):
    # التحقق من رابط القروب
    if "t.me/" in message.text and message.from_user.id not in allowed_users:
        allowed_users.append(message.from_user.id)
        await message.reply("✅ تم قبول رابطك! يمكنك الآن استخدام أمر /u للبحث.")
    
    # الرد التلقائي لمن لا يملك صلاحية
    elif message.from_user.id not in allowed_users:
        await message.reply("🚫 يرجى إرسال رابط القروب ليتم تفعيل اشتراكك.")
    
    # الرد التلقائي لمن لديه صلاحية
    else:
        await message.reply("أنا هنا! إذا كنت تبحث عن شخص، استخدم الأمر `/u username`", parse_mode="Markdown")

if __name__ == '__main__':
    executor.start_polling(dp)
