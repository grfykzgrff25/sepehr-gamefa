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

# حالت فعلی منوی هر کاربر
user_modes = {}


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
        r"^[🎮🎬📱🟣📢📰\s]+",
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

    # Channel ID
    text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        text
    )

    # Emojis at beginning of lines
    text = re.sub(
        r"^\s*[🎮🎬📱📢🟣📰]\s*",
        "",
        text,
        flags=re.M
    )

    return text.strip()


# ============================================================
# SENTENCE NORMALIZER
# ============================================================

def normalize_news_body(text):
    """
    تمام خطوط خبر را به یک پاراگراف تبدیل می‌کند.

    اگر AI هر جمله را در یک خط جدا بفرستد،
    این تابع آن‌ها را با فاصله به هم متصل می‌کند.
    """

    if not text:
        return ""

    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    lines = []

    for line in text.split("\n"):

        line = line.strip()

        if not line:
            continue

        # حذف شماره‌گذاری
        line = re.sub(
            r"^\s*\d+[\.\)\-:]\s*",
            "",
            line
        )

        # حذف بولت
        line = re.sub(
            r"^\s*[•●▪️\-–—]\s*",
            "",
            line
        )

        line = line.strip()

        if line:
            lines.append(line)

    # اتصال همه جمله‌ها به یک پاراگراف
    result = " ".join(lines)

    # حذف فاصله‌های اضافی
    result = re.sub(
        r"\s+",
        " ",
        result
    )

    return result.strip()


# ============================================================
# FORMAT POST
# ============================================================

def format_post(ai_text):

    ai_text = clean_ai_text(
        ai_text
    )

    if not ai_text:
        return ""

    # --------------------------------------------------------
    # تبدیل خروجی AI به خطوط
    # --------------------------------------------------------

    raw_lines = [
        line.strip()
        for line in ai_text.splitlines()
        if line.strip()
    ]

    if not raw_lines:
        return ""

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title = raw_lines[0]

    title = re.sub(
        r"^[🎮🎬📱📢📰]\s*",
        "",
        title
    ).strip()

    title = make_persian_start(
        title,
        is_title=True
    )

    # --------------------------------------------------------
    # BODY
    # --------------------------------------------------------

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

    # حداکثر 7 جمله/خط AI
    body_parts = body_parts[:7]

    # اگر AI کمتر داد
    fallback_sentences = [
        "جزئیات بیشتر این موضوع در گزارش اصلی ارائه شده است.",
        "اطلاعات تکمیلی این خبر در منبع اصلی قابل بررسی است.",
        "جزئیات بیشتری درباره این موضوع منتشر نشده است."
    ]

    fallback_index = 0

    while len(body_parts) < 7:

        body_parts.append(
            fallback_sentences[
                min(
                    fallback_index,
                    len(fallback_sentences) - 1
                )
            ]
        )

        fallback_index += 1

    # --------------------------------------------------------
    # تبدیل 7 جمله به یک پاراگراف
    # --------------------------------------------------------

    body = normalize_news_body(
        "\n".join(body_parts)
    )

    # --------------------------------------------------------
    # CATEGORY
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

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

    # محدودیت برای ارسال به AI
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

وظیفه تو این است که کل مقاله را بخوانی، اطلاعات مهم آن را استخراج کنی و سپس یک خبر فارسی دقیق، طبیعی و حرفه‌ای تولید کنی.

============================================================
قانون بسیار مهم
============================================================

هرگز فقط پاراگراف اول مقاله را خلاصه یا بازنویسی نکن.

قبل از تولید خروجی، باید کل مقاله را از ابتدا تا انتها بررسی کنی.

اطلاعات مهم را از بخش‌های مختلف مقاله استخراج کن.

خبر نهایی باید حاصل تحلیل کل مقاله باشد، نه فقط پاراگراف اول.

============================================================
اطلاعاتی که نباید حذف شوند
============================================================

اگر مقاله شامل هرکدام از اطلاعات زیر است، باید مقدار دقیق آن‌ها را در خبر نهایی بیاوری:

