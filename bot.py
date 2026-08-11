import os
import re
import json
import html
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse, urljoin

import aiohttp
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message,
    FSInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from openai import AsyncOpenAI


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

CHANNEL_ID = os.getenv(
    "CHANNEL_ID",
    "@Gamefa_official"
).strip()

MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5.4-mini"
).strip()


# ------------------------------------------------------------
# ADMIN ID
# ------------------------------------------------------------

try:
    ADMIN_ID = int(
        os.getenv("ADMIN_ID", "0") or "0"
    )
except (ValueError, TypeError):
    ADMIN_ID = 0


# ------------------------------------------------------------
# MEMORY
# ------------------------------------------------------------

MEMORY_FILE = Path("news_memory.json")

MAX_MEMORY = 1500

memory = []

prepared = {}

processing_users = set()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("gamefa_bot")


# ============================================================
# MEMORY
# ============================================================

def load_memory():

    global memory

    try:

        if not MEMORY_FILE.exists():
            memory = []
            return

        data = json.loads(
            MEMORY_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, list):
            memory = data
        else:
            memory = []

    except Exception as error:

        log.warning(
            "Memory load error: %s",
            error
        )

        memory = []


def save_memory():

    try:

        MEMORY_FILE.write_text(
            json.dumps(
                memory[-MAX_MEMORY:],
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    except Exception as error:

        log.warning(
            "Memory save error: %s",
            error
        )


# ============================================================
# TEXT TOOLS
# ============================================================

def norm(text):

    text = text or ""

    text = re.sub(
        r"https?://\S+",
        " ",
        text
    )

    text = text.lower()

    text = re.sub(
        r"[^\w\u0600-\u06FF\s]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def similarity(a, b):

    words_a = set(
        norm(a).split()
    )

    words_b = set(
        norm(b).split()
    )

    if not words_a or not words_b:
        return 0

    return len(
        words_a & words_b
    ) / len(
        words_a | words_b
    )


def duplicate(text):

    for item in memory:

        old_source = item.get(
            "source",
            ""
        )

        if similarity(
            text,
            old_source
        ) >= 0.82:

            return True

    return False


def extract_url(text):

    if not text:
        return None

    match = re.search(
        r"https?://[^\s<>()]+",
        text
    )

    if not match:
        return None

    return match.group(0).rstrip(
        ".,)]}"
    )


def escape_html(text):

    return html.escape(
        text or "",
        quote=False
    )


# ============================================================
# ADMIN
# ============================================================

def is_admin(message):

    return bool(
        ADMIN_ID
        and message.from_user
        and message.from_user.id == ADMIN_ID
    )


def is_admin_id(user_id):

    return bool(
        ADMIN_ID
        and user_id == ADMIN_ID
    )


# ============================================================
# PERSIAN DETECTION
# ============================================================

PERSIAN_RE = re.compile(
    r"[\u0600-\u06FF]"
)


def starts_with_persian(text):

    if not text:
        return False

    clean = text.strip()

    clean = re.sub(
        r"^[🎮🎬📱🟣📢\s]+",
        "",
        clean
    )

    if not clean:
        return False

    first = clean[0]

    return bool(
        PERSIAN_RE.match(first)
    )


def make_persian_start(
    text,
    is_title=False
):

    if not text:
        return text

    text = text.strip()

    if starts_with_persian(text):
        return text

    if is_title:

        return (
            "گزارش جدید درباره "
            + text
        )

    return (
        "براساس گزارش‌های منتشرشده، "
        + text
    )


# ============================================================
# CATEGORY
# ============================================================

def detect_category(text):

    text_lower = (
        text or ""
    ).lower()

    game_words = [
        "بازی",
        "گیم",
        "game",
        "gaming",
        "playstation",
        "xbox",
        "nintendo",
        "steam",
        "doom",
        "gta",
        "resident evil",
        "halo",
        "final fantasy",
        "devil may cry",
        "assassin",
        "elden ring",
        "minecraft",
        "fortnite",
        "call of duty",
        "battlefield"
    ]

    movie_words = [
        "فیلم",
        "سریال",
        "بازیگر",
        "movie",
        "film",
        "series",
        "season",
        "actor",
        "actress",
        "netflix",
        "hbo",
        "disney",
        "marvel",
        "dc"
    ]

    if any(
        word in text_lower
        for word in game_words
    ):
        return "🎮"

    if any(
        word in text_lower
        for word in movie_words
    ):
        return "🎬"

    return "📱"


# ============================================================
# AI TEXT CLEAN
# ============================================================

def clean_ai_text(text):

    text = text or ""

    # حذف Markdown
    text = re.sub(
        r"\*\*(.*?)\*\*",
        r"\1",
        text,
        flags=re.S
    )

    text = re.sub(
        r"__(.*?)__",
        r"\1",
        text,
        flags=re.S
    )

    text = re.sub(
        r"\*(.*?)\*",
        r"\1",
        text,
        flags=re.S
    )

    text = re.sub(
        r"`(.*?)`",
        r"\1",
        text,
        flags=re.S
    )

    # حذف لینک Markdown
    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text
    )

    # حذف امضای کانال
    text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        text
    )

    # حذف ایموجی بنفش ابتدای بند
    text = re.sub(
        r"^\s*🟣\s*",
        "",
        text,
        flags=re.M
    )

    return text.strip()


# ============================================================
# FORMAT POST
# ============================================================

def format_post(ai_text):

    ai_text = clean_ai_text(
        ai_text
    )

    if not ai_text:
        return ""

    lines = [
        line.strip()
        for line in ai_text.splitlines()
        if line.strip()
    ]

    if not lines:
        return ""

    # ========================================================
    # TITLE
    # ========================================================

    title = lines[0]

    title = re.sub(
        r"^[🎮🎬📱📢]\s*",
        "",
        title
    ).strip()

    title = make_persian_start(
        title,
        is_title=True
    )

    # ========================================================
    # BODY
    # ========================================================

    body_lines = lines[1:]

    body_parts = []

    for line in body_lines:

        line = re.sub(
            r"^[🟣•\-–—]\s*",
            "",
            line
        ).strip()

        if not line:
            continue

        line = make_persian_start(
            line,
            is_title=False
        )

        body_parts.append(
            line
        )

    # ========================================================
    # CATEGORY
    # ========================================================

    category = detect_category(
        title
        + " "
        + " ".join(body_lines)
    )

    title = (
        category
        + " "
        + title
    )

    # ========================================================
    # FINAL
    # ========================================================

    result = (
        "<b>"
        + escape_html(title)
        + "</b>"
    )

    if body_parts:

        body = " ".join(
            body_parts
        )

        result += (
            "\n\n🟣 "
            + escape_html(body)
        )

    result += (
        "\n\n"
        "<b>🆔 @Gamefa_official</b>"
    )

    return result


# ============================================================
# GAMEFA FETCH
# ============================================================

async def fetch_gamefa(url):

    parsed = urlparse(url)

    if "gamefa.com" not in (
        parsed.netloc.lower()
    ):

        raise ValueError(
            "فقط لینک Gamefa پشتیبانی می‌شود."
        )

    headers = {
        "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151 Safari/537.36"
    }

    timeout = aiohttp.ClientTimeout(
        total=35
    )

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout
    ) as session:

        async with session.get(
            url,
            allow_redirects=True
        ) as response:

            response.raise_for_status()

            final_url = str(
                response.url
            )

            raw = await response.text(
                errors="ignore"
            )

    soup = BeautifulSoup(
        raw,
        "html.parser"
    )

    for element in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "form",
            "aside"
        ]
    ):

        element.decompose()

    # ========================================================
    # TITLE
    # ========================================================

    h1 = soup.find("h1")

    if h1:

        title = h1.get_text(
            " ",
            strip=True
        )

    elif soup.title:

        title = soup.title.get_text(
            " ",
            strip=True
        )

    else:

        title = ""

    # ========================================================
    # DESCRIPTION
    # ========================================================

    description = ""

    meta_options = [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"}
    ]

    for attrs in meta_options:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta and meta.get(
            "content"
        ):

            description = (
                meta["content"].strip()
            )

            break

    # ========================================================
    # IMAGE
    # ========================================================

    image = ""

    image_options = [
        {"property": "og:image"},
        {"name": "twitter:image"},
        {"property": "og:image:url"}
    ]

    for attrs in image_options:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta and meta.get(
            "content"
        ):

            image = urljoin(
                final_url,
                meta["content"].strip()
            )

            break

    # ========================================================
    # BODY
    # ========================================================

    article = (
        soup.find("article")
        or soup
    )

    paragraphs = article.find_all(
        [
            "p",
            "h2",
            "h3"
        ]
    )

    body_parts = []

    for paragraph in paragraphs:

        text = paragraph.get_text(
            " ",
            strip=True
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        ).strip()

        if len(text) >= 35:

            body_parts.append(
                text
            )

    body = "\n".join(
        body_parts
    )[:24000]

    return {
        "url": final_url,
        "title": title,
        "description": description,
        "body": body,
        "image": image
    }


