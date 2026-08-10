import os
import re
import json
import html
import asyncio
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from openai import AsyncOpenAI

# ============================================================
# SETTINGS
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "@Gamefa_official").strip()
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip())
except (ValueError, TypeError):
    ADMIN_ID = 0

MEMORY_FILE = Path("news_memory.json")
MAX_MEMORY = 1500
MAX_ARTICLE_CHARS = 24000

memory = []
prepared = {}
router = Router()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("gamefa")

# ============================================================
# MEMORY
# ============================================================
def load_memory():
    global memory
    try:
        if MEMORY_FILE.exists():
            data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
            memory = data if isinstance(data, list) else []
        else:
            memory = []
    except Exception as e:
        log.warning("Memory load error: %s", e)
        memory = []


def save_memory():
    try:
        MEMORY_FILE.write_text(
            json.dumps(memory[-MAX_MEMORY:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("Memory save error: %s", e)

# ============================================================
# TEXT
# ============================================================
PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")


def norm(text):
    text = text or ""
    text = re.sub(r"https?://\S+", " ", text).lower()
    text = re.sub(r"[^\w\u0600-\u06FF\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a, b):
    a, b = set(norm(a).split()), set(norm(b).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def duplicate(text):
    return any(similarity(text, x.get("source", "")) >= 0.82 for x in memory)


def is_admin(message):
    return bool(ADMIN_ID and message.from_user and message.from_user.id == ADMIN_ID)


def extract_url(text):
    m = re.search(r"https?://[^\s<>()]+", text or "")
    return m.group(0).rstrip(".,)]}") if m else None


def esc(text):
    return html.escape(text or "", quote=False)


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "")


def starts_persian(text):
    text = re.sub(r"^[🎮🎬📱🟣🟢🔵🟡🟠\s]+", "", (text or "").strip())
    return bool(text and PERSIAN_RE.match(text[0]))


def persian_start(text, title=False):
    text = (text or "").strip()
    if not text or starts_persian(text):
        return text
    return ("گزارش جدید درباره " if title else "براساس گزارش‌های منتشرشده، ") + text


def category(text):
    t = (text or "").lower()
    games = ["بازی", "گیم", "game", "gaming", "playstation", "xbox", "nintendo", "steam", "doom", "gta", "halo", "resident evil", "devil may cry", "final fantasy"]
    movies = ["فیلم", "سریال", "بازیگر", "سینما", "movie", "film", "series", "season", "actor", "actress", "netflix", "hbo", "marvel", "dc"]
    if any(x in t for x in games):
        return "🎮"
    if any(x in t for x in movies):
        return "🎬"
    return "📱"

# ============================================================
# NEWS FORMAT: ONE PARAGRAPH / 7 LINES
# ============================================================
PROMPT = """
تو ویراستار حرفه‌ای اخبار کانال Gamefa هستی.

از اطلاعات ورودی یک پست فارسی آماده انتشار بساز.

قوانین قطعی:
1. خط اول فقط تیتر باشد.
2. تیتر حتماً با یک کلمه یا عبارت فارسی شروع شود.
3. اگر نام انگلیسی ابتدای جمله است، قبل از آن عبارت فارسی طبیعی بیاور.
4. متن خبر فقط یک بند باشد.
5. بدنه را دقیقاً در 7 خط بنویس؛ بین این خطوط خط خالی نگذار.
6. هر 7 خط باید با فارسی شروع شود.
7. هیچ خطی را با نام انگلیسی، شرکت، بازی، فیلم یا شخص شروع نکن.
8. نام‌های انگلیسی را داخل جمله حفظ کن.
9. متن خبری، طبیعی و قابل انتشار باشد.
10. اطلاعات ساختگی اضافه نکن.
11. Markdown، HTML، لینک، منبع و @Gamefa_official ننویس.
12. ایموجی 🟣 ننویس.
13. فقط تیتر و متن خبر را خروجی بده.
14. خبر بازی: تیتر با 🎮 شروع شود.
15. خبر فیلم/سریال: تیتر با 🎬 شروع شود.
16. خبر فناوری/هوش مصنوعی/موبایل/سخت‌افزار: تیتر با 📱 شروع شود.
17. بعد از ایموجی دسته‌بندی، اولین کلمه واقعی تیتر فارسی باشد.
"""


def split_seven(text):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return []
    sentences = [x.strip() for x in re.split(r"(?<=[.!؟?])\s+", text) if x.strip()]
    if len(sentences) >= 7:
        lines = sentences[:6] + [" ".join(sentences[6:])]
        return [persian_start(x) for x in lines]
    words = text.split()
    if len(words) < 7:
        return [persian_start(text)]
    lines, cur = [], []
    target = max(1, len(words) // 7)
    for w in words:
        cur.append(w)
        if len(cur) >= target and len(lines) < 6:
            lines.append(" ".join(cur))
            cur = []
    if cur:
        lines.append(" ".join(cur))
    if len(lines) > 7:
        lines = lines[:6] + [" ".join(lines[6:])]
    return [persian_start(x) for x in lines]


def format_post(ai_text):
    text = ai_text or ""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"__(.*?)__", r"\1", text, flags=re.S)
    text = re.sub(r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$", "", text)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines:
        return ""
    title = re.sub(r"^[🎮🎬📱]\s*", "", lines[0]).strip()
    title = persian_start(title, title=True)
    body = re.sub(r"^\s*🟣\s*", "", " ".join(lines[1:])).strip()
    body_lines = split_seven(body)
    result = f"<b>{esc(category(title + ' ' + body))} {esc(title)}</b>"
    if body_lines:
        result += "\n\n" + esc("\n".join(body_lines))
    result += "\n\n<b>🆔 @Gamefa_official</b>"
    return result


async def generate_news(source):
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.responses.create(
        model=MODEL,
        instructions=PROMPT,
        input=source,
        max_output_tokens=1600,
    )
    return (response.output_text or "").strip()

# ============================================================
# GAMEFA FETCH
# ============================================================
async def fetch_gamefa(url):
    if "gamefa.com" not in urlparse(url).netloc.lower():
        raise ValueError("فقط لینک Gamefa پشتیبانی می‌شود.")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"}
    timeout = aiohttp.ClientTimeout(total=35)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as response:
            response.raise_for_status()
            final_url = str(response.url)
            raw = await response.text(errors="ignore")

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "form", "aside"]):
        tag.decompose()

    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(" ", strip=True) if soup.title else "")

    description = ""
    for attrs in [{"name": "description"}, {"property": "og:description"}, {"name": "twitter:description"}]:
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            description = meta["content"].strip()
            break

    # فقط تصویر اصلی مقاله؛ هیچ جستجوی تصویر تصادفی انجام نمی‌شود.
    image = ""
    for attrs in [{"property": "og:image"}, {"name": "twitter:image"}, {"property": "og:image:url"}]:
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            image = urljoin(final_url, meta["content"].strip())
            break

    article = soup.find("article") or soup
    parts = []
    for p in article.find_all(["p", "h2", "h3"]):
        t = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if len(t) >= 35:
            parts.append(t)
    body = "\n".join(parts)[:MAX_ARTICLE_CHARS]

    return {"url": final_url, "title": title, "description": description, "body": body, "image": image}

# ============================================================
# IMAGE: SOURCE ONLY
# ============================================================
async def download_image(url):
    if not url:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    return None
                content_type = response.headers.get("Content-Type", "").lower()
                if "image" not in content_type:
                    return None
                data = await response.read()
        if not (1000 < len(data) <= 15 * 1024 * 1024):
            return None
        ext = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".webp" if "webp" in content_type else ".png"
        path = Path("gamefa_news_image" + ext)
        path.write_bytes(data)
        return path
    except Exception as e:
        log.warning("Image error: %s", e)
        return None


async def find_news_image(source_image):
    # اگر مقاله تصویر نداشت، فقط متن ارسال می‌شود.
    return await download_image(source_image) if source_image else None

# ============================================================
# UI: TWO-COLUMN LARGE INLINE BUTTONS
# ============================================================
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 بررسی خبر جدید", callback_data="new_news"), InlineKeyboardButton(text="🗂 مشاهده و مدیریت آرشیو", callback_data="archive")],
        [InlineKeyboardButton(text="📊 آمار آرشیو", callback_data="stats"), InlineKeyboardButton(text="🤖 وضعیت هوش مصنوعی", callback_data="ai_status")],
        [InlineKeyboardButton(text="👥 لیست مدیران", callback_data="admins"), InlineKeyboardButton(text="⚙️ تنظیمات سیستم", callback_data="settings")],
        [InlineKeyboardButton(text="📋 راهنما", callback_data="help"), InlineKeyboardButton(text="🗑 پاکسازی کامل آرشیو", callback_data="clear_confirm")],
    ])


