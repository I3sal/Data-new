import subprocess
import os
import re
import requests
import zipfile
import logging
import threading
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ─── Config ──────────────────────────────────────────────────────────────────
API_TOKEN = "8723495517:AAEFsdiG0DR6NK8BHpwhVmATSlTVgkJah6o"
ADMIN_ID  = 8506955611
DATA_DIR  = "data_files"
ZIP_PATH  = "temp.zip"
MEDIAFIRE_PAGE_URL = "https://www.mediafire.com/file/i8x5x9844vl24o5/mydata.zip/file"

# ─── State ────────────────────────────────────────────────────────────────────
allowed_users: set[int] = {ADMIN_ID}
is_ready      = False          # True once data is extracted
download_lock = threading.Lock()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ─── Bot / Dispatcher ─────────────────────────────────────────────────────────
bot = Bot(token=API_TOKEN)
dp  = Dispatcher(bot)


# ─── Background download ──────────────────────────────────────────────────────
def _resolve_mediafire_link(page_url: str) -> str:
    """Scrape the direct download link from a MediaFire page."""
    html = requests.get(page_url, timeout=30).text
    # MediaFire embeds the direct link in an anchor with id="downloadButton"
    match = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', html)
    if not match:
        # Fallback: look for aria-label="Download file"
        match = re.search(r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton["\']', html)
    if not match:
        raise ValueError("Could not find direct download link on MediaFire page.")
    return match.group(1)


def download_background() -> None:
    global is_ready
    with download_lock:
        if is_ready:
            return  # another thread already finished
        if os.path.isdir(DATA_DIR) and os.listdir(DATA_DIR):
            log.info("Data directory already exists and is non-empty – skipping download.")
            is_ready = True
            return

        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            log.info("Resolving MediaFire direct link …")
            direct_url = _resolve_mediafire_link(MEDIAFIRE_PAGE_URL)
            log.info("Downloading ZIP from %s …", direct_url)

            with requests.get(direct_url, stream=True, timeout=120) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(ZIP_PATH, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):  # 1 MB chunks
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded * 100 // total
                            if pct % 10 == 0:
                                log.info("Download progress: %d%%", pct)

            log.info("Extracting ZIP …")
            with zipfile.ZipFile(ZIP_PATH, "r") as z:
                z.extractall(DATA_DIR)

            os.remove(ZIP_PATH)
            is_ready = True
            log.info("Data ready in '%s'.", DATA_DIR)

        except Exception as exc:
            log.exception("Background download failed: %s", exc)
            # Clean up partial artefacts so a restart can retry
            if os.path.exists(ZIP_PATH):
                os.remove(ZIP_PATH)


# Start immediately, non-blocking
threading.Thread(target=download_background, daemon=True).start()


# ─── Keyboards ────────────────────────────────────────────────────────────────
def lang_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
        InlineKeyboardButton("🇬🇧 English",  callback_data="lang_en"),
    )
    return kb


# Simple in-memory language preference per user
user_lang: dict[int, str] = {}

def t(uid: int, ar: str, en: str) -> str:
    return ar if user_lang.get(uid, "ar") == "ar" else en


# ─── Auth helper ──────────────────────────────────────────────────────────────
def is_allowed(uid: int) -> bool:
    return uid in allowed_users


# ─── /start ───────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message) -> None:
    if not is_allowed(msg.from_user.id):
        return
    await msg.answer(
        "🌐 اختر اللغة / Choose language:",
        reply_markup=lang_keyboard(),
    )


# ─── Language selection ───────────────────────────────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("lang_"))
async def cb_language(call: types.CallbackQuery) -> None:
    uid  = call.from_user.id
    if not is_allowed(uid):
        return
    lang = call.data.split("_")[1]
    user_lang[uid] = lang
    await call.message.edit_text(
        t(uid,
          "✅ تم اختيار العربية.\n\nأرسل اسم المستخدم أو الكلمة للبحث:",
          "✅ English selected.\n\nSend a username or keyword to search:")
    )
    await call.answer()


# ─── /adduser (admin only) ────────────────────────────────────────────────────
@dp.message_handler(commands=["adduser"])
async def cmd_adduser(msg: types.Message) -> None:
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await msg.reply("Usage: /adduser [user_id]")
        return
    new_id = int(parts[1])
    allowed_users.add(new_id)
    log.info("Admin added user %d", new_id)
    await msg.reply(f"✅ User {new_id} added.")


# ─── /removeuser (admin only) ─────────────────────────────────────────────────
@dp.message_handler(commands=["removeuser"])
async def cmd_removeuser(msg: types.Message) -> None:
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await msg.reply("Usage: /removeuser [user_id]")
        return
    uid = int(parts[1])
    allowed_users.discard(uid)
    await msg.reply(f"✅ User {uid} removed.")


# ─── /status ──────────────────────────────────────────────────────────────────
@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message) -> None:
    if not is_allowed(msg.from_user.id):
        return
    uid = msg.from_user.id
    if is_ready:
        await msg.reply(t(uid, "✅ البيانات جاهزة للبحث.", "✅ Data is ready for search."))
    else:
        await msg.reply(t(uid, "⏳ البيانات لا تزال تُحمَّل …", "⏳ Data is still downloading …"))


# ─── Search ───────────────────────────────────────────────────────────────────
@dp.message_handler()
async def search(msg: types.Message) -> None:
    uid = msg.from_user.id
    if not is_allowed(uid):
        return

    if not is_ready:
        await msg.reply(
            t(uid,
              "⚠️ البوت لا يزال يُحمِّل البيانات، انتظر قليلاً ثم أعد المحاولة.",
              "⚠️ Bot is still downloading data. Please wait a moment and try again.")
        )
        return

    query = msg.text.strip().lstrip("@")
    if not query:
        return

    # Sanitise: reject shell meta-characters to prevent injection
    if any(c in query for c in ("'", '"', ";", "&", "|", "`", "$", "\\", "\n")):
        await msg.reply(
            t(uid,
              "⚠️ الاستعلام يحتوي على رموز غير مسموح بها.",
              "⚠️ Query contains disallowed characters.")
        )
        return

    # Run grep safely: pass query as a positional argument, not via shell interpolation
    try:
        result = subprocess.run(
            ["grep", "-rFihl", "--", query, DATA_DIR],
            capture_output=True, text=True, timeout=30
        )
        # -l gives matching file paths; fetch up to 5 lines per file with context
        if not result.stdout.strip():
            await msg.reply(t(uid, "❌ لم يُعثر على نتائج.", "❌ No results found."))
            return

        matched_files = result.stdout.strip().splitlines()[:5]
        lines_out: list[str] = []
        for fpath in matched_files:
            res2 = subprocess.run(
                ["grep", "-Fih", "--", query, fpath],
                capture_output=True, text=True, timeout=15
            )
            for line in res2.stdout.splitlines()[:3]:
                lines_out.append(line.strip())

        output = "\n".join(lines_out[:15])
        header = t(uid, "✅ النتائج:", "✅ Results:")
        await msg.answer(f"{header}\n\n<code>{output}</code>", parse_mode="HTML")

    except subprocess.TimeoutExpired:
        await msg.reply(t(uid, "⚠️ انتهت مهلة البحث.", "⚠️ Search timed out."))
    except Exception as exc:
        log.exception("Search error: %s", exc)
        await msg.reply(t(uid, "⚠️ حدث خطأ أثناء البحث.", "⚠️ An error occurred during search."))


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