# ============================================================
# AI
# ============================================================

PROMPT = """
تو ویراستار حرفه‌ای اخبار کانال Gamefa هستی.

از اطلاعات داده‌شده یک خبر فارسی آماده انتشار بساز.

قوانین:

1. خط اول فقط تیتر باشد.

2. تیتر حتماً با کلمه یا عبارت فارسی شروع شود.

3. نام انگلیسی نباید اولین عبارت تیتر باشد.

مثال غلط:
Netflix نسخه آمریکایی Squid Game را لغو کرد

مثال درست:
نتفلیکس نسخه آمریکایی Squid Game را لغو کرد

4. متن کاملاً فارسی و روان باشد.

5. متن خبر فقط یک پاراگراف باشد.

6. متن را در حدود 7 خط قابل‌خواندن تنظیم کن،
اما پاراگراف را به چند بند جدا تقسیم نکن.

7. اولین کلمه متن خبر باید فارسی باشد.

8. اگر جمله با نام انگلیسی شروع می‌شود،
قبل از آن عبارت فارسی طبیعی قرار بده.

مثال غلط:
Brad Pitt در مصاحبه جدید...

مثال درست:
برد پیت در مصاحبه جدید...

یا:
براساس گزارش‌ها، Brad Pitt در مصاحبه جدید...

9. نام‌های انگلیسی مانند نام بازی، فیلم،
شرکت و شخص را درون جمله حفظ کن.

10. اطلاعات ساختگی اضافه نکن.

11. هیچ لینک، منبع یا آیدی کانال تولید نکن.

12. Markdown تولید نکن.

13. HTML تولید نکن.

14. ایموجی تولید نکن.

15. خروجی فقط تیتر و متن خبر باشد.

16. تیتر باید با فارسی شروع شود.

17. متن خبر باید با فارسی شروع شود.
"""