- تاریخ عرضه
- تاریخ انتشار
- تاریخ پیش‌دانلود
- ساعت انتشار
- ساعت پیش‌دانلود
- حجم دانلود
- حجم نصب
- قیمت
- نسخه‌های مختلف
- نام نسخه‌ها
- پلتفرم‌ها
- مشخصات فنی
- سیستم مورد نیاز
- نام شرکت‌ها
- نام استودیوها
- نام توسعه‌دهندگان
- نام ناشر
- آمار
- اعداد
- زمان دسترسی زودهنگام
- اطلاعات رسمی
- اطلاعات فاش‌شده
- شایعات
- منبع خبر

اگر چنین اطلاعاتی در مقاله وجود دارد، آن‌ها را با عبارت کلی جایگزین نکن.

مثلاً غلط:

«تاریخ عرضه بازی مشخص شده است.»

درست:

«نسخه استاندارد بازی در تاریخ ۲۰ اوت ۲۰۲۶ عرضه خواهد شد.»

مثلاً غلط:

«نسخه ویژه زودتر منتشر می‌شود.»

درست:

«نسخه Devout Edition در تاریخ ۱۷ اوت عرضه خواهد شد.»

مثلاً غلط:

«پیش‌دانلود بازی چند روز زودتر آغاز می‌شود.»

درست:

«پیش‌دانلود نسخه استاندارد از ۱۸ اوت آغاز می‌شود.»

============================================================
قانون تاریخ و عدد
============================================================

اگر مقاله دارای تاریخ یا عدد دقیق است، حتماً آن را در خروجی قرار بده.

هرگز اطلاعات دقیق را به اطلاعات کلی تبدیل نکن.

مثلاً:

«در آینده عرضه می‌شود»

به جای:

«۲۰ اوت ۲۰۲۶ عرضه می‌شود»

ممنوع است.

همچنین:

«حجم بازی بیش از ۳۲ گیگابایت است»

اگر مقاله عدد دقیق دارد، نباید جایگزین عدد دقیق شود.

============================================================
اولویت اطلاعات
============================================================

اطلاعات را با این اولویت انتخاب کن:

1. مهم‌ترین اتفاق خبر
2. تاریخ و زمان دقیق
3. اعداد و آمار مهم
4. نسخه‌ها و تفاوت آن‌ها
5. پلتفرم‌ها
6. اطلاعات منبع خبر
7. جزئیات مهم مقاله
8. وضعیت فعلی و اتفاق آینده

============================================================
خروجی
============================================================

خروجی دقیقاً شامل:

خط اول: تیتر

خطوط دوم تا هشتم:
7 جمله خبری

یعنی:

1 تیتر
7 جمله خبری

هیچ شماره‌گذاری نکن.

هیچ بولتی استفاده نکن.

هیچ Markdown استفاده نکن.

هیچ HTML استفاده نکن.

هیچ ایموجی استفاده نکن.

هیچ لینک تولید نکن.

آیدی کانال تولید نکن.

============================================================
قانون بسیار مهم برای متن
============================================================

هر 7 جمله باید اطلاعات متفاوت و مفید داشته باشند.

جملات نباید تکراری باشند.

از تکرار عبارت‌هایی مثل:

«براساس گزارش‌های منتشرشده»
«طبق گزارش‌ها»
«این اطلاعات»
«در این گزارش»

به شکل مداوم خودداری کن.

متن باید شبیه خبر واقعی یک رسانه فارسی باشد.

============================================================
قانون پوشش کل مقاله
============================================================

حتماً بخش‌های میانی و پایانی مقاله را هم بررسی کن.

اگر اطلاعات مهم در انتهای مقاله آمده باشد، آن اطلاعات را حذف نکن.

پاراگراف اول مقاله نباید سهم بیشتری از سایر بخش‌ها داشته باشد.

اگر مقاله درباره یک بازی است و در بخش‌های مختلف مقاله تاریخ عرضه، حجم، نسخه‌ها، پیش‌دانلود و پلتفرم‌ها ذکر شده‌اند، همه اطلاعات مهم را در 7 جمله ترکیب کن.

============================================================
ساختار پیشنهادی
============================================================

جمله اول:
مهم‌ترین اتفاق خبر.

جمله دوم:
مهم‌ترین تاریخ، عدد، حجم، قیمت یا زمان دقیق.

جمله سوم:
جزئیات نسخه‌ها یا پلتفرم‌ها.

جمله چهارم:
یک اطلاعات مهم از بخش میانی مقاله.

