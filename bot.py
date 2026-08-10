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
from aiogram.types import Message, FSInputFile

from openai import AsyncOpenAI


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

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

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "0") or "0"
)

MEMORY_FILE = Path("news_memory.json")

MAX_MEMORY = 1500

memory = []

prepared = {}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("gamefa")


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

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def similarity(a, b):
    a_words = set(
        norm(a).split()
    )

    b_words = set(
        norm(b).split()
    )

    if not a_words or not b_words:
        return 0

    return len(
        a_words & b_words
    ) / len(
        a_words | b_words
    )


def duplicate(text):
    normalized_text = norm(text)

    if len(normalized_text) < 30:
        return False

    for item in memory:
        old_source = item.get(
            "source",
            ""
        )

        if similarity(
            normalized_text,
            old_source
        ) >= 0.82:
            return True

    return False


# ============================================================
# ADMIN
# ============================================================

def is_admin(message):
    return bool(
        ADMIN_ID
        and message.from_user
        and message.from_user.id == ADMIN_ID
    )


# ============================================================
# URL
# ============================================================

def extract_url(text):
    if not text:
        return None

    match = re.search(
        r"https?://[^\s<>()]+",
        text
    )

    if not match:
        return None

    return match.group(
        0
    ).rstrip(
        ".,)]}"
    )


# ============================================================
# HTML
# ============================================================

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


LATIN_RE = re.compile(
    r"[A-Za-z]"
)


def contains_persian(text):
    if not text:
        return False

    return bool(
        PERSIAN_RE.search(text)
    )


def starts_with_persian(text):
    if not text:
        return False

    clean = text.strip()

    # حذف ایموجی‌های ابتدایی
    clean = re.sub(
        r"^[🎮🎬📱🟣🆔\s]+",
        "",
        clean
    )

    if not clean:
        return False

    # پیدا کردن اولین کاراکتر حرفی
    for char in clean:
        if char.isspace():
            continue

        if PERSIAN_RE.match(char):
            return True

        if LATIN_RE.match(char):
            return False

    return False


# ============================================================
# FORCE PERSIAN START
# ============================================================

def make_persian_start(
    text,
    is_title=False
):
    if not text:
        return ""

    text = text.strip()

    # حذف ایموجی‌های ابتدایی
    text = re.sub(
        r"^[🎮🎬📱🟣\s]+",
        "",
        text
    ).strip()

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
# CLEAN AI TEXT
# ============================================================

def clean_ai_text(text):
    text = text or ""

    # حذف Markdown bold
    text = re.sub(
        r"\*\*(.*?)\*\*",
        r"\1",
        text,
        flags=re.S
    )

    # حذف Markdown italic
    text = re.sub(
        r"__(.*?)__",
        r"\1",
        text,
        flags=re.S
    )

    # حذف code block
    text = re.sub(
        r"```(?:text|markdown|html)?",
        "",
        text,
        flags=re.I
    )

    text = text.replace(
        "```",
        ""
    )

    # حذف آیدی انتهایی
    text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        text
    )

    # حذف 🟣 از ابتدای خطوط
    text = re.sub(
        r"(?m)^\s*🟣\s*",
        "",
        text
    )

    return text.strip()


# ============================================================
# SPLIT NEWS
# ============================================================

def split_news_blocks(text):
    """
    خبرهای متعدد را که در یک پیام ارسال شده‌اند
    از یکدیگر جدا می‌کند.

    اگر چند تیتر با 🎮 / 🎬 / 📱 شروع شده باشند،
    هرکدام یک خبر مستقل در نظر گرفته می‌شوند.
    """

    text = text or ""

    # نرمال‌سازی line ending
    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    # حذف فاصله‌های اضافه
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    # اگر تیترهای دسته‌بندی‌دار داریم
    pattern = re.compile(
        r"(?m)(?=^\s*[🎮🎬📱]\s+)"
    )

    parts = pattern.split(
        text
    )

    parts = [
        part.strip()
        for part in parts
        if part.strip()
    ]

    if not parts:
        return [text.strip()]

    return parts