async def generate_news(source):

    if not OPENAI_API_KEY:

        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده است."
        )

    client = AsyncOpenAI(
        api_key=OPENAI_API_KEY
    )

    response = await client.responses.create(
        model=MODEL,
        instructions=PROMPT,
        input=source,
        max_output_tokens=1400
    )

    return (
        response.output_text
        or ""
    ).strip()


# ============================================================
# IMAGE DOWNLOAD
# ============================================================

async def download_image(url):

    if not url:
        return None

    try:

        headers = {
            "User-Agent":
                "Mozilla/5.0"
        }

        timeout = aiohttp.ClientTimeout(
            total=30
        )

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout
        ) as session:

            async with session.get(
                url,
                allow_redirects=True
            ) as response:

                if response.status != 200:
                    return None

                content_type = (
                    response.headers.get(
                        "Content-Type",
                        ""
                    ).lower()
                )

                if "image" not in content_type:
                    return None

                data = await response.read()

        if not (
            1000
            < len(data)
            <= 15 * 1024 * 1024
        ):

            return None

        if (
            "jpeg" in content_type
            or "jpg" in content_type
        ):

            extension = ".jpg"

        elif "webp" in content_type:

            extension = ".webp"

        else:

            extension = ".png"

        path = Path(
            "gamefa_news_image"
            + extension
        )

        path.write_bytes(
            data
        )

        return path

    except Exception as error:

        log.warning(
            "Image download error: %s",
            error
        )

        return None