جمله پنجم:
تاریخ عرضه، پیش‌دانلود یا دسترسی زودهنگام، در صورت وجود.

جمله ششم:
یک جزئیات مهم دیگر از مقاله.

جمله هفتم:
جمع‌بندی وضعیت فعلی و اتفاق آینده.

این ساختار انعطاف‌پذیر است، اما اطلاعات مهم مقاله نباید حذف شوند.

============================================================
قانون شایعه و گزارش
============================================================

اگر اطلاعات توسط یک منبع یا دیتابیس فاش شده، آن را به عنوان اطلاعات فاش‌شده معرفی کن.

اگر سازنده یا ناشر آن را رسماً اعلام کرده، آن را رسمی معرفی کن.

اگر اطلاعات شایعه است، آن را رسمی جلوه نده.

============================================================
قانون تیتر
============================================================

تیتر باید:

- کوتاه باشد
- خبری باشد
- جذاب باشد
- مهم‌ترین اتفاق را منتقل کند
- با متن فارسی شروع شود

مثال:

حجم و زمان پیش‌دانلود Mortal Shell 2 برای PS5 فاش شد

============================================================
قانون زبان
============================================================

فارسی روان و طبیعی بنویس.

نام بازی‌ها، شرکت‌ها، افراد و اصطلاحات مهم انگلیسی را حفظ کن.

اگر جمله با نام انگلیسی شروع می‌شود، یک عبارت فارسی مناسب قبل از آن قرار بده.

غلط:

Brad Pitt در فیلم جدید...

درست:

برد پیت در فیلم جدید...

یا:

براساس گزارش جدید، Brad Pitt در فیلم جدید...

============================================================
قانون نهایی
============================================================

قبل از تولید خروجی:

1. کل مقاله را بخوان.
2. تمام تاریخ‌ها را استخراج کن.
3. تمام ساعت‌ها را استخراج کن.
4. تمام اعداد مهم را استخراج کن.
5. تمام نسخه‌ها را استخراج کن.
6. تمام پلتفرم‌ها را استخراج کن.
7. اطلاعات بخش میانی را بررسی کن.
8. اطلاعات بخش پایانی را بررسی کن.
9. اطلاعات مهم را اولویت‌بندی کن.
10. سپس تیتر و 7 جمله خبری بنویس.

اگر چند تاریخ یا عدد مهم وجود دارد، آن‌ها را در یک جمله طبیعی با هم ترکیب کن.

هیچ اطلاعاتی را حدس نزن.

اکنون کل مقاله را تحلیل کن و خروجی را تولید کن.
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
        "مهم: قبل از نوشتن خبر، تاریخ‌ها، ساعت‌ها، حجم‌ها، "
        "قیمت‌ها، نسخه‌ها، پلتفرم‌ها و سایر اعداد مهم مقاله "
        "را شناسایی کن.\n\n"
        "اگر مقاله تاریخ عرضه یا پیش‌دانلود دارد، تاریخ دقیق "
        "را حتماً در خروجی بیاور و آن را با عبارت کلی مانند "
        "«به‌زودی» جایگزین نکن.\n\n"
        "اگر مقاله چند نسخه یا چند تاریخ دارد، مهم‌ترین آن‌ها "
        "را در یک یا چند جمله از 7 جمله خبری قرار بده.\n\n"
        "هرگز فقط پاراگراف اول مقاله را خلاصه نکن.\n"
        "اطلاعات را از کل مقاله استخراج کن.\n"
        "خروجی باید یک تیتر و دقیقاً 7 جمله خبری باشد.\n\n"
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
# REPLY KEYBOARDS
# ============================================================

def main_menu():

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
        is_persistent=True
    )