# ============================================================
# CATEGORY
# ============================================================

def detect_category(text):
    clean = re.sub(
        r"[🎮🎬📱🟣]",
        "",
        text or ""
    ).lower()

    gaming_words = [
        "بازی",
        "گیم",
        "گیمینگ",
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
        "assassin"
    ]

    movie_words = [
        "فیلم",
        "سریال",
        "بازیگر",
        "سینما",
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

    for word in gaming_words:
        if word in clean:
            return "🎮"

    for word in movie_words:
        if word in clean:
            return "🎬"

    return "📱"


# ============================================================
# FORMAT ONE NEWS
# ============================================================

def format_one_news(ai_text):
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

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title = lines[0]

    title = re.sub(
        r"^[🎮🎬📱🟣\s]+",
        "",
        title
    ).strip()

    title = make_persian_start(
        title,
        is_title=True
    )

    category = detect_category(
        ai_text
    )

    title = (
        category
        + " "
        + title
    )

    # --------------------------------------------------------
    # BODY
    # --------------------------------------------------------

    body_lines = lines[1:]

    paragraphs = []

    for line in body_lines:
        line = re.sub(
            r"^\s*[🟣•\-]\s*",
            "",
            line
        ).strip()

        if not line:
            continue

        # هر بند باید با فارسی شروع شود
        line = make_persian_start(
            line,
            is_title=False
        )

        paragraphs.append(
            "🟣 " + line
        )

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    result = (
        "<b>"
        + escape_html(title)
        + "</b>"
    )

    if paragraphs:
        result += (
            "\n\n"
            + "\n\n".join(
                escape_html(
                    paragraph
                )
                for paragraph in paragraphs
            )
        )

    result += (
        "\n\n"
        "<b>🆔 @Gamefa_official</b>"
    )

    return result


# ============================================================
# FORMAT MULTIPLE NEWS
# ============================================================

def format_post(ai_text):
    """
    خروجی AI را به یک یا چند خبر تبدیل می‌کند.
    """

    ai_text = clean_ai_text(
        ai_text
    )

    if not ai_text:
        return ""

    blocks = split_news_blocks(
        ai_text
    )

    formatted = []

    for block in blocks:
        result = format_one_news(
            block
        )

        if result:
            formatted.append(
                result
            )

    return "\n\n".join(
        formatted
    )


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
            "Chrome/151.0 Safari/537.36"
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

    # حذف عناصر غیرمتنی
    for element in soup.find_all(
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

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # SOURCE IMAGE
    # --------------------------------------------------------

    image = ""

    image_options = [
        {"property": "og:image"},
        {"property": "og:image:url"},
        {"name": "twitter:image"},
        {"name": "twitter:image:src"}
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

    # --------------------------------------------------------
    # BODY
    # --------------------------------------------------------

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
# AI PROMPT
# ============================================================

PROMPT = """
تو ویراستار حرفه‌ای اخبار کانال Gamefa هستی.

از اطلاعات داده‌شده یک یا چند پست خبری فارسی آماده انتشار بساز.

قوانین بسیار مهم:

1. اگر ورودی شامل چند خبر است، هر خبر را جداگانه نگه دار.

2. خط اول هر خبر فقط تیتر باشد.

3. تیتر هر خبر حتماً با یک کلمه یا عبارت فارسی شروع شود.

4. هیچ تیتر یا جمله‌ای را با نام انگلیسی شروع نکن.

مثال غلط:
Netflix نسخه آمریکایی Squid Game را...

مثال درست:
نتفلیکس نسخه آمریکایی Squid Game را...

5. متن خبر باید فارسی، روان و طبیعی باشد.

6. ابتدای EVERY paragraph باید فارسی باشد.

7. هر پاراگراف را با نام انگلیسی، نام شرکت، نام بازی، نام فیلم، نام شخص یا برند انگلیسی شروع نکن.

مثال غلط:
Brad Pitt در گفت‌وگویی تازه...

مثال درست:
برد پیت در گفت‌وگویی تازه با مجله Esquire...

مثال دیگر:
Square Enix در گزارش مالی خود...

غلط است.

درست:
شرکت Square Enix در گزارش مالی خود...

8. اگر نام شخص یا شرکت معادل فارسی شناخته‌شده دارد، در ابتدای جمله از شکل فارسی آن استفاده کن.

مثال:
Brad Pitt → برد پیت
David Fincher → دیوید فینچر
Hideki Kamiya → هیدکی کامیا
Netflix → نتفلیکس
Square Enix → شرکت Square Enix

9. نام انگلیسی اصلی را در ادامه جمله حفظ کن، اما نباید اولین عبارت جمله باشد.

10. هیچ جمله‌ای را با حروف انگلیسی شروع نکن.

11. اطلاعات ساختگی اضافه نکن.

12. متن را خلاصه و خبری بنویس.

13. Markdown تولید نکن.

14. HTML تولید نکن.

15. لینک تولید نکن.

16. منبع تولید نکن.

17. @Gamefa_official تولید نکن.

18. ایموجی 🟣 تولید نکن.

19. اگر خبر مربوط به بازی است، تیتر را با 🎮 شروع کن.

20. اگر خبر مربوط به فیلم یا سریال است، تیتر را با 🎬 شروع کن.

21. اگر خبر مربوط به فناوری، هوش مصنوعی، موبایل یا سخت‌افزار است، تیتر را با 📱 شروع کن.

22. بعد از ایموجی دسته‌بندی نیز اولین کلمه واقعی تیتر باید فارسی باشد.

23. برای هر خبر فقط یک پاراگراف اصلی ایجاد کن، مگر اینکه اطلاعات واقعاً نیازمند چند پاراگراف باشد.

24. اگر چند خبر در ورودی وجود دارد، بین خبرها یک خط خالی قرار بده.

خروجی فقط متن خبر باشد.
"""


# ============================================================
# GENERATE NEWS
# ============================================================

async def generate_news(source):
    client = AsyncOpenAI(
        api_key=OPENAI_API_KEY
    )

    response = await client.responses.create(
        model=MODEL,
        instructions=PROMPT,
        input=source,
        max_output_tokens=1800
    )

    return (
        response.output_text
        or ""
    ).strip()


# ============================================================
# IMAGE DOWNLOAD
# ============================================================

async def download_image(url):
    """
    فقط برای تصویر واقعی موجود در منبع استفاده می‌شود.
    هیچ جست‌وجوی تصادفی تصویر انجام نمی‌شود.
    """

    if not url:
        return None

    try:
        headers = {
            "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/151.0 Safari/537.36"
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

        # حداقل 1KB
        if len(data) < 1000:
            return None

        # حداکثر 15MB
        if len(data) > 15 * 1024 * 1024:
            return None

        if (
            "jpeg" in content_type
            or "jpg" in content_type
        ):
            extension = ".jpg"

        elif "webp" in content_type:
            extension = ".webp"

        elif "gif" in content_type:
            extension = ".gif"

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
# FIND SOURCE IMAGE
# ============================================================

async def find_source_image(
    source_image
):
    """
    مهم:
    اگر منبع تصویر داشته باشد، همان تصویر استفاده می‌شود.
    اگر منبع تصویر نداشته باشد، None برمی‌گردد.
    هیچ تصویر تصادفی از اینترنت انتخاب نمی‌شود.
    """

    if not source_image:
        log.info(
            "Source has no image. Text only."
        )
        return None

    path = await download_image(
        source_image
    )

    if path:
        log.info(
            "Using original source image."
        )
        return path

    log.info(
        "Source image could not be downloaded. Text only."
    )

    return None


# ============================================================
# PROCESS NEWS
# ============================================================

async def process_news(
    message,
    text
):
    url = extract_url(
        text
    )

    source_image = ""

    article_title = ""

    article_body = ""

    source = text

    # --------------------------------------------------------
    # URL / GAMEFA
    # --------------------------------------------------------

    if url:
        await message.answer(
            "⏳ در حال دریافت خبر..."
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

    # --------------------------------------------------------
    # DUPLICATE
    # --------------------------------------------------------

    if duplicate(source):
        await message.answer(
            "⚠️ این خبر یا یک خبر بسیار مشابه قبلاً دریافت شده است."
        )
        return

    # --------------------------------------------------------
    # AI
    # --------------------------------------------------------

    await message.answer(
        "✍️ در حال آماده‌سازی متن خبر..."
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

    # --------------------------------------------------------
    # IMAGE
    # --------------------------------------------------------

    await message.answer(
        "🖼 در حال بررسی تصویر اصلی خبر..."
    )

    image_path = await find_source_image(
        source_image
    )

    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    memory.append(
        {
            "source": source[:16000],
            "post": post,
            "url": url or ""
        }
    )

    save_memory()

    # --------------------------------------------------------
    # PREPARE
    # --------------------------------------------------------

    user_id = (
        message.from_user.id
        if message.from_user
        else 0
    )

    prepared[user_id] = {
        "text": post,
        "image": (
            str(image_path)
            if image_path
            else ""
        )
    }

    # --------------------------------------------------------
    # PREVIEW
    # --------------------------------------------------------

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
                "Photo preview failed: %s",
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
        "✅ خبر آماده انتشار است.\n\n"
        "برای ارسال به کانال:\n"
        "/publish"
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
async def start(
    message: Message
):
    if not is_admin(message):
        await message.answer(
            "این ربات خصوصی است."
        )
        return

    await message.answer(
        "ربات Gamefa آماده است.\n\n"
        "لینک Gamefa یا متن خبر را ارسال کن.\n\n"
        "/publish - انتشار خبر\n"
        "/stats - آمار\n"
        "/clear - پاک کردن حافظه"
    )


# ============================================================
# STATS
# ============================================================

@router.message(
    Command("stats")
)
async def stats(
    message: Message
):
    if not is_admin(message):
        return

    await message.answer(
        "📊 تعداد خبرهای ذخیره‌شده: "
        + str(len(memory))
    )


# ============================================================
# CLEAR
# ============================================================

@router.message(
    Command("clear")
)
async def clear(
    message: Message
):
    if not is_admin(message):
        return

    memory.clear()

    save_memory()

    await message.answer(
        "✅ حافظه ربات پاک شد."
    )


# ============================================================
# PUBLISH
# ============================================================

@router.message(
    Command("publish")
)
async def publish(
    message: Message
):
    if not is_admin(message):
        return

    user_id = (
        message.from_user.id
        if message.from_user
        else 0
    )

    item = prepared.get(
        user_id
    )

    if not item:
        await message.answer(
            "❌ هنوز خبری برای انتشار آماده نشده است."
        )
        return

    try:
        image = item.get(
            "image",
            ""
        )

        text = item.get(
            "text",
            ""
        )

        # ----------------------------------------------------
        # WITH ORIGINAL IMAGE
        # ----------------------------------------------------

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
                    "Channel photo failed: %s",
                    error
                )

                await message.bot.send_message(
                    CHANNEL_ID,
                    text,
                    parse_mode=ParseMode.HTML
                )

        # ----------------------------------------------------
        # TEXT ONLY
        # ----------------------------------------------------

        else:
            await message.bot.send_message(
                CHANNEL_ID,
                text,
                parse_mode=ParseMode.HTML
            )

        await message.answer(
            "✅ خبر با موفقیت در کانال منتشر شد."
        )

        # پاک کردن خبر منتشرشده
        prepared.pop(
            user_id,
            None
        )

    except Exception as error:
        log.exception(
            "Publish error"
        )

        await message.answer(
            "❌ خطا هنگام انتشار:\n"
            + str(error)[:1500]
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
    if not is_admin(message):
        return

    text = (
        message.text
        or ""
    ).strip()

    if not text:
        return

    # دستورات را دوباره پردازش نکن
    if text.startswith("/"):
        return

    try:
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
        "Gamefa bot started successfully."
    )

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
