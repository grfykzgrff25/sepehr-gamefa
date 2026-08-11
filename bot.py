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

try:
    ADMIN_ID = int(
        os.getenv("ADMIN_ID", "0") or "0"
    )
except (ValueError, TypeError):
    ADMIN_ID = 0

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
            memory = data[-MAX_MEMORY:]
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
    words_a = set(norm(a).split())
    words_b = set(norm(b).split())

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
# PERSIAN
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

    return bool(
        PERSIAN_RE.match(clean[0])
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
        "sony",
        "microsoft"
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
# AI TEXT CLEANER
# ============================================================

def clean_ai_text(text):

    text = text or ""

    # Markdown
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

    # Markdown links
    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text
    )

    # Channel ID
    text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        text
    )

    # Emojis at beginning
    text = re.sub(
        r"^\s*[🎮🎬📱📢🟣]\s*",
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

    raw_lines = [
        line.strip()
        for line in ai_text.splitlines()
        if line.strip()
    ]

    if not raw_lines:
        return ""

    # ========================================================
    # TITLE
    # ========================================================

    title = raw_lines[0]

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

    body_lines = raw_lines[1:]

    body_parts = []

    for line in body_lines:

        line = re.sub(
            r"^[🟣•\-–—\d.)]+\s*",
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
    # EXACTLY 7 CONTENT PARTS
    # ========================================================

    body_parts = body_parts[:7]

    while len(body_parts) < 7:

        body_parts.append(
            "جزئیات بیشتر این موضوع در گزارش اصلی ارائه شده است."
        )

    # ========================================================
    # CATEGORY
    # ========================================================

    category = detect_category(
        title
        + " "
        + " ".join(body_parts)
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

    # ========================================================
    # مهم‌ترین تغییر:
    #
    # قبلاً:
    #
    # body = "\n".join(body_parts)
    #
    # همین باعث می‌شد بعد از هر جمله/خط Enter بخورد.
    #
    # اکنون:
    # تمام بخش‌های خبر با فاصله به یک پاراگراف تبدیل می‌شوند.
    # ========================================================

    body = " ".join(
        part.strip()
        for part in body_parts
        if part.strip()
    )

    # حذف فاصله‌های اضافی
    body = re.sub(
        r"\s+",
        " ",
        body
    ).strip()

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

    # ========================================================
    # REMOVE UNNECESSARY
    # ========================================================

    for element in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "form",
            "aside",
            "header"
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
    # ARTICLE
    # ========================================================

    article = (
        soup.find("article")
        or soup.find(
            class_=re.compile(
                r"(article|post|entry|content)",
                re.I
            )
        )
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

        if len(text) < 30:
            continue

        if text in body_parts:
            continue

        body_parts.append(
            text
        )

    body = "\n".join(
        body_parts
    )

    # حجم مناسب برای AI
    body = body[:50000]

    return {
        "url": final_url,
        "title": title,
        "description": description,
        "body": body,
        "image": image
    }


# ============================================================
# AI PROMPT
# ============================================================

PROMPT = """
تو یک سردبیر حرفه‌ای اخبار فارسی برای رسانه Gamefa هستی.

وظیفه تو این است که مقاله ورودی را کامل بخوانی و از کل مقاله یک خبر فارسی دقیق، کوتاه و حرفه‌ای تولید کنی.

مهم‌ترین قانون:

هرگز فقط بند اول مقاله را کپی یا بازنویسی نکن.

باید کل مقاله را تحلیل کنی و اطلاعات مهم را از بخش‌های مختلف آن استخراج کنی.

1. خط اول خروجی فقط تیتر باشد.

2. بعد از تیتر دقیقاً 7 خط خبر تولید کن.

3. این 7 خط باید خلاصه واقعی کل مقاله باشند.

4. هر خط باید اطلاعات مفید و متفاوتی داشته باشد.

5. از کپی کردن جمله‌های مقاله خودداری کن.

6. ساختار جمله‌ها را نیز تغییر بده.

7. خبر باید حاصل تحلیل و بازنویسی کل مقاله باشد.

8. بند اول مقاله نباید بیش از سایر بخش‌ها مورد استفاده قرار بگیرد.

9. اگر اطلاعات مهمی در بخش‌های میانی یا پایانی مقاله وجود دارد، حتماً در خلاصه لحاظ کن.

10. اطلاعات ساختگی اضافه نکن.

قبل از نوشتن خروجی:

مرحله اول:
کل مقاله را بخوان.

مرحله دوم:
نکات مهم مقاله را استخراج کن.

مرحله سوم:
نکات تکراری و کم‌اهمیت را حذف کن.

مرحله چهارم:
مهم‌ترین اطلاعات را انتخاب کن.

مرحله پنجم:
اطلاعات انتخاب‌شده را با زبان خبری فارسی بازنویسی کن.

مرحله ششم:
آن‌ها را در دقیقاً 7 خط قرار بده.

در صورت وجود اطلاعات کافی، این موارد را پوشش بده:

خط 1:
مهم‌ترین اتفاق خبر.

خط 2:
جزئیات اصلی اتفاق.

خط 3:
اطلاعات مربوط به بازی، فیلم، سریال، شرکت یا شخص مرتبط.

خط 4:
جزئیات مهم دیگری که در بخش‌های دیگر مقاله آمده است.

خط 5:
تاریخ، پلتفرم، وضعیت پروژه یا اطلاعات مشابه.

خط 6:
یک نکته مهم دیگر از مقاله.

خط 7:
وضعیت فعلی، نتیجه یا اتفاق آینده.

این ساختار اجباری نیست و در صورت تفاوت موضوع، ترتیب را منطقی کن.

این موارد ممنوع هستند:

- کپی مستقیم بند اول
- کپی چند جمله از مقاله
- تغییر چند کلمه از جمله اصلی
- خلاصه کردن فقط پاراگراف اول
- استفاده از همان ترتیب جمله‌های مقاله

خبر باید از ترکیب اطلاعات کل مقاله ساخته شود.

تیتر باید:

- کوتاه باشد
- خبری باشد
- جذاب باشد
- مهم‌ترین اتفاق را منتقل کند
- با فارسی شروع شود

مثال غلط:

Netflix announces new season...

مثال درست:

نتفلیکس فصل جدید Squid Game را معرفی کرد

متن فارسی روان و طبیعی باشد.

نام‌های انگلیسی مهم را حفظ کن.

اگر جمله با نام انگلیسی شروع می‌شود، قبل از آن عبارت فارسی مناسب قرار بده.

مثال غلط:

Brad Pitt در فیلم جدید...

مثال درست:

برد پیت در فیلم جدید...

یا:

براساس گزارش جدید، Brad Pitt در فیلم جدید...

خروجی دقیقاً به شکل زیر باشد:

خط اول = تیتر

خط دوم = خبر
خط سوم = خبر
خط چهارم = خبر
خط پنجم = خبر
خط ششم = خبر
خط هفتم = خبر
خط هشتم = خبر

یعنی:

1 تیتر
7 خط خبر

هیچ خط خالی بین 7 خط خبر قرار نده.

شماره‌گذاری نکن.

بولت استفاده نکن.

Markdown استفاده نکن.

HTML استفاده نکن.

ایموجی استفاده نکن.

لینک تولید نکن.

آیدی کانال تولید نکن.

هیچ اطلاعاتی را حدس نزن.

هیچ تاریخ، عدد، نام، شرکت یا پلتفرمی را بدون وجود در مقاله اضافه نکن.

اگر خبر درباره شایعه است، آن را به عنوان شایعه بیان کن.

اگر چیزی رسماً تأیید شده، آن را به عنوان تأیید رسمی بیان کن.

تفاوت بین شایعه، گزارش و تأیید رسمی را حفظ کن.

اکنون کل مقاله را تحلیل کن و خبر را دقیقاً طبق قوانین بالا تولید کن.
"""


# ============================================================
# AI GENERATION
# ============================================================

async def generate_news(source):

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده است."
        )

    client = AsyncOpenAI(
        api_key=OPENAI_API_KEY
    )

    input_text = (
        "مقاله زیر را کامل بخوان و تحلیل کن.\n\n"
        "هرگز فقط بند اول را خلاصه نکن.\n"
        "اطلاعات را از کل مقاله استخراج کن.\n"
        "خروجی باید یک تیتر و دقیقاً 7 خط خبر باشد.\n\n"
        "================ ARTICLE ================\n"
        + source
        + "\n\n"
        "================ END ARTICLE ================\n"
    )

    response = await client.responses.create(
        model=MODEL,
        instructions=PROMPT,
        input=input_text,
        max_output_tokens=1800
    )

    result = (
        response.output_text
        or ""
    ).strip()

    if not result:
        raise RuntimeError(
            "AI خروجی خالی تولید کرد."
        )

    return result


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

        path.write_bytes(data)

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
        return None

    return await download_image(
        source_image
    )


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🔎 بررسی خبر جدید",
                    callback_data="news_new"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📁 آرشیو",
                    callback_data="archive"
                ),
                InlineKeyboardButton(
                    text="📊 آمار",
                    callback_data="stats"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🤖 وضعیت AI",
                    callback_data="ai_status"
                ),
                InlineKeyboardButton(
                    text="⚙️ تنظیمات",
                    callback_data="settings"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📋 راهنما",
                    callback_data="help"
                )
            ]
        ]
    )