def news_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📝 ارسال خبر"
                )
            ],
            [
                KeyboardButton(
                    text="🔗 ارسال لینک Gamefa"
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


def archive_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📚 آخرین اخبار"
                )
            ],
            [
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


def settings_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📢 کانال انتشار"
                )
            ],
            [
                KeyboardButton(
                    text="🧠 مدل AI"
                )
            ],
            [
                KeyboardButton(
                    text="🖼 سیستم تصویر"
                )
            ],
            [
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


def clear_confirm_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="⚠️ بله، پاک کن"
                )
            ],
            [
                KeyboardButton(
                    text="❌ لغو"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def back_menu():

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


def publish_menu():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📤 انتشار خبر"
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
                        "در حال استخراج تاریخ‌ها، اعداد و نکات مهم..."
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
                    "تاریخ‌ها، اعداد و اطلاعات مهم نیز بررسی می‌شوند."
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
            "✅ خبر آماده است.\n\n"
            "در صورت تأیید، روی «📤 انتشار خبر» بزن.",
            reply_markup=publish_menu()
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

    user_modes[
        message.from_user.id
    ] = "main"

    await message.answer(
        "✨ <b>پنل مدیریت Gamefa</b>\n\n"
        "به پنل مدیریت اخبار خوش آمدید.\n"
        "از منوی زیر عملیات موردنظر را انتخاب کن.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )


# ============================================================
# MAIN MENU BUTTONS
# ============================================================

@router.message(
    F.text == "🔎 بررسی خبر جدید"
)
async def news_new_handler(
    message: Message
):

    if not is_admin(message):
        return

    user_modes[
        message.from_user.id
    ] = "news"

    await message.answer(
        "🔎 <b>بررسی خبر جدید</b>\n\n"
        "نوع ورودی را انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=news_menu()
    )


@router.message(
    F.text == "📁 آرشیو"
)
async def archive_handler(
    message: Message
):

    if not is_admin(message):
        return

    user_modes[
        message.from_user.id
    ] = "archive"

    await message.answer(
        "📁 <b>آرشیو اخبار</b>\n\n"
        "گزینه موردنظر را انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=archive_menu()
    )


@router.message(
    F.text == "📊 آمار"
)
async def stats_handler(
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
        reply_markup=main_menu()
    )


@router.message(
    F.text == "🤖 وضعیت AI"
)
async def ai_status_handler(
    message: Message
):

    if not is_admin(message):
        return

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
        "✅ استخراج تاریخ‌ها و اعداد\n"
        "✅ استخراج نکات مهم\n"
        "✅ خلاصه‌سازی\n"
        "✅ بازنویسی\n"
        "✅ خروجی ۷ جمله‌ای",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )


@router.message(
    F.text == "⚙️ تنظیمات"
)
async def settings_handler(
    message: Message
):

    if not is_admin(message):
        return

    user_modes[
        message.from_user.id
    ] = "settings"

    await message.answer(
        "⚙️ <b>تنظیمات</b>\n\n"
        "یک بخش را انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_menu()
    )


@router.message(
    F.text == "📋 راهنما"
)
async def help_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "📋 <b>راهنمای ربات</b>\n\n"
        "🔎 بررسی خبر جدید\n"
        "برای پردازش متن یا لینک Gamefa.\n\n"
        "🤖 پردازش AI\n"
        "کل مقاله تحلیل شده و اطلاعات مهم آن استخراج می‌شود.\n\n"
        "📰 خروجی\n"
        "یک تیتر + ۷ جمله خبری در یک پاراگراف.\n\n"
        "📁 آرشیو\n"
        "ذخیره و بررسی اخبار پردازش‌شده.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )


# ============================================================
# NEWS MENU
# ============================================================

@router.message(
    F.text == "📝 ارسال خبر"
)
async def news_text_handler(
    message: Message
):

    if not is_admin(message):
        return

    user_modes[
        message.from_user.id
    ] = "waiting_news"

    await message.answer(
        "📝 <b>ارسال خبر</b>\n\n"
        "متن خبر را ارسال کن.\n\n"
        "هوش مصنوعی کل متن را تحلیل می‌کند "
        "و یک تیتر + ۷ جمله خبری تولید می‌کند.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_menu()
    )


@router.message(
    F.text == "🔗 ارسال لینک Gamefa"
)
async def news_link_handler(
    message: Message
):

    if not is_admin(message):
        return

    user_modes[
        message.from_user.id
    ] = "waiting_news"

    await message.answer(
        "🔗 <b>ارسال لینک Gamefa</b>\n\n"
        "لینک مقاله Gamefa را ارسال کن.\n\n"
        "ربات کل مقاله را دریافت می‌کند و "
        "تاریخ‌ها، اعداد، نسخه‌ها و اطلاعات مهم "
        "را از کل مقاله استخراج می‌کند.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_menu()
    )


# ============================================================
# ARCHIVE MENU
# ============================================================

@router.message(
    F.text == "📚 آخرین اخبار"
)
async def archive_latest_handler(
    message: Message
):

    if not is_admin(message):
        return

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

    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=archive_menu()
    )


@router.message(
    F.text == "🗑 پاکسازی آرشیو"
)
async def clear_confirm_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "⚠️ <b>پاکسازی آرشیو</b>\n\n"
        "تمام اخبار ذخیره‌شده حذف خواهند شد.\n"
        "این عملیات قابل بازگشت نیست.",
        parse_mode=ParseMode.HTML,
        reply_markup=clear_confirm_menu()
    )