# ============================================================
# FIND IMAGE
# ============================================================

async def find_best_image(
    source_image
):

    if not source_image:

        log.info(
            "Article has no image. Text only."
        )

        return None

    image_path = await download_image(
        source_image
    )

    if image_path:

        log.info(
            "Using article source image."
        )

        return image_path

    log.info(
        "Source image unavailable. Text only."
    )

    return None


# ============================================================
# MAIN MENU
# ============================================================
#
# فقط 4 گزینه:
#
# 1. بررسی خبر جدید
# 2. مشاهده و مدیریت آرشیو
# 3. تنظیمات سیستم
# 4. پاکسازی کامل آرشیو
#
# ============================================================

def main_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🔎 بررسی خبر جدید",
                    callback_data="news_new"
                ),
                InlineKeyboardButton(
                    text="📁 مشاهده و مدیریت آرشیو",
                    callback_data="archive"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⚙️ تنظیمات سیستم",
                    callback_data="settings"
                ),
                InlineKeyboardButton(
                    text="🗑 پاکسازی کامل آرشیو",
                    callback_data="clear_confirm"
                )
            ]
        ]
    )


# ============================================================
# NEWS MENU
# ============================================================
#
# فقط گزینه‌های ضروری
#
# ============================================================

def news_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="📝 ارسال متن خبر",
                    callback_data="news_text"
                ),
                InlineKeyboardButton(
                    text="🔗 ارسال لینک Gamefa",
                    callback_data="news_link"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data="home"
                )
            ]
        ]
    )


# ============================================================
# ARCHIVE MENU
# ============================================================

def archive_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="📚 آخرین اخبار",
                    callback_data="archive_latest"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🗑 پاکسازی آرشیو",
                    callback_data="clear_confirm"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data="home"
                )
            ]
        ]
    )


# ============================================================
# SETTINGS MENU
# ============================================================

def settings_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="📢 کانال انتشار",
                    callback_data="setting_channel"
                ),
                InlineKeyboardButton(
                    text="🧠 مدل AI",
                    callback_data="setting_model"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🖼 حالت تصویر",
                    callback_data="setting_image"
                ),
                InlineKeyboardButton(
                    text="✍️ قالب خبر",
                    callback_data="setting_format"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data="home"
                )
            ]
        ]
    )


# ============================================================
# CLEAR MENU
# ============================================================

def clear_confirm_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⚠️ بله، پاک کن",
                    callback_data="clear_yes"
                ),
                InlineKeyboardButton(
                    text="❌ لغو",
                    callback_data="home"
                )
            ]
        ]
    )


# ============================================================
# BACK MENU
# ============================================================

def back_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data="home"
                )
            ]
        ]
    )


# ============================================================
# PROCESS NEWS
# ============================================================

