import os
import re
import json
import html
import asyncio
import logging
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, quote_plus
from difflib import SequenceMatcher

import aiohttp
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message,
    FSInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from openai import AsyncOpenAI


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY", ""
).strip()

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
        os.getenv("ADMIN_ID", "0").strip()
    )
except Exception:
    ADMIN_ID = 0

# کاربران اضافی:
# USER_ROLES مثال:
# 123456789:editor,987654321:publisher
USER_ROLES = os.getenv(
    "USER_ROLES",
    ""
).strip()

MEMORY_FILE = Path("news_memory.json")
SETTINGS_FILE = Path("bot_settings.json")
ARCHIVE_FILE = Path("news_archive.json")
SCHEDULE_FILE = Path("scheduled_news.json")

IMAGE_DIR = Path("images")
IMAGE_DIR.mkdir(exist_ok=True)

MAX_MEMORY = 1500
MAX_ARCHIVE = 3000

memory = []
archive = []
scheduled = []

prepared = {}

bot_settings = {
    "auto_publish": False,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("gamefa")


# ============================================================
# ROLES
# ============================================================

def get_roles():
    roles = {}

    if ADMIN_ID:
        roles[ADMIN_ID] = "admin"

    if USER_ROLES:
        for item in USER_ROLES.split(","):
            try:
                user_id, role = item.strip().split(":")
                roles[int(user_id)] = role.strip().lower()
            except Exception:
                continue

    return roles


def user_role(user_id):
    return get_roles().get(user_id)


def is_admin(message):
    return bool(
        message.from_user
        and user_role(message.from_user.id) == "admin"
    )


def can_edit(message):
    if not message.from_user:
        return False

    return user_role(message.from_user.id) in {
        "admin",
        "editor"
    }


def can_publish(message):
    if not message.from_user:
        return False

    return user_role(message.from_user.id) in {
        "admin",
        "publisher"
    }


# ============================================================
# FILE STORAGE
# ============================================================

def load_json(path, default):
    try:
        if not path.exists():
            return default

        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        return data

    except Exception as error:
        log.warning(
            "Could not load %s: %s",
            path,
            error
        )

        return default


def save_json(path, data):
    try:
        path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    except Exception as error:
        log.error(
            "Could not save %s: %s",
            path,
            error
        )


def load_all():
    global memory
    global archive
    global scheduled
    global bot_settings

    memory = load_json(
        MEMORY_FILE,
        []
    )

    archive = load_json(
        ARCHIVE_FILE,
        []
    )

    scheduled = load_json(
        SCHEDULE_FILE,
        []
    )

    bot_settings = load_json(
        SETTINGS_FILE,
        {
            "auto_publish": False
        }
    )


def save_all():
    save_json(
        MEMORY_FILE,
        memory[-MAX_MEMORY:]
    )

    save_json(
        ARCHIVE_FILE,
        archive[-MAX_ARCHIVE:]
    )

    save_json(
        SCHEDULE_FILE,
        scheduled
    )

    save_json(
        SETTINGS_FILE,
        bot_settings
    )


# ============================================================
# TEXT NORMALIZATION
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

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def similarity(a, b):
    na = norm(a)
    nb = norm(b)

    if not na or not nb:
        return 0

    return SequenceMatcher(
        None,
        na,
        nb
    ).ratio()


def duplicate(text):
    normalized = norm(text)

    if not normalized:
        return False

    for item in memory:

        old = item.get(
            "source",
            ""
        )

        if similarity(
            normalized,
            old
        ) >= 0.82:

            return True

    return False


def extract_url(text):
    match = re.search(
        r"https?://[^\s<>()]+",
        text or ""
    )

    if not match:
        return None

    return match.group(
        0
    ).rstrip(
        ".,)]}"
    )


def escape_html(text):
    return html.escape(
        text or "",
        quote=False
    )


# ============================================================
# PERSIAN DETECTION
# ============================================================

PERSIAN_RE = re.compile(
    r"[\u0600-\u06FF]"
)


def contains_persian(text):
    return bool(
        PERSIAN_RE.search(
            text or ""
        )
    )


def first_real_character(text):
    if not text:
        return ""

    clean = text.strip()

    clean = re.sub(
        r"^[🎮🎬📱🟣📰⚡🔥\s]+",
        "",
        clean
    )

    if not clean:
        return ""

    return clean[0]


def starts_with_persian(text):
    first = first_real_character(
        text
    )

    return bool(
        first
        and PERSIAN_RE.search(first)
    )


# ============================================================
# FORCE PERSIAN START
# ============================================================

def force_persian_start(
    text,
    title=False
):
    if not text:
        return ""

    text = text.strip()

    if starts_with_persian(text):
        return text

    if title:
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
    text = (text or "").lower()

    gaming_words = [
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
        "sony interactive",
        "microsoft gaming"
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
        "hollywood"
    ]

    technology_words = [
        "فناوری",
        "هوش مصنوعی",
        "موبایل",
        "گوشی",
        "پردازنده",
        "کارت گرافیک",
        "تکنولوژی",
        "technology",
        "artificial intelligence",
        "ai",
        "iphone",
        "android",
        "nvidia",
        "amd",
        "intel"
    ]

    if any(
        word in text
        for word in gaming_words
    ):
        return "🎮"

    if any(
        word in text
        for word in movie_words
    ):
        return "🎬"

    if any(
        word in text
        for word in technology_words
    ):
        return "📱"

    return "📱"


# ============================================================
# FORMAT POST
# ============================================================

def format_post(ai_text):
    ai_text = ai_text or ""

    ai_text = re.sub(
        r"\*\*(.*?)\*\*",
        r"\1",
        ai_text,
        flags=re.S
    )

    ai_text = re.sub(
        r"__(.*?)__",
        r"\1",
        ai_text,
        flags=re.S
    )

    ai_text = re.sub(
        r"`(.*?)`",
        r"\1",
        ai_text,
        flags=re.S
    )

    ai_text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        ai_text
    )

    lines = [
        x.strip()
        for x in ai_text.splitlines()
        if x.strip()
    ]

    if not lines:
        return ""

    title = lines[0]

    title = re.sub(
        r"^[🎮🎬📱]\s*",
        "",
        title
    ).strip()

    title = force_persian_start(
        title,
        title=True
    )

    body_lines = lines[1:]

    # تبدیل متن به یک پاراگراف
    body_parts = []

    for line in body_lines:

        line = re.sub(
            r"^\s*🟣\s*",
            "",
            line
        ).strip()

        if not line:
            continue

        line = force_persian_start(
            line
        )

        body_parts.append(
            line
        )

    # متن نهایی یک بند
    body = " ".join(
        body_parts
    )

    # محدودسازی به حدود ۷ خط تلگرامی
    # تعداد کاراکتر به صورت تقریبی کنترل می‌شود
    if len(body) > 1050:
        body = body[:1050].rsplit(
            " ",
            1
        )[0] + "..."

    category = detect_category(
        title + " " + body
    )

    title = (
        category
        + " "
        + title
    )

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
# GAMEFA ARTICLE FETCH
# ============================================================