def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data="home")]])


def archive_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 آخرین خبرها", callback_data="archive_latest"), InlineKeyboardButton(text="🗑 حذف آخرین رکورد", callback_data="archive_delete_last")],
        [InlineKeyboardButton(text="📊 آمار آرشیو", callback_data="stats"), InlineKeyboardButton(text="🔙 بازگشت", callback_data="home")],
    ])


def settings_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 مدل هوش مصنوعی", callback_data="setting_model"), InlineKeyboardButton(text="🖼 حالت تصویر", callback_data="setting_image")],
        [InlineKeyboardButton(text="📝 قالب خبر", callback_data="setting_format"), InlineKeyboardButton(text="📢 کانال انتشار", callback_data="setting_channel")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="home")],
    ])


def clear_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ بله، پاک کن", callback_data="clear_yes"), InlineKeyboardButton(text="❌ لغو", callback_data="home")]])


def publish_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 انتشار در کانال", callback_data="publish"), InlineKeyboardButton(text="✏️ بازنویسی خبر", callback_data="rewrite")],
        [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="home")],
    ])


def home_text():
    return (
        "✨ <b>مدیریت ربات Gamefa</b>\n\n"
        "از منوی زیر عملیات موردنظر را انتخاب کنید.\n\n"
        "📰 خبر را به‌صورت متن یا لینک Gamefa ارسال کنید.\n"
        "🖼 فقط تصویر اصلی خود مقاله استفاده می‌شود؛ اگر تصویر نداشته باشد، پست فقط متنی خواهد بود."
    )