@router.message(
    F.text == "⚠️ بله، پاک کن"
)
async def clear_yes_handler(
    message: Message
):

    if not is_admin(message):
        return

    memory.clear()

    save_memory()

    prepared.clear()

    await message.answer(
        "✅ <b>آرشیو پاک شد.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )


@router.message(
    F.text == "❌ لغو"
)
async def clear_cancel_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "❌ عملیات لغو شد.",
        reply_markup=main_menu()
    )


# ============================================================
# SETTINGS
# ============================================================

@router.message(
    F.text == "📢 کانال انتشار"
)
async def setting_channel_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "📢 <b>کانال انتشار</b>\n\n"
        f"<code>{escape_html(CHANNEL_ID)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_menu()
    )


@router.message(
    F.text == "🧠 مدل AI"
)
async def setting_model_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "🧠 <b>مدل هوش مصنوعی</b>\n\n"
        f"<code>{escape_html(MODEL)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_menu()
    )


@router.message(
    F.text == "🖼 سیستم تصویر"
)
async def setting_image_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "🖼 <b>سیستم تصویر</b>\n\n"
        "ربات در صورت وجود تصویر اصلی مقاله، "
        "همان تصویر را استفاده می‌کند.\n\n"
        "اگر مقاله تصویر نداشته باشد، "
        "تصویر تصادفی انتخاب نمی‌شود.",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_menu()
    )


@router.message(
    F.text == "✍️ قالب خبر"
)
async def setting_format_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "✍️ <b>قالب خبر</b>\n\n"
        "• تیتر فارسی\n"
        "• ۷ جمله خبری\n"
        "• یک پاراگراف واحد\n"
        "• تحلیل کل مقاله\n"
        "• استخراج تاریخ و اعداد\n"
        "• عدم کپی بند اول\n"
        "• شروع فارسی\n"
        "• دسته‌بندی خودکار\n"
        "• امضای Gamefa",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_menu()
    )


# ============================================================
# BACK
# ============================================================

@router.message(
    F.text == "🔙 بازگشت"
)
async def back_handler(
    message: Message
):

    if not is_admin(message):
        return

    user_modes[
        message.from_user.id
    ] = "main"

    await message.answer(
        "✨ <b>پنل مدیریت Gamefa</b>\n\n"
        "یک گزینه را انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )


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
# PUBLISH BUTTON
# ============================================================

@router.message(
    F.text == "📤 انتشار خبر"
)
async def publish_button_handler(
    message: Message
):

    if not is_admin(message):
        return

    await publish_news(
        message,
        message.from_user.id
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

    user_id = message.from_user.id

    # --------------------------------------------------------
    # اگر کاربر در حالت ارسال خبر باشد
    # --------------------------------------------------------

    mode = user_modes.get(
        user_id,
        "main"
    )

    if mode == "waiting_news":

        # اگر متن لینک Gamefa باشد
        url = extract_url(text)

        if url:
            parsed = urlparse(url)

            if "gamefa.com" not in (
                parsed.netloc.lower()
            ):

                await message.answer(
                    "⚠️ فقط لینک‌های Gamefa قابل پردازش هستند.",
                    reply_markup=back_menu()
                )

                return

        await process_news(
            message,
            text
        )

        return

    # --------------------------------------------------------
    # اگر لینک Gamefa مستقیماً ارسال شد
    # --------------------------------------------------------

    url = extract_url(text)

    if url:

        parsed = urlparse(url)

        if "gamefa.com" in (
            parsed.netloc.lower()
        ):

            await process_news(
                message,
                text
            )

            return

    # --------------------------------------------------------
    # متن عادی
    # --------------------------------------------------------

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