async def fetch_gamefa(url):

    parsed = urlparse(url)

    if (
        "gamefa.com"
        not in parsed.netloc.lower()
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

    for element in soup([
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "form",
        "aside"
    ]):
        element.decompose()

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

    description = ""

    for attrs in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"}
    ]:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta and meta.get("content"):
            description = meta[
                "content"
            ].strip()
            break

    image = ""

    for attrs in [
        {"property": "og:image"},
        {"name": "twitter:image"},
        {"property": "og:image:url"}
    ]:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta and meta.get("content"):
            image = urljoin(
                final_url,
                meta["content"].strip()
            )
            break

    article = (
        soup.find("article")
        or soup
    )

    paragraphs = article.find_all([
        "p",
        "h2",
        "h3"
    ])

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
تو سردبیر حرفه‌ای کانال Gamefa هستی.

از اطلاعات ورودی یک خبر فارسی آماده انتشار بساز.

قوانین:

1. خط اول فقط تیتر باشد.

2. تیتر حتماً با عبارت فارسی شروع شود.

3. هرگز تیتر را با نام انگلیسی شروع نکن.

غلط:
Netflix نسخه آمریکایی Squid Game را لغو کرد

درست:
نتفلیکس نسخه آمریکایی Squid Game را لغو کرد

4. متن خبر باید فارسی، روان و رسانه‌ای باشد.

5. متن خبر باید فقط یک پاراگراف باشد.

6. متن باید حدود 7 خط تلگرام باشد.

7. طول متن را تقریباً بین 700 تا 1050 کاراکتر نگه دار.

8. مهم‌ترین اطلاعات خبر را در همان پاراگراف بیاور.

9. نام انگلیسی افراد، بازی‌ها، فیلم‌ها و شرکت‌ها را حفظ کن،
اما ابتدای جمله با آنها شروع نشود.

غلط:
Brad Pitt گفته است...

درست:
برد پیت در گفت‌وگویی تازه گفته است...

10. ابتدای هر جمله یا بخش جدید باید فارسی باشد.

11. هیچ جمله‌ای را با:
David
Brad
Netflix
Sony
Microsoft
Square Enix
Nintendo
یا هر عبارت انگلیسی دیگری شروع نکن.

12. اطلاعات ساختگی اضافه نکن.

13. منبع و لینک تولید نکن.

14. Markdown تولید نکن.

15. HTML تولید نکن.

16. ایموجی تولید نکن.

17. @Gamefa_official تولید نکن.

18. اگر خبر بازی است، تیتر با عبارت فارسی و سپس موضوع بازی شروع شود.

19. اگر فیلم یا سریال است، تیتر با عبارت فارسی شروع شود.