async def process_news(
    message,
    text
):

    user_id = message.from_user.id

    if user_id in processing_users:

        await message.answer(
            "⏳ یک خبر در حال پردازش است. لطفاً کمی صبر کن."
        )

        return

    processing_users.add(
        user_id
    )

    try:

        url = extract_url(
            text
        )

        source_image = ""

        article_title = ""

        article_body = ""

        source = text

        # ====================================================
        # GAMEFA URL
        # ====================================================

        if url:

            status = await message.answer(
                "⏳ در حال دریافت اطلاعات خبر از Gamefa..."
            )

            try:

                article = await fetch_gamefa(
                    url
                )

            except Exception as error:

                await status.edit_text(
                    "❌ خطا در دریافت مقاله:\n\n"
                    + str(error)[:1500],
                    reply_markup=main_menu()
                )

                return

            source_image = article.get(
                "image",
                ""
            )

            article_title = article.get(
                "title",
                ""
            )

            article_body = article.get(
                "body",
                ""
            )

            source = (
                "TITLE:\n"
                + article_title
                + "\n\n"
                "DESCRIPTION:\n"
                + article.get(
                    "description",
                    ""
                )
                + "\n\n"
                "ARTICLE:\n"
                + article_body
            )

            try:

                await status.edit_text(
                    "✍️ اطلاعات خبر دریافت شد.\n"
                    "در حال آماده‌سازی متن..."
                )

            except Exception:
                pass

        # ====================================================
        # DUPLICATE
        # ====================================================

        if duplicate(source):

            await message.answer(
                "⚠️ این خبر یا یک خبر بسیار مشابه قبلاً "
                "در آرشیو وجود دارد.",
                reply_markup=main_menu()
            )

            return

        # ====================================================
        # AI
        # ====================================================

        try:

            generated = await generate_news(
                source
            )

        except Exception as error:

            await message.answer(
                "❌ خطا در پردازش هوش مصنوعی:\n\n"
                + str(error)[:1500],
                reply_markup=main_menu()
            )

            return

        post = format_post(
            generated
        )

        if not post:

            raise RuntimeError(
                "متن تولیدشده خالی است."
            )

        # ====================================================
        # IMAGE
        # ====================================================

        image_path = await find_best_image(
            source_image
        )

        # ====================================================
        # MEMORY
        # ====================================================

        memory.append(
            {
                "source": source[:16000],
                "post": post,
                "url": url or ""
            }
        )

        save_memory()

        # ====================================================
        # PREPARE
        # ====================================================

        prepared[user_id] = {
            "text": post,
            "image": (
                str(image_path)
                if image_path
                else ""
            )
        }

        # ====================================================
        # AUTOMATIC PREVIEW
        # ====================================================
        #
        # چون دکمه مشاهده پیش‌نمایش حذف شده،
        # خبر بلافاصله بعد از آماده‌شدن نمایش داده می‌شود.
        #
        # ====================================================

        if image_path:

            try:

                await message.answer_photo(
                    FSInputFile(
                        image_path
                    ),
                    caption=post,
                    parse_mode=ParseMode.HTML
                )

            except Exception as error:

                log.warning(
                    "Preview photo error: %s",
                    error
                )

                await message.answer(
                    post,
                    parse_mode=ParseMode.HTML
                )

        else:

            await message.answer(
                post,
                parse_mode=ParseMode.HTML
            )

        # ====================================================
        # READY MESSAGE
        # ====================================================

        await message.answer(
            "✅ خبر آماده انتشار است.\n\n"
            "برای انتشار می‌توانی از دستور /publish استفاده کنی.",
            reply_markup=main_menu()
        )

    except Exception as error:

        log.exception(
            "Process news error"
        )

        await message.answer(
            "❌ خطایی هنگام پردازش خبر رخ داد:\n\n"
            + str(error)[:1500],
            reply_markup=main_menu()
        )

    finally:

        processing_users.discard(
            user_id
        )


# ============================================================
# ROUTER
# ============================================================

router = Router()


# ============================================================
# START
# ============================================================

