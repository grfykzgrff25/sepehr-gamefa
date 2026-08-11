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

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
).strip()

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    ""
).strip()

CHANNEL_ID = os.getenv(
    "CHANNEL_ID",
    "@Gamefa_official"
).strip()

MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5.4-mini"
).strip()


# ============================================================
# ADMIN ID
# ============================================================

try:
    ADMIN_ID = int(
        os.getenv(
            "ADMIN_ID",
            "0"
        ) or "0"
    )

except (ValueError, TypeError):
    ADMIN_ID = 0


# ============================================================
# MEMORY
# ============================================================

MEMORY_FILE = Path(
    "news_memory.json"
)

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

log = logging.getLogger(
    "gamefa_bot"
)


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

    if isinstance(text, dict):

        text = (
            text.get("title", "")
            + "\n"
            + text.get("body", "")
        )

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

    return bool(
        PERSIAN_RE.match(
            clean[0]
        )
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
        "call of duty"
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
# CLEAN AI TEXT
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

    # حذف ایموجی‌های ابتدای خط
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

    # یک پاراگراف
    body = " ".join(
        body_parts
    ).strip()

    # ========================================================
    # CATEGORY
    # ========================================================

    category = detect_category(
        title
        + " "
        + body
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

    if body:

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

    parsed = urlparse(
        url
    )

    if "gamefa.com" not in (
        parsed.netloc.lower()
    ):

        raise ValueError(
            "فقط لینک Gamefa پشتیبانی می‌شود."
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151 Safari/537.36"
        )
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
    # REMOVE UNNECESSARY ELEMENTS
    # ========================================================

    for element in soup([
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "form",
        "aside",
        "header",
        "iframe"
    ]):

        element.decompose()

    # ========================================================
    # TITLE
    # ========================================================

    h1 = soup.find(
        "h1"
    )

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

        if (
            meta
            and meta.get("content")
        ):

            description = (
                meta["content"]
                .strip()
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

        if (
            meta
            and meta.get("content")
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
        or soup.find("main")
        or soup
    )

    for element in article.find_all([
        "script",
        "style",
        "noscript",
        "nav",
        "footer",
        "aside",
        "form",
        "iframe",
        "button"
    ]):

        element.decompose()

    # ========================================================
    # PARAGRAPHS
    # ========================================================

    paragraphs = []

    for paragraph in article.find_all(
        "p"
    ):

        text = paragraph.get_text(
            " ",
            strip=True
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        ).strip()

        if len(text) < 40:
            continue

        ignored_phrases = [
            "مطالب مرتبط",
            "اخبار مرتبط",
            "تبلیغات",
            "عضویت در کانال",
            "دنبال کنید",
            "ارسال دیدگاه",
            "ثبت دیدگاه",
            "gamefa.com"
        ]

        if (
            any(
                phrase.lower()
                in text.lower()
                for phrase in ignored_phrases
            )
            and len(text) < 180
        ):

            continue

        paragraphs.append(
            text
        )

    # ========================================================
    # FALLBACK
    # ========================================================

    if not paragraphs:

        for block in article.find_all([
            "div",
            "section"
        ]):

            text = block.get_text(
                " ",
                strip=True
            )

            text = re.sub(
                r"\s+",
                " ",
                text
            ).strip()

            if (
                80
                <= len(text)
                <= 1500
            ):

                paragraphs.append(
                    text
                )

    # ========================================================
    # REMOVE DUPLICATE PARAGRAPHS
    # ========================================================

    unique_paragraphs = []

    seen = set()

    for paragraph in paragraphs:

        key = norm(
            paragraph
        )

        if not key:
            continue

        if key in seen:
            continue

        seen.add(key)

        unique_paragraphs.append(
            paragraph
        )

    # ========================================================
    # FULL ARTICLE
    # ========================================================

    body = "\n".join(
        unique_paragraphs
    )

    # حداکثر متن ورودی به AI
    body = body[:30000]

    log.info(
        "Gamefa article extracted: %d paragraphs / %d chars",
        len(unique_paragraphs),
        len(body)
    )

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
تو ویراستار حرفه‌ای اخبار کانال Gamefa هستی.

وظیفه تو این است که مقاله کامل داده‌شده را بخوانی و
از آن یک خبر فارسی کوتاه، روان و خلاصه‌شده برای
کانال تلگرام Gamefa تولید کنی.

مهم‌ترین قانون:

پاراگراف اول مقاله را کپی نکن.

حتی اگر پاراگراف اول خودش خلاصه خبر باشد،
نباید همان متن را عیناً یا با تغییرات جزئی بازنویسی کنی.

باید کل ARTICLE را بخوانی و اطلاعات مهم چند بخش
مختلف مقاله را با هم ترکیب کنی.

==================================================
قوانین خلاصه‌سازی
==================================================

1. کل مقاله را بررسی کن.

2. فقط پاراگراف اول را مبنا قرار نده.

3. اگر مقاله اطلاعات کافی دارد، از اطلاعات
   حداقل 2 تا 4 بخش مختلف مقاله استفاده کن.

4. اطلاعات تکراری را حذف کن.

5. جزئیات غیرضروری را حذف کن.

6. اصل خبر و مهم‌ترین جزئیات را حفظ کن.

7. متن نهایی باید جمله‌بندی کاملاً جدید داشته باشد.

8. هیچ جمله‌ای را عیناً از مقاله کپی نکن.

9. متن نهایی معمولاً حدود 100 تا 160 کلمه باشد.

10. اگر مقاله کوتاه است، خلاصه نیز کوتاه‌تر باشد.

11. اگر مقاله بسیار طولانی است، فقط مهم‌ترین
    اطلاعات را انتخاب کن.

==================================================
قوانین تیتر
==================================================

12. خط اول فقط تیتر باشد.

13. تیتر حتماً با فارسی شروع شود.

مثال غلط:

GTA 6 دوباره خبرساز شد

مثال درست:

بازی GTA 6 دوباره خبرساز شد

==================================================
قوانین متن
==================================================

14. متن خبر فقط یک پاراگراف باشد.

15. متن خبر حتماً با فارسی شروع شود.

16. نام بازی‌ها، فیلم‌ها، شرکت‌ها و افراد
    را در متن حفظ کن.

17. متن فارسی و روان باشد.

18. اطلاعات ساختگی اضافه نکن.

19. هیچ لینک تولید نکن.

20. هیچ منبع تولید نکن.

21. هیچ هشتگ تولید نکن.

22. هیچ آیدی کانال تولید نکن.

23. ایموجی تولید نکن.

24. Markdown تولید نکن.

25. HTML تولید نکن.

==================================================
فرمت خروجی
==================================================

فقط دو بخش تولید کن:

خط اول:
تیتر

خطوط بعد:
یک پاراگراف خلاصه‌شده

هیچ توضیح دیگری اضافه نکن.

==================================================
اطلاعات مقاله
==================================================

TITLE:
{title}

DESCRIPTION:
{description}

ARTICLE:
{body}
"""


# ============================================================
# AI
# ============================================================

async def generate_news(
    article
):

    if not OPENAI_API_KEY:

        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده است."
        )

    client = AsyncOpenAI(
        api_key=OPENAI_API_KEY
    )

    prompt = PROMPT.format(
        title=article.get(
            "title",
            ""
        ),
        description=article.get(
            "description",
            ""
        ),
        body=article.get(
            "body",
            ""
        )
    )

    response = await client.responses.create(
        model=MODEL,

        instructions=(
            "کل مقاله را بخوان. "
            "پاراگراف اول را کپی نکن. "
            "از چند بخش مختلف مقاله اطلاعات استخراج کن "
            "و یک خلاصه جدید بساز. "
            "خروجی باید خلاصه واقعی باشد، نه کپی "
            "یا بازنویسی نزدیک پاراگراف اول."
        ),

        input=prompt,

        max_output_tokens=1200
    )

    result = (
        response.output_text
        or ""
    ).strip()

    return result


# ============================================================
# IMAGE DOWNLOAD
# ============================================================

async def download_image(
    url
):

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
                    response.headers
                    .get(
                        "Content-Type",
                        ""
                    )
                    .lower()
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
            "Article has no image."
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

    return None


# ============================================================
# MAIN MENU
# فقط 4 دکمه
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
# فقط ورودی خبر
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

    user_id = (
        message.from_user.id
        if message.from_user
        else 0
    )

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

        # ====================================================
        # GAMEFA LINK
        # ====================================================

        if url:

            status = await message.answer(
                "⏳ در حال دریافت کل مقاله Gamefa..."
            )

            try:

                article = await fetch_gamefa(
                    url
                )

            except Exception as error:

                await status.edit_text(
                    "❌ دریافت مقاله ناموفق بود.\n\n"
                    + str(error)[:1200],
                    reply_markup=main_menu()
                )

                return

            source_image = article.get(
                "image",
                ""
            )

            log.info(
                "Gamefa title: %s",
                article.get(
                    "title",
                    ""
                )
            )

            log.info(
                "Article body chars: %d",
                len(
                    article.get(
                        "body",
                        ""
                    )
                )
            )

            # =================================================
            # DUPLICATE
            # =================================================

            duplicate_source = (
                article.get(
                    "title",
                    ""
                )
                + "\n"
                + article.get(
                    "body",
                    ""
                )
            )

            if duplicate(
                duplicate_source
            ):

                await status.edit_text(
                    "⚠️ این خبر یا یک خبر بسیار مشابه "
                    "قبلاً در آرشیو وجود دارد.",
                    reply_markup=main_menu()
                )

                return

            try:

                await status.edit_text(
                    "✍️ کل مقاله دریافت شد.\n"
                    "🧠 در حال خلاصه‌سازی با هوش مصنوعی..."
                )

            except Exception:
                pass

        # ====================================================
        # RAW TEXT
        # ====================================================

        else:

            article = {
                "url": "",
                "title": "",
                "description": "",
                "body": text,
                "image": ""
            }

            if duplicate(
                text
            ):

                await message.answer(
                    "⚠️ این خبر یا یک خبر بسیار مشابه "
                    "قبلاً در آرشیو وجود دارد.",
                    reply_markup=main_menu()
                )

                return

            status = await message.answer(
                "🧠 در حال خلاصه‌سازی خبر..."
            )

        # ====================================================
        # AI
        # ====================================================

        try:

            generated = await generate_news(
                article
            )

        except Exception as error:

            log.exception(
                "AI generation error"
            )

            try:

                await status.edit_text(
                    "❌ خطا در تولید خبر:\n\n"
                    + str(error)[:1500],
                    reply_markup=main_menu()
                )

            except Exception:

                await message.answer(
                    "❌ خطا در تولید خبر:\n\n"
                    + str(error)[:1500],
                    reply_markup=main_menu()
                )

            return

        # ====================================================
        # FORMAT
        # ====================================================

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

        memory_source = (
            article.get(
                "title",
                ""
            )
            + "\n"
            + article.get(
                "body",
                ""
            )
        )

        memory.append(
            {
                "source": memory_source[:30000],
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
        # STATUS DELETE
        # ====================================================

        try:

            await status.delete()

        except Exception:
            pass

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

        # ====================================================
        # AUTO PUBLISH
        # ====================================================

        await publish_news(
            message,
            user_id
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

@router.message(
    Command("start")
)
async def start_handler(
    message: Message
):

    if not is_admin(
        message
    ):

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

    if data == "news_text":

        await callback.message.edit_text(
            "📝 <b>ارسال متن خبر</b>\n\n"
            "متن خبر را همین‌جا ارسال کن.\n\n"
            "ربات آن را خلاصه و برای انتشار آماده می‌کند.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    if data == "news_link":

        await callback.message.edit_text(
            "🔗 <b>ارسال لینک Gamefa</b>\n\n"
            "لینک مقاله Gamefa را ارسال کن.\n\n"
            "ربات کل مقاله را دریافت می‌کند، "
            "آن را با هوش مصنوعی خلاصه می‌کند "
            "و تصویر اصلی مقاله را در صورت وجود استفاده می‌کند.",
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

    if data == "archive_latest":

        if not memory:

            text = (
                "📚 آرشیو خالی است."
            )

        else:

            latest = memory[-10:]

            lines = [
                "📚 <b>آخرین اخبار آرشیو</b>",
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
                    f"{index}. "
                    f"{first_line[:100]}"
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

    if data == "setting_channel":

        await callback.message.edit_text(
            "📢 <b>کانال انتشار</b>\n\n"
            "کانال فعلی:\n"
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
            "حالت فعلی:\n\n"
            "✅ تصویر اصلی مقاله Gamefa\n\n"
            "اگر مقاله تصویر نداشته باشد، "
            "تصویر تصادفی از اینترنت انتخاب نمی‌شود.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu()
        )

        return

    if data == "setting_format":

        await callback.message.edit_text(
            "✍️ <b>قالب خبر</b>\n\n"
            "• تیتر فارسی\n"
            "• شروع فارسی متن\n"
            "• خلاصه واقعی\n"
            "• یک پاراگراف\n"
            "• حذف اطلاعات اضافی\n"
            "• دسته‌بندی خودکار\n"
            "• امضای Gamefa",
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

@router.message(
    Command("publish")
)
async def publish_command(
    message: Message
):

    if not is_admin(
        message
    ):
        return

    await publish_news(
        message,
        message.from_user.id
    )


# ============================================================
# COMMAND: STATS
# ============================================================

@router.message(
    Command("stats")
)
async def stats_command(
    message: Message
):

    if not is_admin(
        message
    ):
        return

    await message.answer(
        "📊 تعداد اخبار آرشیو: "
        + str(len(memory)),
        reply_markup=main_menu()
    )


# ============================================================
# COMMAND: CLEAR
# ============================================================

@router.message(
    Command("clear")
)
async def clear_command(
    message: Message
):

    if not is_admin(
        message
    ):
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

@router.message(
    F.text
)
async def text_handler(
    message: Message
):

    if not is_admin(
        message
    ):
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
        "Memory: %d news",
        len(memory)
    )

    log.info(
        "========================================"
    )

    await dispatcher.start_polling(
        bot,
        allowed_updates=(
            dispatcher.resolve_used_update_types()
        )
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