20. خروجی فقط تیتر و یک پاراگراف خبر باشد.
"""


async def generate_news(source):

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
# QUALITY SCORE
# ============================================================

def quality_score(title, body):

    score = 100

    if not title:
        score -= 30

    if not body:
        score -= 40

    if not contains_persian(
        title
    ):
        score -= 20

    if body:
        if len(body) < 400:
            score -= 15

        if len(body) > 1300:
            score -= 10

    if body:
        first_words = body.strip()[:80]

        english_start = re.match(
            r"^[A-Za-z]",
            first_words
        )

        if english_start:
            score -= 25

    return max(
        0,
        min(
            100,
            score
        )
    )


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

        extension = ".jpg"

        if "png" in content_type:
            extension = ".png"

        elif "webp" in content_type:
            extension = ".webp"

        filename = (
            hashlib.sha256(
                url.encode()
            ).hexdigest()[:20]
            + extension
        )

        path = IMAGE_DIR / filename

        path.write_bytes(data)

        return path

    except Exception as error:

        log.warning(
            "Image download error: %s",
            error
        )

        return None


# ============================================================
# IMAGE SEARCH
# ============================================================

async def search_image(query):

    if not query:
        return None

    query = re.sub(
        r"[🎮🎬📱🟣]",
        "",
        query
    ).strip()

    query = query[:220]

    # جستجوی دقیق‌تر
    search_url = (
        "https://www.bing.com/images/search"
        "?q="
        + quote_plus(query)
        + "&form=HDRSC2"
    )

    headers = {
        "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151 Safari/537.36"
    }

    try:

        timeout = aiohttp.ClientTimeout(
            total=25
        )

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout
        ) as session:

            async with session.get(
                search_url
            ) as response:

                if response.status != 200:
                    return None

                raw = await response.text(
                    errors="ignore"
                )

        soup = BeautifulSoup(
            raw,
            "html.parser"
        )

        candidates = []

        for item in soup.select(
            "a.iusc"
        ):

            metadata = item.get(
                "m"
            )

            if not metadata:
                continue

            try:

                info = json.loads(
                    metadata
                )

                image_url = (
                    info.get("murl")
                    or info.get("turl")
                )

                title = (
                    info.get("t")
                    or ""
                ).lower()

                # نتیجه‌هایی که هیچ شباهتی
                # به جستجو ندارند حذف شوند
                query_words = [
                    x for x in norm(query).split()
                    if len(x) > 2
                ]

                title_score = sum(
                    1
                    for word in query_words
                    if word in title
                )

                candidates.append(
                    (
                        title_score,
                        image_url
                    )
                )

            except Exception:
                continue

        candidates.sort(
            key=lambda x: x[0],
            reverse=True
        )

        seen = set()

        for _, image_url in candidates:

            if not image_url:
                continue

            if image_url in seen:
                continue

            seen.add(image_url)

            path = await download_image(
                image_url
            )

            if path:
                return path

    except Exception as error:

        log.warning(
            "Image search error: %s",
            error
        )

    return None


# ============================================================
# FIND BEST IMAGE
# ============================================================

async def find_best_image(
    source_image,
    title,
    body
):

    # 1. تصویر اصلی خبر
    if source_image:

        path = await download_image(
            source_image
        )

        if path:
            return path

    # 2. جستجوی دقیق فقط بر اساس عنوان
    clean_title = re.sub(
        r"^[🎮🎬📱]\s*",
        "",
        title or ""
    ).strip()

    if clean_title:

        path = await search_image(
            clean_title
        )

        if path:
            return path

    # 3. جستجوی عنوان + چند کلمه کلیدی
    # فقط اگر عنوان نتیجه نداده باشد
    if body:

        body_clean = re.sub(
            r"\s+",
            " ",
            body
        ).strip()

        query = (
            clean_title
            + " "
            + body_clean[:120]
        )

        path = await search_image(
            query
        )

        if path:
            return path

    # اگر تصویر مطمئن پیدا نشد:
    # بدون تصویر منتشر می‌شود
    return None


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 ساخت خبر",
                    callback_data="new_news"
                ),
                InlineKeyboardButton(
                    text="🗃 آرشیو",
                    callback_data="archive"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 انتشار",
                    callback_data="publish_menu"
                ),
                InlineKeyboardButton(
                    text="🖼 تصویر",
                    callback_data="image_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 آمار",
                    callback_data="stats"
                ),
                InlineKeyboardButton(
                    text="⚙️ تنظیمات",
                    callback_data="settings"
                )
            ]
        ]
    )


def preview_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 انتشار",
                    callback_data="publish_current"
                ),
                InlineKeyboardButton(
                    text="✏️ ویرایش",
                    callback_data="edit_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 بازنویسی",
                    callback_data="rewrite"
                ),
                InlineKeyboardButton(
                    text="🖼 تصویر",
                    callback_data="image_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎯 دسته‌بندی",
                    callback_data="category_menu"
                ),
                InlineKeyboardButton(
                    text="🗑 حذف",
                    callback_data="delete_current"
                )
            ]
        ]
    )


def edit_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ عنوان",
                    callback_data="edit_title"
                ),
                InlineKeyboardButton(
                    text="📝 متن",
                    callback_data="edit_body"
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ بازگشت",
                    callback_data="back_preview"
                )
            ]
        ]
    )


def image_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 جستجوی تصویر",
                    callback_data="search_new_image"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ حذف تصویر",
                    callback_data="remove_image"
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ بازگشت",
                    callback_data="back_preview"
                )
            ]
        ]
    )


def category_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎮 بازی",
                    callback_data="cat_game"
                ),
                InlineKeyboardButton(
                    text="🎬 فیلم",
                    callback_data="cat_movie"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 فناوری",
                    callback_data="cat_tech"
                )
            ]
        ]
    )


# ============================================================
# PREVIEW
# ============================================================

async def send_preview(
    message,
    item
):

    text = item.get(
        "text",
        ""
    )

    image = item.get(
        "image",
        ""
    )

    if image and Path(image).exists():

        try:

            await message.answer_photo(
                FSInputFile(image),
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=preview_keyboard()
            )

            return

        except Exception as error:

            log.warning(
                "Preview photo error: %s",
                error
            )

    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=preview_keyboard()
    )


# ============================================================
# PROCESS NEWS
# ============================================================

async def process_news(
    message,
    text
):

    url = extract_url(text)

    source_image = ""
    article_title = ""
    article_body = ""

    source = text

    if url:

        await message.answer(
            "⏳ در حال دریافت و بررسی خبر..."
        )

        article = await fetch_gamefa(
            url
        )

        source_image = article[
            "image"
        ]

        article_title = article[
            "title"
        ]

        article_body = article[
            "body"
        ]

        source = (
            "TITLE:\n"
            + article["title"]
            + "\n\n"
            "DESCRIPTION:\n"
            + article["description"]
            + "\n\n"
            "ARTICLE:\n"
            + article["body"]
        )

    # جلوگیری از تکرار
    if duplicate(source):

        await message.answer(
            "⚠️ این خبر یا خبر بسیار مشابه آن قبلاً دریافت شده است."
        )

        return

    await message.answer(
        "✍️ در حال نوشتن نسخه Gamefa..."
    )

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

    # متن ساده
    plain = re.sub(
        r"<[^>]+>",
        "",
        post
    )

    lines = [
        x.strip()
        for x in plain.splitlines()
        if x.strip()
    ]

    generated_title = (
        lines[0]
        if lines
        else article_title
    )

    generated_body = (
        " ".join(lines[1:])
        if len(lines) > 1
        else article_body
    )

    score = quality_score(
        generated_title,
        generated_body
    )

    await message.answer(
        "🖼 در حال بررسی تصویر مرتبط..."
    )

    image_path = await find_best_image(
        source_image,
        generated_title,
        generated_body
    )

    item = {
        "text": post,
        "image": (
            str(image_path)
            if image_path
            else ""
        ),
        "source": source[:16000],
        "url": url or "",
        "title": generated_title,
        "body": generated_body,
        "score": score,
        "created_at": datetime.now(
            timezone.utc
        ).isoformat()
    }

    user_id = (
        message.from_user.id
        if message.from_user
        else 0
    )

    prepared[user_id] = item

    # ذخیره در حافظه
    memory.append(
        {
            "source": source[:16000],
            "post": post,
            "url": url or "",
            "created_at": datetime.now(
                timezone.utc
            ).isoformat()
        }
    )

    # آرشیو
    archive.append(
        item
    )

    save_all()

    image_status = (
        "🖼 تصویر مرتبط پیدا شد"
        if image_path
        else "📄 تصویر مناسب پیدا نشد؛ خبر بدون تصویر آماده شد"
    )

    await message.answer(
        "✅ خبر آماده شد.\n\n"
        + "🎯 امتیاز کیفیت: "
        + str(score)
        + "/100\n"
        + image_status
    )

    await send_preview(
        message,
        item
    )

    # انتشار خودکار
    if bot_settings.get(
        "auto_publish",
        False
    ):

        if can_publish(message):

            await publish_item(
                message,
                item
            )


# ============================================================
# PUBLISH
# ============================================================

async def publish_item(
    message,
    item
):

    image = item.get(
        "image",
        ""
    )

    text = item.get(
        "text",
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
                    FSInputFile(image),
                    caption=text,
                    parse_mode=ParseMode.HTML
                )

            except Exception:

                # اگر تصویر مشکل داشت:
                # خبر حتماً متنی منتشر شود
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

        return True

    except Exception as error:

        log.exception(
            "Publish error"
        )

        await message.answer(
            "❌ خطا هنگام انتشار:\n"
            + str(error)[:1500]
        )

        return False


# ============================================================
# SCHEDULE
# ============================================================

async def schedule_item(
    user_id,
    item,
    minutes
):

    run_at = datetime.now(
        timezone.utc
    ) + timedelta(
        minutes=minutes
    )

    scheduled.append(
        {
            "user_id": user_id,
            "item": item,
            "run_at": run_at.isoformat()
        }
    )

    save_all()


async def scheduler_loop(bot):

    while True:

        try:

            now = datetime.now(
                timezone.utc
            )

            remaining = []

            for task in scheduled:

                try:

                    run_at = datetime.fromisoformat(
                        task["run_at"]
                    )

                    if now >= run_at:

                        item = task["item"]

                        image = item.get(
                            "image",
                            ""
                        )

                        text = item.get(
                            "text",
                            ""
                        )

                        if (
                            image
                            and Path(image).exists()
                        ):

                            try:

                                await bot.send_photo(
                                    CHANNEL_ID,
                                    FSInputFile(image),
                                    caption=text,
                                    parse_mode=ParseMode.HTML
                                )

                            except Exception:

                                await bot.send_message(
                                    CHANNEL_ID,
                                    text,
                                    parse_mode=ParseMode.HTML
                                )

                        else:

                            await bot.send_message(
                                CHANNEL_ID,
                                text,
                                parse_mode=ParseMode.HTML
                            )

                    else:

                        remaining.append(
                            task
                        )

                except Exception as error:

                    log.error(
                        "Scheduled task error: %s",
                        error
                    )

            scheduled[:] = remaining

            save_all()

        except Exception as error:

            log.error(
                "Scheduler error: %s",
                error
            )

        await asyncio.sleep(20)


# ============================================================
# RSS
# ============================================================

async def fetch_rss(url):

    try:

        headers = {
            "User-Agent":
                "Mozilla/5.0"
        }

        timeout = aiohttp.ClientTimeout(
            total=20
        )

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout
        ) as session:

            async with session.get(
                url
            ) as response:

                if response.status != 200:
                    return []

                raw = await response.text(
                    errors="ignore"
                )

        soup = BeautifulSoup(
            raw,
            "xml"
        )

        items = []

        for item in soup.find_all(
            "item"
        )[:10]:

            title = item.find("title")
            link = item.find("link")
            description = item.find(
                "description"
            )

            items.append(
                {
                    "title": (
                        title.get_text(
                            strip=True
                        )
                        if title
                        else ""
                    ),
                    "link": (
                        link.get_text(
                            strip=True
                        )
                        if link
                        else ""
                    ),
                    "description": (
                        description.get_text(
                            strip=True
                        )
                        if description
                        else ""
                    )
                }
            )

        return items

    except Exception as error:

        log.warning(
            "RSS error: %s",
            error
        )

        return []


# ============================================================
# ROUTER
# ============================================================

router = Router()


# ============================================================
# START
# ============================================================

@router.message(Command("start"))
async def start(message: Message):

    if not can_edit(message):
        await message.answer(
            "🔒 این ربات خصوصی است."
        )
        return

    await message.answer(
        "🎮 <b>Gamefa News Studio</b>\n\n"
        "سیستم آماده دریافت خبر است.\n\n"
        "از منوی زیر استفاده کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )


# ============================================================
# TEXT
# ============================================================

@router.message(F.text)
async def text_handler(message: Message):

    if not can_edit(message):
        return

    text = (
        message.text or ""
    ).strip()

    if not text:
        return

    if text.startswith("/"):
        return

    try:

        # اگر در حالت انتظار ویرایش هستیم
        state = prepared.get(
            message.from_user.id,
            {}
        )

        mode = state.get(
            "edit_mode"
        )

        if mode == "title":

            item = prepared[
                message.from_user.id
            ]

            item["title"] = text

            old_body = item.get(
                "body",
                ""
            )

            category = detect_category(
                text
                + " "
                + old_body
            )

            formatted_title = (
                category
                + " "
                + force_persian_start(
                    text,
                    True
                )
            )

            item["text"] = (
                "<b>"
                + escape_html(
                    formatted_title
                )
                + "</b>"
                + "\n\n🟣 "
                + escape_html(
                    old_body
                )
                + "\n\n"
                + "<b>🆔 @Gamefa_official</b>"
            )

            item.pop(
                "edit_mode",
                None
            )

            await message.answer(
                "✅ عنوان تغییر کرد."
            )

            await send_preview(
                message,
                item
            )

            return

        if mode == "body":

            item = prepared[
                message.from_user.id
            ]

            body = force_persian_start(
                text
            )

            if len(body) > 1050:
                body = body[:1050].rsplit(
                    " ",
                    1
                )[0] + "..."

            title = item.get(
                "title",
                ""
            )

            category = detect_category(
                title
                + " "
                + body
            )

            title = re.sub(
                r"^[🎮🎬📱]\s*",
                "",
                title
            )

            title = (
                category
                + " "
                + force_persian_start(
                    title,
                    True
                )
            )

            item["body"] = body

            item["text"] = (
                "<b>"
                + escape_html(title)
                + "</b>"
                + "\n\n🟣 "
                + escape_html(body)
                + "\n\n"
                + "<b>🆔 @Gamefa_official</b>"
            )

            item.pop(
                "edit_mode",
                None
            )

            await message.answer(
                "✅ متن تغییر کرد."
            )

            await send_preview(
                message,
                item
            )

            return

        await process_news(
            message,
            text
        )

    except Exception as error:

        log.exception(
            "Processing error"
        )

        await message.answer(
            "❌ خطا:\n"
            + str(error)[:1500]
        )


# ============================================================
# CALLBACKS
# ============================================================

@router.callback_query(F.data == "new_news")
async def cb_new_news(callback: CallbackQuery):

    await callback.answer()

    await callback.message.answer(
        "📝 خبر را همینجا ارسال کن.\n\n"
        "می‌توانی لینک Gamefa یا متن خام خبر را بفرستی."
    )


@router.callback_query(F.data == "back_preview")
async def cb_back_preview(callback: CallbackQuery):

    await callback.answer()

    item = prepared.get(
        callback.from_user.id
    )

    if item:
        await send_preview(
            callback.message,
            item
        )


# ============================================================
# EDIT
# ============================================================

@router.callback_query(F.data == "edit_menu")
async def cb_edit_menu(callback: CallbackQuery):

    await callback.answer()

    await callback.message.answer(
        "✏️ چه چیزی را می‌خواهی تغییر بدهی؟",
        reply_markup=edit_keyboard()
    )


@router.callback_query(F.data == "edit_title")
async def cb_edit_title(callback: CallbackQuery):

    await callback.answer()

    item = prepared.get(
        callback.from_user.id
    )

    if not item:
        return

    item["edit_mode"] = "title"

    await callback.message.answer(
        "✏️ عنوان جدید را ارسال کن:"
    )


@router.callback_query(F.data == "edit_body")
async def cb_edit_body(callback: CallbackQuery):

    await callback.answer()

    item = prepared.get(
        callback.from_user.id
    )

    if not item:
        return

    item["edit_mode"] = "body"

    await callback.message.answer(
        "📝 متن جدید خبر را ارسال کن:"
    )


# ============================================================
# REWRITE
# ============================================================

@router.callback_query(F.data == "rewrite")
async def cb_rewrite(callback: CallbackQuery):

    await callback.answer(
        "در حال بازنویسی..."
    )

    item = prepared.get(
        callback.from_user.id
    )

    if not item:
        return

    source = item.get(
        "source",
        item.get("text", "")
    )

    try:

        generated = await generate_news(
            source
        )

        post = format_post(
            generated
        )

        if not post:
            return

        plain = re.sub(
            r"<[^>]+>",
            "",
            post
        )

        lines = [
            x.strip()
            for x in plain.splitlines()
            if x.strip()
        ]

        item["text"] = post

        if lines:
            item["title"] = lines[0]

        if len(lines) > 1:
            item["body"] = " ".join(
                lines[1:]
            )

        item["score"] = quality_score(
            item.get("title", ""),
            item.get("body", "")
        )

        prepared[
            callback.from_user.id
        ] = item

        await send_preview(
            callback.message,
            item
        )

    except Exception as error:

        await callback.message.answer(
            "❌ خطا در بازنویسی:\n"
            + str(error)[:1000]
        )


# ============================================================
# IMAGE MENU
# ============================================================

@router.callback_query(F.data == "image_menu")
async def cb_image_menu(callback: CallbackQuery):

    await callback.answer()

    await callback.message.answer(
        "🖼 مدیریت تصویر:",
        reply_markup=image_keyboard()
    )


@router.callback_query(F.data == "search_new_image")
async def cb_search_image(callback: CallbackQuery):

    await callback.answer(
        "در حال جستجوی تصویر..."
    )

    item = prepared.get(
        callback.from_user.id
    )

    if not item:
        return

    path = await search_image(
        item.get(
            "title",
            ""
        )
    )

    if not path:

        await callback.message.answer(
            "⚠️ تصویر مناسبی پیدا نشد.\n"
            "خبر بدون تصویر باقی می‌ماند."
        )

        return

    item["image"] = str(path)

    await callback.message.answer(
        "✅ تصویر جدید انتخاب شد."
    )

    await send_preview(
        callback.message,
        item
    )


@router.callback_query(F.data == "remove_image")
async def cb_remove_image(callback: CallbackQuery):

    await callback.answer()

    item = prepared.get(
        callback.from_user.id
    )

    if not item:
        return

    item["image"] = ""

    await callback.message.answer(
        "❌ تصویر حذف شد."
    )

    await send_preview(
        callback.message,
        item
    )


# ============================================================
# CATEGORY
# ============================================================

@router.callback_query(F.data == "category_menu")
async def cb_category_menu(callback: CallbackQuery):

    await callback.answer()

    await callback.message.answer(
        "🎯 دسته‌بندی را انتخاب کن:",
        reply_markup=category_keyboard()
    )


async def change_category(
    callback,
    emoji
):

    item = prepared.get(
        callback.from_user.id
    )

    if not item:
        return

    title = item.get(
        "title",
        ""
    )

    title = re.sub(
        r"^[🎮🎬📱]\s*",
        "",
        title
    )

    body = item.get(
        "body",
        ""
    )

    title = (
        emoji
        + " "
        + force_persian_start(
            title,
            True
        )
    )

    item["text"] = (
        "<b>"
        + escape_html(title)
        + "</b>"
        + "\n\n🟣 "
        + escape_html(body)
        + "\n\n"
        + "<b>🆔 @Gamefa_official</b>"
    )

    item["title"] = title

    await callback.answer(
        "دسته‌بندی تغییر کرد."
    )

    await send_preview(
        callback.message,
        item
    )


@router.callback_query(F.data == "cat_game")
async def cb_cat_game(callback: CallbackQuery):
    await change_category(
        callback,
        "🎮"
    )


@router.callback_query(F.data == "cat_movie")
async def cb_cat_movie(callback: CallbackQuery):
    await change_category(
        callback,
        "🎬"
    )


@router.callback_query(F.data == "cat_tech")
async def cb_cat_tech(callback: CallbackQuery):
    await change_category(
        callback,
        "📱"
    )


# ============================================================
# DELETE
# ============================================================

@router.callback_query(F.data == "delete_current")
async def cb_delete_current(callback: CallbackQuery):

    await callback.answer()

    prepared.pop(
        callback.from_user.id,
        None
    )

    await callback.message.answer(
        "🗑 خبر از حالت آماده انتشار حذف شد."
    )


# ============================================================
# PUBLISH
# ============================================================

@router.callback_query(F.data == "publish_current")
async def cb_publish_current(callback: CallbackQuery):

    if user_role(
        callback.from_user.id
    ) not in {
        "admin",
        "publisher"
    }:

        await callback.answer(
            "شما دسترسی انتشار ندارید.",
            show_alert=True
        )

        return

    await callback.answer(
        "در حال انتشار..."
    )

    item = prepared.get(
        callback.from_user.id
    )

    if not item:
        await callback.message.answer(
            "❌ خبری آماده انتشار نیست."
        )
        return

    fake_message = callback.message

    ok = await publish_item(
        fake_message,
        item
    )

    if ok:

        await callback.message.answer(
            "✅ خبر با موفقیت منتشر شد."
        )

        prepared.pop(
            callback.from_user.id,
            None
        )


@router.callback_query(F.data == "publish_menu")
async def cb_publish_menu(callback: CallbackQuery):

    await callback.answer()

    item = prepared.get(
        callback.from_user.id
    )

    if not item:

        await callback.message.answer(
            "❌ خبری برای انتشار آماده نیست."
        )

        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 انتشار فوری",
                    callback_data="publish_current"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ 10 دقیقه بعد",
                    callback_data="schedule_10"
                ),
                InlineKeyboardButton(
                    text="⏰ 30 دقیقه بعد",
                    callback_data="schedule_30"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ 1 ساعت بعد",
                    callback_data="schedule_60"
                )
            ]
        ]
    )

    await callback.message.answer(
        "📢 نحوه انتشار را انتخاب کن:",
        reply_markup=keyboard
    )


async def do_schedule(
    callback,
    minutes
):

    item = prepared.get(
        callback.from_user.id
    )

    if not item:
        await callback.message.answer(
            "❌ خبری آماده نیست."
        )
        return

    await schedule_item(
        callback.from_user.id,
        item,
        minutes
    )

    await callback.answer(
        "زمان‌بندی شد."
    )

    await callback.message.answer(
        "⏰ خبر برای "
        + str(minutes)
        + " دقیقه بعد زمان‌بندی شد."
    )


@router.callback_query(F.data == "schedule_10")
async def cb_schedule_10(callback: CallbackQuery):
    await do_schedule(
        callback,
        10
    )


@router.callback_query(F.data == "schedule_30")
async def cb_schedule_30(callback: CallbackQuery):
    await do_schedule(
        callback,
        30
    )


@router.callback_query(F.data == "schedule_60")
async def cb_schedule_60(callback: CallbackQuery):
    await do_schedule(
        callback,
        60
    )


# ============================================================
# STATS
# ============================================================

@router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery):

    await callback.answer()

    games = 0
    movies = 0
    tech = 0

    for item in archive:

        title = item.get(
            "title",
            ""
        )

        category = detect_category(
            title
        )

        if category == "🎮":
            games += 1

        elif category == "🎬":
            movies += 1

        else:
            tech += 1

    image_count = sum(
        1
        for item in archive
        if item.get("image")
    )

    await callback.message.answer(
        "📊 <b>آمار Gamefa News Studio</b>\n\n"
        "📰 کل اخبار: "
        + str(len(archive))
        + "\n"
        "🧠 حافظه تکراری: "
        + str(len(memory))
        + "\n"
        "🖼 اخبار دارای تصویر: "
        + str(image_count)
        + "\n\n"
        "🎮 بازی: "
        + str(games)
        + "\n"
        "🎬 فیلم و سریال: "
        + str(movies)
        + "\n"
        "📱 فناوری: "
        + str(tech),
        parse_mode=ParseMode.HTML
    )


@router.message(Command("stats"))
async def stats_command(message: Message):

    if not can_edit(message):
        return

    await message.answer(
        "📊 تعداد اخبار آرشیو: "
        + str(len(archive))
        + "\n"
        "🧠 حافظه: "
        + str(len(memory))
    )


# ============================================================
# SETTINGS
# ============================================================

@router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery):

    await callback.answer()

    if not is_admin(
        callback.message
    ):
        await callback.message.answer(
            "🔒 فقط Admin."
        )
        return

    auto = bot_settings.get(
        "auto_publish",
        False
    )

    status = (
        "فعال ✅"
        if auto
        else "غیرفعال ❌"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "🔴 خاموش کردن انتشار خودکار"
                        if auto
                        else "🟢 فعال کردن انتشار خودکار"
                    ),
                    callback_data="toggle_auto"
                )
            ]
        ]
    )

    await callback.message.answer(
        "⚙️ <b>تنظیمات</b>\n\n"
        "📢 انتشار خودکار: "
        + status,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )


@router.callback_query(F.data == "toggle_auto")
async def cb_toggle_auto(callback: CallbackQuery):

    if not is_admin(
        callback.message
    ):
        await callback.answer(
            "دسترسی ندارید.",
            show_alert=True
        )
        return

    bot_settings[
        "auto_publish"
    ] = not bot_settings.get(
        "auto_publish",
        False
    )

    save_all()

    await callback.answer(
        "تنظیمات تغییر کرد."
    )

    await cb_settings(
        callback
    )


# ============================================================
# ARCHIVE SEARCH
# ============================================================

@router.callback_query(F.data == "archive")
async def cb_archive(callback: CallbackQuery):

    await callback.answer()

    await callback.message.answer(
        "🗃 <b>آرشیو اخبار</b>\n\n"
        "برای جستجو، نام بازی، فیلم، شرکت یا موضوع را ارسال کن.",
        parse_mode=ParseMode.HTML
    )


# ============================================================
# COMMAND SEARCH
# ============================================================

@router.message(Command("search"))
async def search_command(message: Message):

    if not can_edit(message):
        return

    parts = (
        message.text or ""
    ).split(
        maxsplit=1
    )

    if len(parts) < 2:

        await message.answer(
            "مثال:\n"
            "/search Spider-Man"
        )

        return

    query = norm(
        parts[1]
    )

    results = []

    for item in reversed(
        archive
    ):

        title = item.get(
            "title",
            ""
        )

        if query in norm(title):

            results.append(
                item
            )

        if len(results) >= 10:
            break

    if not results:

        await message.answer(
            "🔎 نتیجه‌ای پیدا نشد."
        )

        return

    text = "🗃 نتایج جستجو:\n\n"

    for index, item in enumerate(
        results,
        1
    ):

        text += (
            str(index)
            + ". "
            + item.get(
                "title",
                "بدون عنوان"
            )
            + "\n"
        )

    await message.answer(
        text
    )


# ============================================================
# CLEAR
# ============================================================

@router.message(Command("clear"))
async def clear_command(message: Message):

    if not is_admin(message):
        return

    memory.clear()

    save_all()

    await message.answer(
        "🧹 حافظه تشخیص تکراری پاک شد."
    )


# ============================================================
# PUBLISH COMMAND
# ============================================================

@router.message(Command("publish"))
async def publish_command(message: Message):

    if not can_publish(message):
        return

    item = prepared.get(
        message.from_user.id
    )

    if not item:

        await message.answer(
            "❌ خبری آماده انتشار نیست."
        )

        return

    ok = await publish_item(
        message,
        item
    )

    if ok:

        await message.answer(
            "✅ منتشر شد."
        )


# ============================================================
# RSS COMMAND
# ============================================================

@router.message(Command("rss"))
async def rss_command(message: Message):

    if not can_edit(message):
        return

    parts = (
        message.text or ""
    ).split(
        maxsplit=1
    )

    if len(parts) < 2:

        await message.answer(
            "مثال:\n"
            "/rss https://example.com/feed"
        )

        return

    url = parts[1].strip()

    await message.answer(
        "📡 در حال دریافت RSS..."
    )

    items = await fetch_rss(
        url
    )

    if not items:

        await message.answer(
            "❌ خبر جدیدی پیدا نشد."
        )

        return

    text = "📡 <b>RSS</b>\n\n"

    for item in items[:10]:

        text += (
            "📰 "
            + item["title"]
            + "\n"
            + item["link"]
            + "\n\n"
        )

    await message.answer(
        text[:4000],
        parse_mode=ParseMode.HTML
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

    load_all()

    bot = Bot(
        token=BOT_TOKEN
    )

    dispatcher = Dispatcher()

    dispatcher.include_router(
        router
    )

    asyncio.create_task(
        scheduler_loop(bot)
    )

    log.info(
        "Gamefa News Studio started successfully."
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