# ============================================================
# PROCESS
# ============================================================
async def process_news(message, text):
    url = extract_url(text)
    source_image = ""
    article = None
    source = text

    if url:
        status = await message.answer("⏳ <b>در حال دریافت خبر...</b>")
        try:
            article = await fetch_gamefa(url)
        finally:
            try:
                await status.delete()
            except Exception:
                pass
        source_image = article["image"]
        source = (
            f"URL:\n{article['url']}\n\nTITLE:\n{article['title']}\n\n"
            f"DESCRIPTION:\n{article['description']}\n\nARTICLE:\n{article['body']}"
        )

    if duplicate(source):
        await message.answer("⚠️ <b>این خبر قبلاً در آرشیو ثبت شده است.</b>", reply_markup=main_menu())
        return

    status = await message.answer("✍️ <b>در حال آماده‌سازی متن خبر...</b>")
    try:
        generated = await generate_news(source)
    finally:
        try:
            await status.delete()
        except Exception:
            pass

    post = format_post(generated)
    if not post:
        raise RuntimeError("متن تولیدشده خالی است.")

    status = await message.answer("🖼 <b>در حال بررسی تصویر اصلی خبر...</b>")
    try:
        image_path = await find_news_image(source_image)
    finally:
        try:
            await status.delete()
        except Exception:
            pass

    memory.append({"source": source[:16000], "post": post, "url": url or ""})
    save_memory()

    user_id = message.from_user.id
    prepared[user_id] = {"text": post, "image": str(image_path) if image_path else ""}

    if image_path:
        try:
            await message.answer_photo(FSInputFile(image_path), caption=post, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning("Preview photo failed: %s", e)
            await message.answer(post, parse_mode=ParseMode.HTML)
    else:
        await message.answer(post, parse_mode=ParseMode.HTML)

    await message.answer("✅ <b>خبر آماده انتشار است.</b>", reply_markup=publish_keyboard(), parse_mode=ParseMode.HTML)

# ============================================================
# CALLBACKS
# ============================================================
def allowed(c):
    return bool(c.from_user and c.from_user.id == ADMIN_ID)


@router.callback_query(F.data == "home")
async def home(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text(home_text(), reply_markup=main_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "new_news")
async def new_news(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.answer(
        "📰 <b>خبر جدید را ارسال کنید.</b>\n\nمتن خبر یا لینک Gamefa را بفرستید.\nاگر مقاله تصویر نداشته باشد، ربات هیچ تصویر تصادفی پیدا نمی‌کند و فقط متن را آماده می‌کند.",
        reply_markup=back_menu(), parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "archive")
async def archive(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text("🗂 <b>مشاهده و مدیریت آرشیو</b>", reply_markup=archive_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "archive_latest")
async def archive_latest(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    if not memory:
        text = "🗂 <b>آرشیو خالی است.</b>"
    else:
        rows = ["🗂 <b>آخرین خبرها</b>"]
        for i, item in enumerate(memory[-10:][::-1], 1):
            p = strip_html(item.get("post", ""))
            title = p.splitlines()[0] if p else "بدون عنوان"
            rows.append(f"{i}. {esc(title[:180])}")
        text = "\n".join(rows)
    await c.message.edit_text(text, reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "archive_delete_last")
async def archive_delete_last(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    if memory:
        memory.pop(); save_memory()
        text = "✅ آخرین خبر از آرشیو حذف شد."
    else:
        text = "⚠️ آرشیو خالی است."
    await c.message.edit_text(text, reply_markup=archive_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "stats")
async def stats(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text(f"📊 <b>آمار آرشیو</b>\n\nخبرهای ذخیره‌شده: <b>{len(memory)}</b>\nظرفیت: <b>{MAX_MEMORY}</b>", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "ai_status")
async def ai_status(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    status = "🟢 فعال" if OPENAI_API_KEY else "🔴 غیرفعال"
    await c.message.edit_text(f"🤖 <b>وضعیت هوش مصنوعی</b>\n\nوضعیت: {status}\nمدل: <code>{esc(MODEL)}</code>", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "admins")
async def admins(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text(f"👥 <b>لیست مدیران</b>\n\nمدیر اصلی: <code>{ADMIN_ID}</code>", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "settings")
async def settings(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text("⚙️ <b>تنظیمات سیستم</b>\n\nبخش موردنظر را انتخاب کنید.", reply_markup=settings_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "setting_model")
async def setting_model(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text(f"🤖 <b>مدل هوش مصنوعی</b>\n\nمدل فعلی: <code>{esc(MODEL)}</code>\n\nبرای تغییر، OPENAI_MODEL را در Variables ریلوی تغییر دهید.", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "setting_image")
async def setting_image(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text("🖼 <b>حالت تصویر</b>\n\nفقط تصویر اصلی مقاله استفاده می‌شود.\nاگر تصویر اصلی وجود نداشته باشد، هیچ تصویر تصادفی از وب انتخاب نمی‌شود.", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "setting_format")
async def setting_format(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text("📝 <b>قالب خبر</b>\n\n• تیتر با فارسی شروع می‌شود.\n• متن یک بند است.\n• بدنه تا ۷ خط تنظیم می‌شود.\n• شروع هر خط فارسی است.\n• امضای Gamefa در پایان اضافه می‌شود.", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "setting_channel")
async def setting_channel(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text(f"📢 <b>کانال انتشار</b>\n\nکانال فعلی: <code>{esc(CHANNEL_ID)}</code>", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "help")
async def help_cb(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text("📋 <b>راهنما</b>\n\n۱. بررسی خبر جدید را بزنید.\n۲. متن یا لینک Gamefa را بفرستید.\n۳. ربات متن را آماده می‌کند.\n۴. فقط تصویر اصلی مقاله استفاده می‌شود.\n۵. در صورت نبود تصویر، پست فقط متن است.\n۶. در پایان روی انتشار در کانال بزنید.", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "clear_confirm")
async def clear_confirm(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.edit_text("⚠️ <b>پاکسازی کامل آرشیو</b>\n\nتمام خبرهای ذخیره‌شده حذف می‌شوند. ادامه می‌دهید؟", reply_markup=clear_keyboard(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "clear_yes")
async def clear_yes(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    memory.clear(); save_memory()
    await c.answer("آرشیو پاک شد.", show_alert=True)
    await c.message.edit_text("✅ <b>آرشیو با موفقیت پاک شد.</b>", reply_markup=main_menu(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "publish")
async def publish_cb(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await publish_prepared(c.message)


@router.callback_query(F.data == "rewrite")
async def rewrite_cb(c: CallbackQuery):
    if not allowed(c):
        await c.answer("دسترسی ندارید.", show_alert=True); return
    await c.answer()
    await c.message.answer("✏️ متن خبر یا لینک Gamefa را دوباره ارسال کنید تا بازنویسی شود.", reply_markup=back_menu())

# ============================================================
# PUBLISH
# ============================================================
async def publish_prepared(message):
    item = prepared.get(message.from_user.id)
    if not item:
        await message.answer("❌ هنوز خبری برای انتشار آماده نشده است.", reply_markup=main_menu())
        return

    text = item.get("text", "")
    image = item.get("image", "")

    try:
        if image and Path(image).exists():
            try:
                await message.bot.send_photo(CHANNEL_ID, FSInputFile(image), caption=text, parse_mode=ParseMode.HTML)
            except Exception as e:
                log.warning("Photo publish failed: %s", e)
                await message.bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML)
        else:
            await message.bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML)

        prepared.pop(message.from_user.id, None)
        await message.answer("✅ <b>خبر با موفقیت در کانال منتشر شد.</b>", reply_markup=main_menu(), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("Publish error")
        await message.answer("❌ <b>خطا هنگام انتشار:</b>\n" + esc(str(e)[:1500]), reply_markup=main_menu(), parse_mode=ParseMode.HTML)

# ============================================================
# COMMANDS / TEXT
# ============================================================
@router.message(Command("start"))
async def start(message: Message):
    if not is_admin(message):
        await message.answer("⛔ این ربات خصوصی است.")
        return
    await message.answer(home_text(), reply_markup=main_menu(), parse_mode=ParseMode.HTML)


@router.message(Command("menu"))
async def menu(message: Message):
    if not is_admin(message):
        return
    await message.answer(home_text(), reply_markup=main_menu(), parse_mode=ParseMode.HTML)


@router.message(Command("stats"))
async def stats_command(message: Message):
    if not is_admin(message):
        return
    await message.answer(f"📊 <b>آمار آرشیو</b>\n\nتعداد خبرهای ذخیره‌شده: <b>{len(memory)}</b>", reply_markup=back_menu(), parse_mode=ParseMode.HTML)


@router.message(Command("clear"))
async def clear_command(message: Message):
    if not is_admin(message):
        return
    await message.answer("⚠️ برای پاکسازی کامل آرشیو تأیید کنید.", reply_markup=clear_keyboard())


@router.message(Command("publish"))
async def publish_command(message: Message):
    if not is_admin(message):
        return
    await publish_prepared(message)


@router.message(F.text)
async def text_handler(message: Message):
    if not is_admin(message):
        return
    try:
        await process_news(message, message.text.strip())
    except Exception as e:
        log.exception("Processing error")
        await message.answer("❌ <b>خطا:</b>\n" + esc(str(e)[:1500]), reply_markup=main_menu(), parse_mode=ParseMode.HTML)

# ============================================================
# MAIN
# ============================================================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده است.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY تنظیم نشده است.")
    if not ADMIN_ID:
        raise RuntimeError("ADMIN_ID تنظیم نشده است.")

    load_memory()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    log.info("Gamefa bot started | admin=%s | channel=%s | model=%s", ADMIN_ID, CHANNEL_ID, MODEL)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
