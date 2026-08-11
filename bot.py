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
    ReplyKeyboardMarkup,
    KeyboardButton,
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

try:
    ADMIN_ID = int(
        os.getenv(
            "ADMIN_ID",
            "0"
        ) or "0"
    )
except (ValueError, TypeError):
    ADMIN_ID = 0


MEMORY_FILE = Path(
    "news_memory.json"
)

IMAGE_DIR = Path(
    "images"
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

    current_norm = norm(text)

    for item in memory:

        old_source = item.get(
            "source",
            ""
        )

        old_title = item.get(
            "title",
            ""
        )

        # مقایسه کل متن
        if similarity(
            current_norm,
            old_source
        ) >= 0.82:

            return True

        # مقایسه عنوان
        if old_title:

            if similarity(
                current_norm[:1500],
                old_title
            ) >= 0.75:

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
        "mortal shell",
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

    return "📢"


# ============================================================
# AI CLEANER
# ============================================================

def clean_ai_text(text):

    text = text or ""

    # Markdown bold
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

    # Markdown italic
    text = re.sub(
        r"\*(.*?)\*",
        r"\1",
        text,
        flags=re.S
    )

    # Code
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

    # حذف ID کانال
    text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        text
    )

    # حذف ایموجی اول خطوط
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

    # همه خطوط را تبدیل به متن واحد می‌کنیم
    # تا نقطه باعث رفتن به خط جدید نشود

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
            r"^[🟣•\-–—\d.)]+\s*",
            "",
            line
        ).strip()

        if not line:
            continue

        # اگر AI چند خط داده،
        # همه را به یک پاراگراف تبدیل می‌کنیم.

        line = make_persian_start(
            line,
            is_title=False
        )

        body_parts.append(
            line
        )

    # ========================================================
    # اگر AI متن را یک‌جا داده باشد
    # ========================================================

    body = " ".join(
        body_parts
    )

    # حذف فاصله‌های اضافه
    body = re.sub(
        r"\s+",
        " ",
        body
    ).strip()

    if not body:
        return ""

    # ========================================================
    # CATEGORY
    # ========================================================

    category = detect_category(
        title + " " + body
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
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8"
        ),
        "Accept-Language": (
            "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
        "Referer": "https://gamefa.com/",
    }

    timeout = aiohttp.ClientTimeout(
        total=45
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

    for element in soup([
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "form",
        "aside",
        "header"
    ]):

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
        {"property": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
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
    # IMAGE CANDIDATES
    # ========================================================

    image_candidates = []

    def add_image(value):

        if not value:
            return

        value = value.strip()

        if not value:
            return

        # srcset
        if "," in value:

            parts = value.split(",")

            for part in parts:

                part = part.strip()

                if not part:
                    continue

                image_url = part.split()[0]

                if image_url:

                    image_candidates.append(
                        urljoin(
                            final_url,
                            image_url
                        )
                    )

        else:

            image_candidates.append(
                urljoin(
                    final_url,
                    value
                )
            )

    # ========================================================
    # META IMAGES
    # ========================================================

    meta_images = [
        {"property": "og:image"},
        {"property": "og:image:url"},
        {"property": "og:image:secure_url"},
        {"name": "twitter:image"},
        {"name": "twitter:image:src"},
    ]

    for attrs in meta_images:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta:

            add_image(
                meta.get("content")
            )

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

    # ========================================================
    # ARTICLE IMAGES
    # ========================================================

    for img in article.find_all("img"):

        for attribute in [
            "src",
            "data-src",
            "data-lazy-src",
            "data-original",
            "data-image",
            "data-url",
            "data-full",
            "data-large-image",
            "srcset",
            "data-srcset"
        ]:

            value = img.get(
                attribute
            )

            if value:

                add_image(
                    value
                )

    # ========================================================
    # FILTER IMAGES
    # ========================================================

    filtered_images = []

    bad_words = [
        "logo",
        "avatar",
        "icon",
        "favicon",
        "emoji",
        "placeholder",
        "loading",
        "banner-ad",
        "advert"
    ]

    for image_url in image_candidates:

        image_url = image_url.strip()

        if not image_url.startswith(
            (
                "http://",
                "https://"
            )
        ):

            continue

        lower_url = image_url.lower()

        if any(
            word in lower_url
            for word in bad_words
        ):

            continue

        if image_url not in filtered_images:

            filtered_images.append(
                image_url
            )

    # ========================================================
    # ARTICLE TEXT
    # ========================================================

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

    body = body[:60000]

    # ========================================================
    # LOG
    # ========================================================

    log.info(
        "Found %s image candidates",
        len(filtered_images)
    )

    for index, image_url in enumerate(
        filtered_images[:10],
        1
    ):

        log.info(
            "Image candidate %s: %s",
            index,
            image_url
        )

    return {
        "url": final_url,
        "title": title,
        "description": description,
        "body": body,
        "image": (
            filtered_images[0]
            if filtered_images
            else ""
        ),
        "images": filtered_images
    }


# ============================================================
# AI PROMPT
# ============================================================

PROMPT = """
تو یک سردبیر حرفه‌ای اخبار فارسی برای رسانه Gamefa هستی.

وظیفه تو این است که مقاله ورودی را از ابتدا تا انتها کامل بخوانی و یک خبر فارسی حرفه‌ای و دقیق برای انتشار در تلگرام تولید کنی.

مهم‌ترین قانون:

هرگز فقط پاراگراف اول مقاله را خلاصه نکن.

باید کل مقاله را بخوانی و اطلاعات مهم را از بخش‌های مختلف آن استخراج کنی.

ممکن است مهم‌ترین اطلاعات مقاله در پاراگراف‌های میانی یا پایانی قرار داشته باشند.

========================

ساختار خروجی:

خط اول:
فقط تیتر.

بعد از تیتر:
یک پاراگراف خبری کامل.

یعنی خروجی باید شامل:

1 تیتر
1 پاراگراف خبر

باشد.

بدنه خبر نباید به چند پاراگراف تقسیم شود.

اگر در متن مقاله چند جمله وجود دارد، همه آن‌ها باید در یک پاراگراف قرار بگیرند.

========================

قانون بسیار مهم درباره نقطه:

بعد از نقطه نباید خط جدید ایجاد کنی.

مثلاً این اشتباه است:

Mortal Shell 2 بیش از ۳۲ گیگابایت حجم دارد.
این اطلاعات توسط PlayStationGameSize منتشر شده است.
نسخه PS5 در تاریخ مشخصی عرضه می‌شود.

باید این‌گونه باشد:

Mortal Shell 2 بیش از ۳۲ گیگابایت حجم دارد. این اطلاعات توسط PlayStationGameSize منتشر شده است. نسخه PS5 در تاریخ مشخصی عرضه می‌شود.

کل متن خبر باید یک پاراگراف باشد.

========================

اطلاعات مهم:

هنگام خلاصه‌سازی حتماً به این موارد توجه ویژه داشته باش:

- تاریخ عرضه
- تاریخ انتشار
- زمان عرضه
- زمان پیش‌دانلود
- حجم دانلود
- حجم نصب
- پلتفرم‌ها
- نسخه‌های مختلف
- قیمت
- نام سازنده
- نام ناشر
- شخصیت‌ها
- ویژگی‌های مهم
- جزئیات مربوط به بازی
- جزئیات مربوط به فیلم یا سریال
- وضعیت پروژه
- اطلاعات رسمی
- اطلاعات فاش‌شده
- منبع گزارش
- شایعات
- اطلاعاتی که در پایان مقاله آمده‌اند

اگر مقاله تاریخ عرضه را دارد، تاریخ عرضه باید حتماً در خروجی ذکر شود.

اگر مقاله زمان پیش‌دانلود را دارد، آن را نیز ذکر کن.

اگر مقاله هم تاریخ عرضه و هم زمان پیش‌دانلود را دارد، هر دو را ذکر کن.

اگر مقاله حجم بازی را دارد، حجم را ذکر کن.

هیچ‌کدام از این اطلاعات را فقط به دلیل اینکه در انتهای مقاله هستند حذف نکن.

========================

دقت:

هیچ اطلاعاتی را حدس نزن.

هیچ عددی را تغییر نده.

هیچ تاریخ جدیدی نساز.

هیچ پلتفرمی را اضافه نکن.

اگر اطلاعاتی در مقاله به‌عنوان شایعه یا گزارش مطرح شده، آن را رسمی معرفی نکن.

اگر منبعی اطلاعات را فاش کرده، منبع را در متن خبر ذکر کن.

اگر سازندگان رسماً چیزی را اعلام کرده‌اند، آن را به‌عنوان اعلام رسمی بیان کن.

تفاوت میان:

- شایعه
- گزارش
- افشا
- اعلام رسمی

را حفظ کن.

========================

تحلیل مقاله:

ابتدا کل مقاله را بخوان.

سپس:

1. نکات اصلی را استخراج کن.
2. اطلاعات تکراری را حذف کن.
3. اطلاعات مهم انتهای مقاله را نیز بررسی کن.
4. تاریخ‌ها و اعداد مهم را پیدا کن.
5. اطلاعات مربوط به عرضه و پلتفرم‌ها را بررسی کن.
6. اطلاعات مربوط به منبع خبر را بررسی کن.
7. سپس خبر را از ابتدا بازنویسی کن.

========================

تیتر:

تیتر باید:

- کوتاه باشد
- خبری باشد
- جذاب باشد
- مهم‌ترین اتفاق را منتقل کند
- با متن فارسی شروع شود

مثال بد:

Mortal Shell 2 PS5 Download Size Revealed

مثال خوب:

حجم نسخه PS5 بازی Mortal Shell 2 فاش شد

اگر تاریخ عرضه موضوع اصلی خبر است، تیتر می‌تواند روی تاریخ عرضه تمرکز کند.

========================

شروع فارسی:

هر تیتر و متن خبری باید با فارسی شروع شود.

مثال بد:

PlayStationGameSize اعلام کرد که...

مثال خوب:

براساس اطلاعات منتشرشده، PlayStationGameSize اعلام کرده است که...

========================

زبان:

فارسی روان و طبیعی بنویس.

نام‌های انگلیسی مهم را حفظ کن.

از ترجمه عجیب نام بازی‌ها خودداری کن.

از تکرار عبارت‌هایی مثل «براساس گزارش‌های منتشرشده» در چند جمله پشت سر هم خودداری کن.

========================

ممنوع:

- Markdown
- Bold
- Italic
- Bullet
- شماره‌گذاری
- Emoji
- لینک
- آیدی کانال
- HTML
- چند پاراگراف
- چند خط برای بدنه
- کپی مستقیم متن مقاله
- خلاصه کردن فقط پاراگراف اول

========================

خروجی نهایی فقط این باشد:

خط اول = تیتر

خط دوم = یک پاراگراف کامل خبر

هیچ خط دیگری تولید نکن.

هیچ توضیحی درباره کاری که انجام دادی ننویس.

هیچ عبارت اضافی در ابتدا یا انتهای خروجی قرار نده.
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
        "مقاله زیر را از ابتدا تا انتها کامل بخوان.\n\n"
        "به‌خصوص بخش‌های پایانی مقاله را نادیده نگیر.\n"
        "تاریخ عرضه، زمان پیش‌دانلود، حجم، پلتفرم‌ها و سایر "
        "اطلاعات عددی مهم را استخراج کن.\n\n"
        "================ ARTICLE ================\n"
        + source
        + "\n\n"
        "================ END ARTICLE ================\n"
    )

    response = await client.responses.create(
        model=MODEL,
        instructions=PROMPT,
        input=input_text,
        max_output_tokens=2200
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

        IMAGE_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "image/avif,image/webp,image/apng,"
                "image/svg+xml,image/*,*/*;q=0.8"
            ),
            "Referer": "https://gamefa.com/",
        }

        timeout = aiohttp.ClientTimeout(
            total=40
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

                    log.warning(
                        "Image HTTP error %s: %s",
                        response.status,
                        url
                    )

                    return None

                data = await response.read()

                content_type = (
                    response.headers.get(
                        "Content-Type",
                        ""
                    ).lower()
                )

        # ====================================================
        # SIZE
        # ====================================================

        if len(data) < 1000:

            log.warning(
                "Image too small: %s bytes",
                len(data)
            )

            return None

        if len(data) > 20 * 1024 * 1024:

            log.warning(
                "Image too large: %s bytes",
                len(data)
            )

            return None

        # ====================================================
        # FORMAT
        # ====================================================

        if (
            data.startswith(b"\xff\xd8\xff")
            or "jpeg" in content_type
            or "jpg" in content_type
        ):

            extension = ".jpg"

        elif (
            data.startswith(b"\x89PNG")
            or "png" in content_type
        ):

            extension = ".png"

        elif (
            (
                data.startswith(b"RIFF")
                and data[8:12] == b"WEBP"
            )
            or "webp" in content_type
        ):

            extension = ".webp"

        elif (
            data.startswith(b"GIF8")
            or "gif" in content_type
        ):

            extension = ".gif"

        else:

            lower_url = url.lower()

            if (
                ".jpg" in lower_url
                or ".jpeg" in lower_url
            ):

                extension = ".jpg"

            elif ".png" in lower_url:

                extension = ".png"

            elif ".webp" in lower_url:

                extension = ".webp"

            else:

                log.warning(
                    "Unknown image format: %s",
                    content_type
                )

                return None

        # ====================================================
        # FILE
        # ====================================================

        filename = (
            f"gamefa_{abs(hash(url))}{extension}"
        )

        path = (
            IMAGE_DIR
            / filename
        )

        path.write_bytes(
            data
        )

        log.info(
            "Image downloaded successfully: %s (%s bytes)",
            path,
            len(data)
        )

        return path

    except Exception as error:

        log.warning(
            "Image download error: %s",
            error
        )

        return None


# ============================================================
# FIND BEST IMAGE
# ============================================================

async def find_best_image(
    source_image,
    source_images=None
):

    candidates = []

    if source_image:

        candidates.append(
            source_image
        )

    if source_images:

        candidates.extend(
            source_images
        )

    unique_candidates = []

    for url in candidates:

        if (
            url
            and url not in unique_candidates
        ):

            unique_candidates.append(
                url
            )

    for image_url in unique_candidates:

        log.info(
            "Trying image: %s",
            image_url
        )

        image = await download_image(
            image_url
        )

        if image:

            log.info(
                "Selected image: %s",
                image
            )

            return image

    log.warning(
        "No usable image found."
    )

    return None


# ============================================================
# REPLY KEYBOARDS
# ============================================================

def main_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🔎 بررسی خبر جدید"
                ),
                KeyboardButton(
                    text="📁 آرشیو"
                )
            ],
            [
                KeyboardButton(
                    text="📊 آمار"
                ),
                KeyboardButton(
                    text="🤖 وضعیت AI"
                )
            ],
            [
                KeyboardButton(
                    text="⚙️ تنظیمات"
                ),
                KeyboardButton(
                    text="📋 راهنما"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="یک گزینه را انتخاب کنید..."
    )


def news_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📝 ارسال متن خبر"
                ),
                KeyboardButton(
                    text="🔗 ارسال لینک Gamefa"
                )
            ],
            [
                KeyboardButton(
                    text="📤 انتشار خبر آماده"
                )
            ],
            [
                KeyboardButton(
                    text="🔙 بازگشت"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def archive_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📚 آخرین اخبار"
                ),
                KeyboardButton(
                    text="🗑 پاکسازی آرشیو"
                )
            ],
            [
                KeyboardButton(
                    text="🔙 بازگشت"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def settings_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📢 کانال انتشار"
                ),
                KeyboardButton(
                    text="🧠 مدل AI"
                )
            ],
            [
                KeyboardButton(
                    text="🖼 سیستم تصویر"
                ),
                KeyboardButton(
                    text="✍️ قالب خبر"
                )
            ],
            [
                KeyboardButton(
                    text="🔙 بازگشت"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def back_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🔙 بازگشت"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


# ============================================================
# HOME TEXT
# ============================================================

async def send_home(message):

    await message.answer(
        "✨ <b>پنل مدیریت Gamefa</b>\n\n"
        "به پنل مدیریت اخبار خوش آمدی.\n"
        "از منوی زیر گزینه موردنظر را انتخاب کن.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
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

    status = None
    image_path = None

    try:

        url = extract_url(
            text
        )

        source_image = ""
        source_images = []

        article_title = ""
        article_body = ""
        description = ""

        source = text

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

            source_images = article.get(
                "images",
                []
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
                        "در حال استخراج اطلاعات مهم از کل مقاله..."
                    )

                except Exception:
                    pass

        # ====================================================
        # DUPLICATE
        # ====================================================

        if duplicate(source):

            await message.answer(
                "⚠️ این خبر یا یک خبر بسیار مشابه قبلاً در آرشیو وجود دارد.",
                reply_markup=main_keyboard()
            )

            return

        # ====================================================
        # AI
        # ====================================================

        if status:

            try:

                await status.edit_text(
                    "🧠 در حال تحلیل کل مقاله...\n"
                    "تاریخ عرضه، حجم، پلتفرم و سایر جزئیات مهم نیز بررسی می‌شوند."
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

        if url:

            image_path = await find_best_image(
                source_image,
                source_images
            )

        # ====================================================
        # MEMORY
        # ====================================================

        memory.append(
            {
                "source": source[:30000],
                "post": post,
                "title": article_title,
                "url": url or ""
            }
        )

        memory[:] = memory[
            -MAX_MEMORY:
        ]

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
        # REMOVE STATUS
        # ====================================================

        if status:

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

            await message.answer(
                "ℹ️ تصویر قابل دریافت از مقاله پیدا نشد.",
                reply_markup=back_keyboard()
            )

        await message.answer(
            "✅ خبر آماده است.\n\n"
            "برای انتشار، روی «📤 انتشار خبر آماده» بزن.",
            reply_markup=news_keyboard()
        )

    except Exception as error:

        log.exception(
            "News processing error"
        )

        if status:

            try:
                await status.delete()
            except Exception:
                pass

        await message.answer(
            "❌ خطا هنگام پردازش خبر:\n\n"
            + str(error)[:1500],
            reply_markup=main_keyboard()
        )

    finally:

        processing_users.discard(
            user_id
        )


# ============================================================
# PUBLISH NEWS
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
            reply_markup=main_keyboard()
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
        # PHOTO
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

        else:

            await message.bot.send_message(
                CHANNEL_ID,
                text,
                parse_mode=ParseMode.HTML
            )

        await message.answer(
            "✅ <b>خبر با موفقیت در کانال منتشر شد.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard()
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
            reply_markup=main_keyboard()
        )


# ============================================================
# START
# ============================================================

router = Router()


@router.message(
    Command("start")
)
async def start_handler(
    message: Message
):

    if not is_admin(message):

        await message.answer(
            "⛔ این ربات خصوصی است."
        )

        return

    await send_home(
        message
    )


# ============================================================
# COMMAND PUBLISH
# ============================================================

@router.message(
    Command("publish")
)
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
# COMMAND STATS
# ============================================================

@router.message(
    Command("stats")
)
async def stats_command(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "📊 <b>آمار ربات</b>\n\n"
        f"📰 تعداد اخبار آرشیو: <b>{len(memory)}</b>\n"
        f"💾 ظرفیت حافظه: <b>{MAX_MEMORY}</b>\n"
        f"👤 مدیر اصلی: <code>{ADMIN_ID}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )


# ============================================================
# COMMAND CLEAR
# ============================================================

@router.message(
    Command("clear")
)
async def clear_command(
    message: Message
):

    if not is_admin(message):
        return

    memory.clear()

    save_memory()

    prepared.clear()

    await message.answer(
        "✅ آرشیو با موفقیت پاک شد.",
        reply_markup=main_keyboard()
    )


# ============================================================
# REPLY KEYBOARD HANDLER
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

    # ========================================================
    # COMMANDS
    # ========================================================

    if text.startswith("/"):
        return

    # ========================================================
    # HOME
    # ========================================================

    if text == "🔙 بازگشت":

        await send_home(
            message
        )

        return

    # ========================================================
    # NEWS
    # ========================================================

    if text == "🔎 بررسی خبر جدید":

        await message.answer(
            "🔎 <b>بررسی خبر جدید</b>\n\n"
            "نوع ورودی را انتخاب کن:",
            parse_mode=ParseMode.HTML,
            reply_markup=news_keyboard()
        )

        return

    # ========================================================
    # SEND TEXT
    # ========================================================

    if text == "📝 ارسال متن خبر":

        await message.answer(
            "📝 <b>ارسال متن خبر</b>\n\n"
            "متن خبر را ارسال کن.\n\n"
            "هوش مصنوعی متن را تحلیل و بازنویسی می‌کند.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )

        return

    # ========================================================
    # SEND GAMEFA URL
    # ========================================================

    if text == "🔗 ارسال لینک Gamefa":

        await message.answer(
            "🔗 <b>ارسال لینک Gamefa</b>\n\n"
            "لینک کامل مقاله Gamefa را ارسال کن.\n\n"
            "ربات کل مقاله را دریافت می‌کند و "
            "اطلاعات مهم مانند تاریخ عرضه، حجم، "
            "پلتفرم و زمان پیش‌دانلود را بررسی می‌کند.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )

        return

    # ========================================================
    # PUBLISH
    # ========================================================

    if text == "📤 انتشار خبر آماده":

        await publish_news(
            message,
            message.from_user.id
        )

        return

    # ========================================================
    # ARCHIVE
    # ========================================================

    if text == "📁 آرشیو":

        await message.answer(
            "📁 <b>آرشیو اخبار</b>\n\n"
            "یک گزینه را انتخاب کن:",
            parse_mode=ParseMode.HTML,
            reply_markup=archive_keyboard()
        )

        return

    # ========================================================
    # LATEST ARCHIVE
    # ========================================================

    if text == "📚 آخرین اخبار":

        if not memory:

            output = (
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
                    f"{index}. {first_line[:120]}"
                )

            output = "\n".join(
                lines
            )

        await message.answer(
            output,
            parse_mode=ParseMode.HTML,
            reply_markup=archive_keyboard()
        )

        return

    # ========================================================
    # CLEAR ARCHIVE
    # ========================================================

    if text == "🗑 پاکسازی آرشیو":

        memory.clear()

        save_memory()

        prepared.clear()

        await message.answer(
            "✅ آرشیو با موفقیت پاک شد.",
            reply_markup=archive_keyboard()
        )

        return

    # ========================================================
    # STATS
    # ========================================================

    if text == "📊 آمار":

        await message.answer(
            "📊 <b>آمار ربات</b>\n\n"
            f"📰 تعداد اخبار آرشیو: <b>{len(memory)}</b>\n"
            f"💾 ظرفیت حافظه: <b>{MAX_MEMORY}</b>\n"
            f"👤 مدیر اصلی: <code>{ADMIN_ID}</code>\n"
            f"🖼 تصاویر ذخیره‌شده در: <code>images/</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard()
        )

        return

    # ========================================================
    # AI STATUS
    # ========================================================

    if text == "🤖 وضعیت AI":

        status = (
            "🟢 فعال"
            if OPENAI_API_KEY
            else "🔴 غیرفعال"
        )

        await message.answer(
            "🤖 <b>وضعیت هوش مصنوعی</b>\n\n"
            f"وضعیت: {status}\n"
            f"مدل: <code>{escape_html(MODEL)}</code>\n\n"
            "حالت پردازش:\n"
            "✅ تحلیل کل مقاله\n"
            "✅ بررسی بخش‌های پایانی\n"
            "✅ استخراج تاریخ عرضه\n"
            "✅ استخراج زمان پیش‌دانلود\n"
            "✅ استخراج حجم و پلتفرم\n"
            "✅ بازنویسی خبری\n"
            "✅ خروجی تک‌پاراگرافی",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard()
        )

        return

    # ========================================================
    # SETTINGS
    # ========================================================

    if text == "⚙️ تنظیمات":

        await message.answer(
            "⚙️ <b>تنظیمات</b>\n\n"
            "یک بخش را انتخاب کن:",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard()
        )

        return

    # ========================================================
    # CHANNEL
    # ========================================================

    if text == "📢 کانال انتشار":

        await message.answer(
            "📢 <b>کانال انتشار</b>\n\n"
            f"<code>{escape_html(CHANNEL_ID)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard()
        )

        return

    # ========================================================
    # MODEL
    # ========================================================

    if text == "🧠 مدل AI":

        await message.answer(
            "🧠 <b>مدل هوش مصنوعی</b>\n\n"
            f"<code>{escape_html(MODEL)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard()
        )

        return

    # ========================================================
    # IMAGE
    # ========================================================

    if text == "🖼 سیستم تصویر":

        await message.answer(
            "🖼 <b>سیستم تصویر</b>\n\n"
            "ربات ابتدا تصویر اصلی مقاله را بررسی می‌کند.\n\n"
            "سپس در صورت نیاز موارد زیر را بررسی می‌کند:\n"
            "• og:image\n"
            "• twitter:image\n"
            "• src\n"
            "• data-src\n"
            "• data-lazy-src\n"
            "• srcset\n"
            "• data-srcset\n\n"
            "اگر یک تصویر قابل دانلود نباشد، "
            "تصاویر بعدی را امتحان می‌کند.",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard()
        )

        return

    # ========================================================
    # FORMAT
    # ========================================================

    if text == "✍️ قالب خبر":

        await message.answer(
            "✍️ <b>قالب خبر</b>\n\n"
            "• تیتر فارسی\n"
            "• یک پاراگراف کامل\n"
            "• عدم شکستن متن بعد از نقطه\n"
            "• تحلیل کل مقاله\n"
            "• بررسی تاریخ عرضه\n"
            "• بررسی زمان پیش‌دانلود\n"
            "• بررسی حجم و پلتفرم\n"
            "• شروع فارسی\n"
            "• دسته‌بندی خودکار\n"
            "• امضای Gamefa",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard()
        )

        return

    # ========================================================
    # HELP
    # ========================================================

    if text == "📋 راهنما":

        await message.answer(
            "📋 <b>راهنمای ربات</b>\n\n"
            "🔎 بررسی خبر جدید\n"
            "برای پردازش متن یا لینک Gamefa.\n\n"
            "🧠 هوش مصنوعی\n"
            "کل مقاله را تحلیل می‌کند، نه فقط پاراگراف اول.\n\n"
            "📅 اطلاعات مهم\n"
            "تاریخ عرضه، زمان پیش‌دانلود، حجم، "
            "پلتفرم و سایر جزئیات مهم بررسی می‌شوند.\n\n"
            "🖼 تصویر\n"
            "تصویر اصلی مقاله استخراج و دانلود می‌شود.\n\n"
            "📤 انتشار\n"
            "بعد از آماده شدن خبر، آن را می‌توان در کانال منتشر کرد.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard()
        )

        return

    # ========================================================
    # UNKNOWN TEXT = NEWS INPUT
    # ========================================================

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

    IMAGE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

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