# ============================================================
# NEWS MENU
# ============================================================

def news_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="📝 ارسال خبر",
                    callback_data="news_text"
                )
            ],

            [
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
                )
            ],

            [
                InlineKeyboardButton(
                    text="🧠 مدل AI",
                    callback_data="setting_model"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🖼 سیستم تصویر",
                    callback_data="setting_image"
                )
            ],

            [
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
# CONFIRM CLEAR
# ============================================================

def clear_confirm_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⚠️ بله، پاک کن",
                    callback_data="clear_yes"
                )
            ],

            [
                InlineKeyboardButton(
                    text="❌ لغو",
                    callback_data="home"
                )
            ]
        ]
    )


# ============================================================
# BACK
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
            "⏳ یک خبر در حال پردازش است. لطفاً صبر کن."
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

        status = None

        # ====================================================
        # GAMEFA URL
        # ====================================================

        if url:

            status = await message.answer(
                "⏳ در حال دریافت کامل مقاله از Gamefa..."
            )

            article = await fetch_gamefa(
                url
            )

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

            description = article.get(
                "description",
                ""
            )

            source = (
                "عنوان مقاله:\n"
                + article_title
                + "\n\n"
                "توضیحات مقاله:\n"
                + description
                + "\n\n"
                "متن کامل مقاله:\n"
                + article_body
            )

            if status:

                try:
                    await status.edit_text(
                        "🧠 مقاله کامل دریافت شد.\n"
                        "در حال استخراج نکات مهم و خلاصه‌سازی..."
                    )
                except Exception:
                    pass

        # ====================================================
        # DUPLICATE
        # ====================================================

        if duplicate(source):

            await message.answer(
                "⚠️ این خبر یا یک خبر بسیار مشابه قبلاً در آرشیو وجود دارد.",
                reply_markup=main_menu()
            )

            return

        # ====================================================
        # AI
        # ====================================================

        if status:

            try:
                await status.edit_text(
                    "🧠 در حال تحلیل کل مقاله...\n"
                    "این مرحله ممکن است کمی طول بکشد."
                )
            except Exception:
                pass

        generated = await generate_news(
            source
        )

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
                "source": source[:20000],
                "post": post,
                "url": url or ""
            }
        )

        memory[:] = memory[-MAX_MEMORY:]

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
        # PREVIEW
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

        await message.answer(
            "✅ خبر آماده است.",
            reply_markup=main_menu()
        )

    except Exception as error:

        log.exception(
            "News processing error"
        )

        await message.answer(
            "❌ خطا هنگام پردازش خبر:\n\n"
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
        "از منوی زیر عملیات موردنظر را انتخاب کن.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )


# ============================================================
# CALLBACK
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
            "یک گزینه را انتخاب کن:",
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
            "نوع ورودی را انتخاب کن:",
            parse_mode=ParseMode.HTML,
            reply_markup=news_menu()
        )

        return

    if data == "news_text":

        await callback.message.edit_text(
            "📝 <b>ارسال خبر</b>\n\n"
            "متن خبر را ارسال کن.\n\n"
            "هوش مصنوعی آن را تحلیل و به خلاصه ۷ خطی تبدیل می‌کند.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    if data == "news_link":

        await callback.message.edit_text(
            "🔗 <b>ارسال لینک Gamefa</b>\n\n"
            "لینک مقاله Gamefa را ارسال کن.\n\n"
            "ربات کل مقاله را دریافت می‌کند و فقط بند اول را کپی نمی‌کند؛ "
            "بلکه محتوای کامل مقاله را تحلیل و خلاصه می‌کند.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # ARCHIVE
    # ========================================================

    if data == "archive":

        await callback.message.edit_text(
            "📁 <b>آرشیو اخبار</b>\n\n"
            "گزینه موردنظر را انتخاب کن:",
            parse_mode=ParseMode.HTML,
            reply_markup=archive_menu()
        )

        return

    if data == "archive_latest":

        if not memory:

            text = (
                "📚 آرشیو خالی است."
            )

        else:

            latest = memory[-10:]

            lines = [
                "📚 <b>آخرین اخبار</b>",
                ""
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
    # STATS
    # ========================================================

    if data == "stats":

        await callback.message.edit_text(
            "📊 <b>آمار ربات</b>\n\n"
            f"📰 تعداد اخبار آرشیو: <b>{len(memory)}</b>\n"
            f"💾 ظرفیت حافظه: <b>{MAX_MEMORY}</b>\n"
            f"👤 مدیر اصلی: <code>{ADMIN_ID}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # AI STATUS
    # ========================================================

    if data == "ai_status":

        status = (
            "🟢 فعال"
            if OPENAI_API_KEY
            else "🔴 غیرفعال"
        )

        await callback.message.edit_text(
            "🤖 <b>وضعیت هوش مصنوعی</b>\n\n"
            f"وضعیت: {status}\n"
            f"مدل: <code>{escape_html(MODEL)}</code>\n\n"
            "حالت پردازش:\n"
            "✅ تحلیل کل مقاله\n"
            "✅ استخراج نکات مهم\n"
            "✅ خلاصه‌سازی\n"
            "✅ بازنویسی\n"
            "✅ خروجی ۷ خطی",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # SETTINGS
    # ========================================================

    if data == "settings":

        await callback.message.edit_text(
            "⚙️ <b>تنظیمات</b>\n\n"
            "یک بخش را انتخاب کن:",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_menu()
        )

        return

    if data == "setting_channel":

        await callback.message.edit_text(
            "📢 <b>کانال انتشار</b>\n\n"
            f"<code>{escape_html(CHANNEL_ID)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    if data == "setting_model":

        await callback.message.edit_text(
            "🧠 <b>مدل هوش مصنوعی</b>\n\n"
            f"<code>{escape_html(MODEL)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    if data == "setting_image":

        await callback.message.edit_text(
            "🖼 <b>سیستم تصویر</b>\n\n"
            "ربات در صورت وجود تصویر اصلی مقاله، "
            "همان تصویر را استفاده می‌کند.\n\n"
            "اگر مقاله تصویر نداشته باشد، "
            "تصویر تصادفی انتخاب نمی‌شود.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    if data == "setting_format":

        await callback.message.edit_text(
            "✍️ <b>قالب خبر</b>\n\n"
            "• تیتر فارسی\n"
            "• خلاصه دقیق ۷ خطی\n"
            "• تحلیل کل مقاله\n"
            "• عدم کپی بند اول\n"
            "• شروع فارسی\n"
            "• دسته‌بندی خودکار\n"
            "• امضای Gamefa",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # HELP
    # ========================================================

    if data == "help":

        await callback.message.edit_text(
            "📋 <b>راهنمای ربات</b>\n\n"
            "🔎 بررسی خبر جدید\n"
            "برای پردازش متن یا لینک Gamefa.\n\n"
            "🤖 پردازش AI\n"
            "کل مقاله تحلیل شده و خلاصه دقیق تولید می‌شود.\n\n"
            "📰 خروجی\n"
            "یک تیتر + دقیقاً ۷ خط خبر.\n\n"
            "📁 آرشیو\n"
            "ذخیره و بررسی اخبار پردازش‌شده.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    # ========================================================
    # CLEAR
    # ========================================================

    if data == "clear_confirm":

        await callback.message.edit_text(
            "⚠️ <b>پاکسازی آرشیو</b>\n\n"
            "تمام اخبار ذخیره‌شده حذف خواهند شد.\n"
            "این عملیات قابل بازگشت نیست.",
            parse_mode=ParseMode.HTML,
            reply_markup=clear_confirm_menu()
        )

        return

    if data == "clear_yes":

        memory.clear()

        save_memory()

        prepared.clear()

        await callback.message.edit_text(
            "✅ <b>آرشیو پاک شد.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu()
        )

        return


# ============================================================
# PUBLISH
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
            "❌ هنوز خبری آماده انتشار نیست.",
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

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN تنظیم نشده است."
        )

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده است."
        )

    if not ADMIN_ID:
        raise RuntimeError(
            "ADMIN_ID تنظیم نشده است."
        )

    load_memory()

    bot = Bot(
        token=BOT_TOKEN
    )

    dispatcher = Dispatcher()

    dispatcher.include_router(
        router
    )

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
        "Memory: %s articles",
        len(memory)
    )

    log.info(
        "========================================"
    )

    await dispatcher.start_polling(
        bot,
        allowed_updates=dispatcher.resolve_used_update_types()
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