@router.message(Command("start"))
async def start_handler(
    message: Message
):

    if not is_admin(message):

        await message.answer(
            "⛔ این ربات خصوصی است."
        )

        return

    await message.answer(
        "✨ <b>پنل مدیریت Gamefa</b>\n\n"
        "به پنل مدیریت اخبار خوش آمدید.\n"
        "از منوی زیر عملیات موردنظر را انتخاب کنید.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

@router.callback_query()
async def callback_handler(
    callback: CallbackQuery
):

    if not is_admin_id(
        callback.from_user.id
    ):

        await callback.answer(
            "⛔ دسترسی ندارید.",
            show_alert=True
        )

        return

    data = callback.data

    await callback.answer()

    # ========================================================
    # HOME
    # ========================================================

    if data == "home":

        await callback.message.edit_text(
            "✨ <b>پنل مدیریت Gamefa</b>\n\n"
            "یک گزینه را انتخاب کنید:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )

        return

    # ========================================================
    # NEWS
    # ========================================================

    if data == "news_new":

        await callback.message.edit_text(
            "🔎 <b>بررسی خبر جدید</b>\n\n"
            "نوع ورودی خبر را انتخاب کنید:",
            parse_mode=ParseMode.HTML,
            reply_markup=news_menu()
        )

        return

    # ========================================================
    # NEWS TEXT
    # ========================================================

    if data == "news_text":

        await callback.message.edit_text(
            "📝 <b>ارسال متن خبر</b>\n\n"
            "متن خبر را همین‌جا ارسال کن.\n\n"
            "ربات متن را با AI ویرایش کرده و "
            "بعد از آماده‌شدن، پیش‌نمایش را "
            "به‌صورت خودکار نمایش می‌دهد.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # NEWS LINK
    # ========================================================

    if data == "news_link":

        await callback.message.edit_text(
            "🔗 <b>ارسال لینک Gamefa</b>\n\n"
            "لینک مقاله Gamefa را ارسال کن.\n\n"
            "اطلاعات مقاله و تصویر اصلی آن "
            "در صورت وجود دریافت می‌شود.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # ARCHIVE
    # ========================================================

    if data == "archive":

        await callback.message.edit_text(
            "📁 <b>مدیریت آرشیو</b>\n\n"
            "گزینه موردنظر را انتخاب کنید:",
            parse_mode=ParseMode.HTML,
            reply_markup=archive_menu()
        )

        return

    # ========================================================
    # ARCHIVE LATEST
    # ========================================================

    if data == "archive_latest":

        if not memory:

            text = (
                "📚 آرشیو خالی است."
            )

        else:

            latest = memory[-10:]

            lines = [
                "📚 <b>آخرین اخبار آرشیو</b>\n"
            ]

            for index, item in enumerate(
                reversed(latest),
                1
            ):

                post = item.get(
                    "post",
                    ""
                )

                clean = re.sub(
                    r"<[^>]+>",
                    "",
                    post
                )

                first_line = (
                    clean.splitlines()[0]
                    if clean
                    else "خبر بدون عنوان"
                )

                lines.append(
                    f"{index}. {first_line[:100]}"
                )

            text = "\n".join(
                lines
            )

        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # SETTINGS
    # ========================================================

    if data == "settings":

        await callback.message.edit_text(
            "⚙️ <b>تنظیمات سیستم</b>\n\n"
            "یک بخش را انتخاب کنید:",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_menu()
        )

        return

    # ========================================================
    # CHANNEL
    # ========================================================

    if data == "setting_channel":

        await callback.message.edit_text(
            "📢 <b>کانال انتشار</b>\n\n"
            "کانال فعلی:\n"
            f"<code>{escape_html(CHANNEL_ID)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # MODEL
    # ========================================================

    if data == "setting_model":

        await callback.message.edit_text(
            "🧠 <b>مدل هوش مصنوعی</b>\n\n"
            f"<code>{escape_html(MODEL)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # IMAGE
    # ========================================================

    if data == "setting_image":

        await callback.message.edit_text(
            "🖼 <b>سیستم تصویر</b>\n\n"
            "حالت فعلی:\n"
            "✅ فقط تصویر اصلی مقاله\n\n"
            "اگر مقاله تصویر نداشته باشد، "
            "ربات تصویر تصادفی از اینترنت انتخاب نمی‌کند.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # FORMAT
    # ========================================================

    if data == "setting_format":

        await callback.message.edit_text(
            "✍️ <b>قالب خبر</b>\n\n"
            "• تیتر فارسی\n"
            "• شروع فارسی متن\n"
            "• یک پاراگراف\n"
            "• متن روان خبری\n"
            "• امضای Gamefa\n"
            "• دسته‌بندی خودکار",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # CLEAR
    # ========================================================

    if data == "clear_confirm":

        await callback.message.edit_text(
            "⚠️ <b>پاکسازی کامل آرشیو</b>\n\n"
            "تمام خبرهای ذخیره‌شده حذف خواهند شد.\n\n"
            "این عملیات قابل بازگشت نیست.",
            parse_mode=ParseMode.HTML,
            reply_markup=clear_confirm_menu()
        )

        return

    # ========================================================
    # CLEAR YES
    # ========================================================

    if data == "clear_yes":

        memory.clear()

        save_memory()

        prepared.clear()

        await callback.message.edit_text(
            "✅ <b>آرشیو پاک شد.</b>\n\n"
            "تمام اخبار ذخیره‌شده حذف شدند.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )

        return


# ============================================================
# PUBLISH FUNCTION
# ============================================================

async def publish_news(
    message,
    user_id
):

    item = prepared.get(
        user_id
    )

    if not item:

        await message.answer(
            "❌ هنوز خبری برای انتشار آماده نشده است.",
            reply_markup=main_menu()
        )

        return

    text = item.get(
        "text",
        ""
    )

    image = item.get(
        "image",
        ""
    )

    try:

        # ====================================================
        # WITH IMAGE
        # ====================================================

        if (
            image
            and Path(image).exists()
        ):

            try:

                await message.bot.send_photo(
                    CHANNEL_ID,
                    FSInputFile(
                        image
                    ),
                    caption=text,
                    parse_mode=ParseMode.HTML
                )

            except Exception as error:

                log.warning(
                    "Photo publish failed: %s",
                    error
                )

                await message.bot.send_message(
                    CHANNEL_ID,
                    text,
                    parse_mode=ParseMode.HTML
                )

        # ====================================================
        # TEXT ONLY
        # ====================================================

        else:

            await message.bot.send_message(
                CHANNEL_ID,
                text,
                parse_mode=ParseMode.HTML
            )

        await message.answer(
            "✅ <b>خبر با موفقیت منتشر شد.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )

        prepared.pop(
            user_id,
            None
        )

    except Exception as error:

        log.exception(
            "Publish error"
        )

        await message.answer(
            "❌ خطا هنگام انتشار:\n\n"
            + str(error)[:1500],
            reply_markup=main_menu()
        )


# ============================================================
# COMMAND: PUBLISH
# ============================================================

@router.message(Command("publish"))
async def publish_command(
    message: Message
):

    if not is_admin(message):
        return

    await publish_news(
        message,
        message.from_user.id
    )


# ============================================================
# COMMAND: STATS
# ============================================================
#
# دکمه آمار حذف شده،
# اما دستور برای مواقع ضروری باقی مانده است.
#
# ============================================================

@router.message(Command("stats"))
async def stats_command(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "📊 تعداد اخبار آرشیو: "
        + str(len(memory)),
        reply_markup=main_menu()
    )


# ============================================================
# COMMAND: CLEAR
# ============================================================

@router.message(Command("clear"))
async def clear_command(
    message: Message
):

    if not is_admin(message):
        return

    memory.clear()

    save_memory()

    prepared.clear()

    await message.answer(
        "✅ آرشیو پاک شد.",
        reply_markup=main_menu()
    )


# ============================================================
# TEXT MESSAGE
# ============================================================

@router.message(F.text)
async def text_handler(
    message: Message
):

    if not is_admin(message):
        return

    text = (
        message.text or ""
    ).strip()

    if not text:
        return

    # دستورات قبلاً مدیریت شده‌اند
    if text.startswith("/"):
        return

    await process_news(
        message,
        text
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    # ========================================================
    # CHECK BOT TOKEN
    # ========================================================

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN تنظیم نشده است."
        )

    # ========================================================
    # CHECK OPENAI
    # ========================================================

    if not OPENAI_API_KEY:

        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده است."
        )

    # ========================================================
    # CHECK ADMIN
    # ========================================================

    if not ADMIN_ID:

        raise RuntimeError(
            "ADMIN_ID تنظیم نشده است."
        )

    # ========================================================
    # LOAD MEMORY
    # ========================================================

    load_memory()

    # ========================================================
    # BOT
    # ========================================================

    bot = Bot(
        token=BOT_TOKEN
    )

    dispatcher = Dispatcher()

    dispatcher.include_router(
        router
    )

    # ========================================================
    # LOG
    # ========================================================

    log.info(
        "========================================"
    )

    log.info(
        "Gamefa Bot started successfully."
    )

    log.info(
        "Admin ID: %s",
        ADMIN_ID
    )

    log.info(
        "Channel: %s",
        CHANNEL_ID
    )

    log.info(
        "Model: %s",
        MODEL
    )

    log.info(
        "Memory: %s news",
        len(memory)
    )

    log.info(
        "========================================"
    )

    # ========================================================
    # POLLING
    # ========================================================

    await dispatcher.start_polling(
        bot,
        allowed_updates=dispatcher.resolve_used_update_types()
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
