import os
import re
import json
import html
import asyncio
import logging
import hashlib
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
    ReplyKeyboardRemove,
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

MIN_QUALITY_SCORE = 85

MAX_ARTICLE_CHARS = 70000

MAX_SOURCE_MEMORY_CHARS = 25000


# ============================================================
# GLOBALS
# ============================================================

memory = []

prepared = {}

processing_users = set()

user_states = {}


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
# TEXT NORMALIZATION
# ============================================================

def normalize_text(text):
    text = text or ""

    text = re.sub(
        r"https?://\S+",
        " ",
        text
    )

    text = text.lower()

    replacements = {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "ؤ": "و",
        "إ": "ا",
        "أ": "ا",
        "ٱ": "ا",
        "‌": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

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
        normalize_text(a).split()
    )

    words_b = set(
        normalize_text(b).split()
    )

    if not words_a or not words_b:
        return 0

    intersection = len(
        words_a & words_b
    )

    union = len(
        words_a | words_b
    )

    return intersection / union


def text_hash(text):
    normalized = normalize_text(text)

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def duplicate(text):
    normalized = normalize_text(text)

    if not normalized:
        return False

    current_hash = text_hash(text)

    for item in memory:

        old_hash = item.get(
            "hash",
            ""
        )

        if old_hash and old_hash == current_hash:
            return True

        old_source = item.get(
            "source",
            ""
        )

        if similarity(
            text,
            old_source
        ) >= 0.82:
            return True

        old_title = item.get(
            "title",
            ""
        )

        if old_title and similarity(
            text,
            old_title
        ) >= 0.90:
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


def strip_html(text):
    return re.sub(
        r"<[^>]+>",
        "",
        text or ""
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
        r"^[🎮🎬📱🟣📢🔵🟢🔴🟡⚪⚫\s]+",
        "",
        clean
    )

    if not clean:
        return False

    return bool(
        PERSIAN_RE.match(clean[0])
    )


def ensure_persian_start(text):
    if not text:
        return text

    text = text.strip()

    if starts_with_persian(text):
        return text

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
        "pc",
        "ps5",
        "ps4",
        "xbox series",
        "xbox one",
        "doom",
        "gta",
        "resident evil",
        "halo",
        "final fantasy",
        "devil may cry",
        "assassin",
        "elden ring",
        "fromsoftware",
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
        "disney",
        "marvel",
        "dc",
        "cinema"
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
# AI CLEANER
# ============================================================

def clean_ai_text(text):

    text = text or ""

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

    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text
    )

    text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        text
    )

    text = re.sub(
        r"^\s*[🎮🎬📱📢🟣🔵🟢🔴🟡⚪⚫]\s*",
        "",
        text,
        flags=re.M
    )

    text = re.sub(
        r"\n{2,}",
        "\n",
        text
    )

    return text.strip()


# ============================================================
# SENTENCE TOOLS
# ============================================================

def split_sentences(text):
    text = clean_ai_text(text)

    if not text:
        return []

    parts = re.split(
        r"(?<=[.!؟؛])\s+",
        text
    )

    result = []

    for part in parts:
        part = part.strip()

        if len(part) >= 15:
            result.append(part)

    return result


def remove_duplicate_sentences(sentences):
    result = []

    for sentence in sentences:

        duplicate_found = False

        for old in result:

            if similarity(
                sentence,
                old
            ) >= 0.75:

                duplicate_found = True
                break

        if not duplicate_found:
            result.append(sentence)

    return result


def build_single_paragraph(sentences):
    cleaned = []

    for sentence in sentences:

        sentence = sentence.strip()

        sentence = re.sub(
            r"\s+",
            " ",
            sentence
        )

        sentence = sentence.replace(
            "\n",
            " "
        )

        if sentence:
            cleaned.append(sentence)

    cleaned = remove_duplicate_sentences(
        cleaned
    )

    return " ".join(cleaned)


# ============================================================
# ARTICLE FETCH
# ============================================================

async def fetch_url(url):

    headers = {
        "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151 Safari/537.36"
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

            response.raise_for_status()

            final_url = str(
                response.url
            )

            raw = await response.text(
                errors="ignore"
            )

    return final_url, raw


async def fetch_gamefa(url):

    parsed = urlparse(url)

    if "gamefa.com" not in (
        parsed.netloc.lower()
    ):
        raise ValueError(
            "فقط لینک Gamefa پشتیبانی می‌شود."
        )

    final_url, raw = await fetch_url(
        url
    )

    soup = BeautifulSoup(
        raw,
        "html.parser"
    )

    # --------------------------------------------------------
    # Remove unnecessary elements
    # --------------------------------------------------------

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
            "header",
            "iframe",
            "advertisement"
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
    # META DESCRIPTION
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

        if meta and meta.get(
            "content"
        ):

            description = (
                meta["content"].strip()
            )

            break

    # --------------------------------------------------------
    # IMAGE COLLECTION
    # --------------------------------------------------------

    image_candidates = []

    image_meta_options = [
        {"property": "og:image"},
        {"property": "og:image:url"},
        {"name": "twitter:image"},
        {"name": "twitter:image:src"}
    ]

    for attrs in image_meta_options:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta and meta.get(
            "content"
        ):

            image_candidates.append(
                urljoin(
                    final_url,
                    meta["content"].strip()
                )
            )

    # --------------------------------------------------------
    # Article images
    # --------------------------------------------------------

    article_container = (
        soup.find("article")
        or soup.find(
            class_=re.compile(
                r"(article|post|entry|content)",
                re.I
            )
        )
        or soup
    )

    for img in article_container.find_all(
        "img"
    ):

        for attribute in [
            "src",
            "data-src",
            "data-lazy-src",
            "data-original"
        ]:

            image_url = img.get(
                attribute
            )

            if image_url:
                image_candidates.append(
                    urljoin(
                        final_url,
                        image_url
                    )
                )

    # --------------------------------------------------------
    # Clean image candidates
    # --------------------------------------------------------

    unique_images = []

    for image in image_candidates:

        if not image:
            continue

        if image not in unique_images:
            unique_images.append(
                image
            )

    # --------------------------------------------------------
    # Article paragraphs
    # --------------------------------------------------------

    paragraphs = article_container.find_all(
        [
            "p",
            "h2",
            "h3",
            "li"
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

        if len(text) < 25:
            continue

        if text in body_parts:
            continue

        # Remove obvious navigation garbage
        garbage_words = [
            "عضویت در کانال",
            "دنبال کنید",
            "تبلیغات",
            "مطالب مرتبط",
            "آخرین اخبار"
        ]

        if any(
            garbage in text
            for garbage in garbage_words
        ):
            continue

        body_parts.append(
            text
        )

    body = "\n".join(
        body_parts
    )

    body = body[:MAX_ARTICLE_CHARS]

    # --------------------------------------------------------
    # If paragraph extraction was weak
    # --------------------------------------------------------

    if len(body) < 300:

        text_content = article_container.get_text(
            "\n",
            strip=True
        )

        text_content = re.sub(
            r"\n+",
            "\n",
            text_content
        )

        body = text_content[
            :MAX_ARTICLE_CHARS
        ]

    return {
        "url": final_url,
        "title": title,
        "description": description,
        "body": body,
        "images": unique_images[:15]
    }


# ============================================================
# IMAGE DOWNLOAD
# ============================================================

async def download_image(url, index=0):

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

        elif "gif" in content_type:
            extension = ".gif"

        filename = (
            f"gamefa_news_{index}"
            f"_{hashlib.md5(url.encode()).hexdigest()[:8]}"
            f"{extension}"
        )

        path = Path(
            filename
        )

        path.write_bytes(data)

        return path

    except Exception as error:

        log.warning(
            "Image download error: %s",
            error
        )

        return None


async def find_best_image(images):

    if not images:
        return None

    # Try several images instead of only one.
    for index, image_url in enumerate(
        images[:10]
    ):

        path = await download_image(
            image_url,
            index
        )

        if path:
            return path

    return None


# ============================================================
# OPENAI
# ============================================================

def get_openai_client():
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده است."
        )

    return AsyncOpenAI(
        api_key=OPENAI_API_KEY
    )


async def ai_call(
    instructions,
    input_text,
    max_output_tokens=2500
):

    client = get_openai_client()

    response = await client.responses.create(
        model=MODEL,
        instructions=instructions,
        input=input_text,
        max_output_tokens=max_output_tokens
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
# JSON EXTRACTION
# ============================================================

def extract_json(text):

    text = text.strip()

    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"^```\s*",
        "",
        text
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(
        r"\{.*\}",
        text,
        flags=re.S
    )

    if match:

        try:
            return json.loads(
                match.group(0)
            )
        except Exception:
            pass

    raise ValueError(
        "AI نتوانست JSON معتبر تولید کند."
    )


# ============================================================
# IDEA 1-10:
# INFORMATION EXTRACTION
# ============================================================

EXTRACTOR_PROMPT = """
تو یک سیستم استخراج اطلاعات برای تحریریه Gamefa هستی.

کل مقاله را بررسی کن.

مهم:
فقط پاراگراف اول را بررسی نکن.
تمام مقاله را تحلیل کن.

اطلاعات مهم را استخراج کن.

باید موارد زیر را پیدا کنی:

- اتفاق اصلی
- تاریخ عرضه
- تاریخ‌های دیگر
- زمان پیش‌دانلود
- پلتفرم‌ها
- قیمت
- حجم دانلود
- حجم نصب
- سازنده
- ناشر
- بازیگران
- شخصیت‌ها
- کارگردان
- توسعه‌دهنده
- ویژگی‌های مهم
- اطلاعات گیم‌پلی
- اطلاعات داستانی
- منبع خبر
- وضعیت خبر
- رسمی یا غیررسمی بودن
- شایعه یا لیک بودن
- مهم‌ترین اعداد
- مهم‌ترین نام‌ها
- نکات مهم بخش‌های میانی و پایانی مقاله

قوانین:

1. هیچ اطلاعاتی را حدس نزن.
2. اگر اطلاعاتی در مقاله وجود ندارد مقدار null بده.
3. تاریخ‌ها را دقیقاً همان‌طور که در مقاله آمده‌اند ثبت کن.
4. بین تاریخ عرضه و تاریخ پیش‌دانلود تفاوت بگذار.
5. بین شایعه، گزارش، لیک و تأیید رسمی تفاوت بگذار.
6. اعداد را دقیق حفظ کن.
7. واحدها را حذف نکن.
8. نام بازی‌ها، شرکت‌ها و افراد را دقیق حفظ کن.
9. اطلاعات تکراری را یکی کن.
10. اطلاعات کم‌اهمیت را از اطلاعات حیاتی جدا کن.

فقط JSON معتبر برگردان.

ساختار:

{
  "main_event": "",
  "title_candidates": [],
  "release_date": null,
  "other_dates": [],
  "preload_date": null,
  "platforms": [],
  "price": null,
  "download_size": null,
  "install_size": null,
  "developer": null,
  "publisher": null,
  "people": [],
  "characters": [],
  "important_features": [],
  "gameplay_details": [],
  "story_details": [],
  "source": null,
  "status": "",
  "confirmation": "",
  "important_numbers": [],
  "important_names": [],
  "critical_facts": [],
  "secondary_facts": [],
  "article_summary": ""
}
"""


async def extract_information(article):

    result = await ai_call(
        EXTRACTOR_PROMPT,
        article,
        max_output_tokens=4000
    )

    data = extract_json(
        result
    )

    if not isinstance(
        data,
        dict
    ):
        raise ValueError(
            "ساختار اطلاعات AI نامعتبر است."
        )

    return data


# ============================================================
# IDEA 11-15:
# NEWS WRITER
# ============================================================

WRITER_PROMPT = """
تو سردبیر حرفه‌ای اخبار فارسی Gamefa هستی.

بر اساس Fact Sheet و متن مقاله، خبر نهایی را بنویس.

قوانین بسیار مهم:

1. کل مقاله را در نظر بگیر.
2. فقط پاراگراف اول را خلاصه نکن.
3. اطلاعات حیاتی Fact Sheet را از دست نده.
4. اگر تاریخ عرضه وجود دارد، در صورت ارتباط با خبر حتماً آن را بیاور.
5. اگر حجم وجود دارد، آن را دقیق بیاور.
6. اگر قیمت وجود دارد، آن را دقیق بیاور.
7. اگر پلتفرم وجود دارد، آن را دقیق بیاور.
8. اگر زمان پیش‌دانلود وجود دارد، آن را با تاریخ عرضه قاطی نکن.
9. اگر خبر رسمی نیست، ادبیات قطعی استفاده نکن.
10. هیچ اطلاعاتی از خودت اضافه نکن.
11. جمله‌های تکراری نساز.
12. جمله‌های پرکننده نساز.
13. هر جمله باید اطلاعات جدید داشته باشد.
14. نام‌های انگلیسی مهم را حفظ کن.
15. تیتر باید با فارسی شروع شود.
16. متن فارسی روان باشد.
17. از عبارت‌های تکراری مثل «براساس گزارش‌های منتشرشده» در چند جمله استفاده نکن.
18. از Markdown استفاده نکن.
19. از ایموجی استفاده نکن.
20. لینک نده.
21. آیدی کانال نده.

ساختار خروجی:

خط اول:
تیتر

خط‌های بعدی:
دقیقاً 7 جمله خبری.

اما توجه:
این 7 جمله نباید در 7 پاراگراف باشند.
هر 7 جمله باید در نهایت یک پاراگراف واحد باشند.

خبر باید تا حد ممکن اطلاعات حیاتی را پوشش دهد.

اگر مقاله اطلاعات کافی برای 7 جمله دارد، 7 جمله واقعی و متفاوت بنویس.

اگر اطلاعاتی وجود ندارد، جمله بی‌ارزش و ساختگی نساز؛ اطلاعات موجود را با جزئیات بیشتر و واقعی بازنویسی کن.

تیتر را در یک خط جدا بده.
"""


async def write_news(
    fact_sheet,
    article
):

    payload = (
        "FACT SHEET:\n"
        + json.dumps(
            fact_sheet,
            ensure_ascii=False,
            indent=2
        )
        + "\n\n"
        "ARTICLE:\n"
        + article
    )

    result = await ai_call(
        WRITER_PROMPT,
        payload,
        max_output_tokens=3000
    )

    return clean_ai_text(
        result
    )


# ============================================================
# IDEA 16:
# FORMAT NEWS
# ============================================================

def parse_writer_output(text):

    text = clean_ai_text(
        text
    )

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return "", ""

    title = lines[0]

    title = re.sub(
        r"^[🎮🎬📱📢🟣🔵🟢🔴🟡⚪⚫]\s*",
        "",
        title
    ).strip()

    title = ensure_persian_start(
        title
    )

    body_text = " ".join(
        lines[1:]
    )

    body_text = re.sub(
        r"\s+",
        " ",
        body_text
    ).strip()

    sentences = split_sentences(
        body_text
    )

    # If AI accidentally created fewer sentences,
    # do not fabricate information.
    sentences = remove_duplicate_sentences(
        sentences
    )

    body = build_single_paragraph(
        sentences
    )

    return title, body


def format_post(
    title,
    body
):

    if not title or not body:
        return ""

    category = detect_category(
        title + " " + body
    )

    full_title = (
        category
        + " "
        + title
    )

    result = (
        "<b>"
        + escape_html(
            full_title
        )
        + "</b>"
    )

    result += (
        "\n\n🟣 "
        + escape_html(
            body
        )
    )

    result += (
        "\n\n"
        "<b>🆔 @Gamefa_official</b>"
    )

    return result


# ============================================================
# IDEA 17-19:
# FACT CHECKER / REVIEWER
# ============================================================

REVIEWER_PROMPT = """
تو ویراستار نهایی اخبار Gamefa هستی.

خبر تولیدشده را با Fact Sheet و مقاله مقایسه کن.

باید بررسی کنی:

1. آیا اتفاق اصلی درست منتقل شده؟
2. آیا تاریخ عرضه در صورت وجود جا افتاده؟
3. آیا تاریخ پیش‌دانلود در صورت وجود جا افتاده؟
4. آیا پلتفرم‌ها درست هستند؟
5. آیا حجم درست است؟
6. آیا قیمت درست است؟
7. آیا اعداد مهم درست هستند؟
8. آیا نام‌ها درست هستند؟
9. آیا وضعیت رسمی/غیررسمی درست منتقل شده؟
10. آیا خبر چیزی را حدس زده؟
11. آیا جمله‌های تکراری وجود دارد؟
12. آیا جمله پرکننده وجود دارد؟
13. آیا خبر فقط بر اساس ابتدای مقاله نوشته شده؟
14. آیا اطلاعات مهم بخش‌های میانی و پایانی مقاله پوشش داده شده؟
15. آیا تیتر با مهم‌ترین اتفاق هماهنگ است؟
16. آیا متن فارسی طبیعی است؟
17. آیا شروع تیتر فارسی است؟
18. آیا متن یک پاراگراف است؟
19. آیا هر جمله اطلاعات جدید دارد؟

به هر بخش امتیاز بده.

امتیاز نهایی از 100.

همچنین تمام اطلاعات مهمی که در مقاله/Factsheet وجود دارد ولی در خبر نیامده را در missing_critical_facts قرار بده.

اگر اطلاعاتی در مقاله وجود دارد و خبر اشتباه گفته، آن را در incorrect_facts قرار بده.

فقط JSON معتبر بده.

ساختار:

{
  "score": 0,
  "accurate": true,
  "coverage_score": 0,
  "fact_score": 0,
  "title_score": 0,
  "language_score": 0,
  "duplication_score": 0,
  "missing_critical_facts": [],
  "incorrect_facts": [],
  "hallucinations": [],
  "repeated_sentences": [],
  "filler_sentences": [],
  "required_fixes": []
}
"""


async def review_news(
    fact_sheet,
    article,
    title,
    body
):

    payload = (
        "FACT SHEET:\n"
        + json.dumps(
            fact_sheet,
            ensure_ascii=False,
            indent=2
        )
        + "\n\nARTICLE:\n"
        + article
        + "\n\nTITLE:\n"
        + title
        + "\n\nNEWS:\n"
        + body
    )

    result = await ai_call(
        REVIEWER_PROMPT,
        payload,
        max_output_tokens=3500
    )

    return extract_json(
        result
    )


# ============================================================
# IDEA 20:
# AUTO CORRECTION
# ============================================================

CORRECTOR_PROMPT = """
تو ویراستار ارشد Gamefa هستی.

خبر زیر توسط AI نوشته شده اما Reviewer ایرادهایی پیدا کرده است.

وظیفه:
خبر را اصلاح کن.

قوانین:

- تمام missing_critical_facts مهم را در صورت ارتباط وارد کن.
- incorrect_facts را اصلاح کن.
- hallucinations را حذف کن.
- جمله‌های تکراری را حذف کن.
- جمله‌های پرکننده را حذف کن.
- تاریخ عرضه را در صورت وجود فراموش نکن.
- تاریخ پیش‌دانلود را در صورت وجود فراموش نکن.
- حجم را در صورت وجود فراموش نکن.
- پلتفرم را در صورت وجود فراموش نکن.
- قیمت را در صورت وجود فراموش نکن.
- اعداد را دقیق حفظ کن.
- وضعیت شایعه/گزارش/لیک/رسمی را دقیق حفظ کن.
- هیچ اطلاعاتی از خودت اضافه نکن.
- تیتر باید با فارسی شروع شود.
- متن خبر باید دقیقاً یک پاراگراف باشد.
- خروجی باید یک تیتر در خط اول و یک پاراگراف در خط دوم باشد.
- Markdown نده.
- ایموجی نده.
- لینک نده.
- آیدی کانال نده.

حداکثر تلاش را برای دقت واقعی انجام بده.
"""


async def correct_news(
    fact_sheet,
    article,
    title,
    body,
    review
):

    payload = (
        "FACT SHEET:\n"
        + json.dumps(
            fact_sheet,
            ensure_ascii=False,
            indent=2
        )
        + "\n\nARTICLE:\n"
        + article
        + "\n\nCURRENT TITLE:\n"
        + title
        + "\n\nCURRENT BODY:\n"
        + body
        + "\n\nREVIEW:\n"
        + json.dumps(
            review,
            ensure_ascii=False,
            indent=2
        )
    )

    result = await ai_call(
        CORRECTOR_PROMPT,
        payload,
        max_output_tokens=3000
    )

    return parse_writer_output(
        result
    )


# ============================================================
# QUALITY PIPELINE
# ============================================================

async def generate_high_quality_news(
    article
):

    # --------------------------------------------------------
    # STEP 1
    # Extract complete information
    # --------------------------------------------------------

    log.info(
        "AI step 1: extracting facts"
    )

    fact_sheet = await extract_information(
        article
    )

    # --------------------------------------------------------
    # STEP 2
    # Generate news
    # --------------------------------------------------------

    log.info(
        "AI step 2: writing news"
    )

    generated = await write_news(
        fact_sheet,
        article
    )

    title, body = parse_writer_output(
        generated
    )

    if not title or not body:
        raise RuntimeError(
            "AI نتوانست خبر معتبر تولید کند."
        )

    # --------------------------------------------------------
    # STEP 3
    # Review
    # --------------------------------------------------------

    log.info(
        "AI step 3: reviewing news"
    )

    review = await review_news(
        fact_sheet,
        article,
        title,
        body
    )

    score = int(
        review.get(
            "score",
            0
        ) or 0
    )

    # --------------------------------------------------------
    # STEP 4
    # Auto correction
    # --------------------------------------------------------

    if (
        score < MIN_QUALITY_SCORE
        or review.get(
            "missing_critical_facts"
        )
        or review.get(
            "incorrect_facts"
        )
        or review.get(
            "hallucinations"
        )
    ):

        log.info(
            "AI step 4: correcting news. Score=%s",
            score
        )

        title, body = await correct_news(
            fact_sheet,
            article,
            title,
            body,
            review
        )

        # ----------------------------------------------------
        # STEP 5
        # Final review
        # ----------------------------------------------------

        log.info(
            "AI step 5: final review"
        )

        final_review = await review_news(
            fact_sheet,
            article,
            title,
            body
        )

        score = int(
            final_review.get(
                "score",
                0
            ) or 0
        )

        review = final_review

    # --------------------------------------------------------
    # Local safety cleanup
    # --------------------------------------------------------

    body_sentences = split_sentences(
        body
    )

    body_sentences = remove_duplicate_sentences(
        body_sentences
    )

    body = build_single_paragraph(
        body_sentences
    )

    return {
        "title": title,
        "body": body,
        "fact_sheet": fact_sheet,
        "review": review,
        "score": score
    }


# ============================================================
# KEYBOARD - MAIN
# ============================================================

def main_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🔎 خبر جدید"
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
                    text="⚙️ تنظیمات"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


# ============================================================
# KEYBOARD - NEWS
# ============================================================

def news_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🔗 لینک Gamefa"
                ),
                KeyboardButton(
                    text="📝 متن خبر"
                )
            ],
            [
                KeyboardButton(
                    text="🚀 انتشار آماده"
                ),
                KeyboardButton(
                    text="🔙 بازگشت"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


# ============================================================
# KEYBOARD - ARCHIVE
# ============================================================

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


# ============================================================
# KEYBOARD - SETTINGS
# ============================================================

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


# ============================================================
# KEYBOARD - CONFIRM
# ============================================================

def confirm_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="⚠️ بله، پاک کن"
                ),
                KeyboardButton(
                    text="❌ لغو"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


# ============================================================
# STATE
# ============================================================

def set_state(
    user_id,
    state
):
    user_states[user_id] = state


def get_state(user_id):
    return user_states.get(
        user_id,
        "home"
    )


def clear_state(user_id):
    user_states.pop(
        user_id,
        None
    )


# ============================================================
# PREVIEW TEXT
# ============================================================

def make_preview_text(
    result
):

    title = result.get(
        "title",
        ""
    )

    body = result.get(
        "body",
        ""
    )

    score = result.get(
        "score",
        0
    )

    review = result.get(
        "review",
        {}
    )

    missing = review.get(
        "missing_critical_facts",
        []
    )

    title_display = escape_html(
        title
    )

    body_display = escape_html(
        body
    )

    text = (
        "<b>📰 پیش‌نمایش خبر</b>\n\n"
        "<b>"
        + title_display
        + "</b>\n\n"
        "🟣 "
        + body_display
        + "\n\n"
        f"🎯 امتیاز دقت AI: <b>{score}/100</b>"
    )

    if missing:

        text += (
            "\n⚠️ اطلاعاتی که Reviewer بررسی کرده: "
            + escape_html(
                ", ".join(
                    map(
                        str,
                        missing[:5]
                    )
                )
            )
        )

    return text


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

    try:

        url = extract_url(
            text
        )

        source_image = ""

        source_images = []

        article_title = ""

        article_body = ""

        description = ""

        # ====================================================
        # GAMEFA URL
        # ====================================================

        if url:

            status = await message.answer(
                "⏳ در حال دریافت کل مقاله Gamefa..."
            )

            article = await fetch_gamefa(
                url
            )

            source_image = (
                article.get(
                    "images",
                    [""] 
                )[0]
                if article.get(
                    "images"
                )
                else ""
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
                        "🧠 کل مقاله دریافت شد.\n"
                        "در حال استخراج اطلاعات مهم، تاریخ‌ها، اعداد و جزئیات..."
                    )
                except Exception:
                    pass

        else:

            source = text

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
        # AI PIPELINE
        # ====================================================

        if status:

            try:
                await status.edit_text(
                    "🧠 مرحله ۱ از ۵\n"
                    "در حال استخراج اطلاعات حیاتی مقاله..."
                )
            except Exception:
                pass

        result = await generate_high_quality_news(
            source
        )

        # ====================================================
        # IMAGE
        # ====================================================

        if status:

            try:
                await status.edit_text(
                    "🖼 در حال پیدا کردن تصویر اصلی مقاله..."
                )
            except Exception:
                pass

        image_path = await find_best_image(
            source_images
        )

        # ====================================================
        # FINAL POST
        # ====================================================

        post = format_post(
            result["title"],
            result["body"]
        )

        if not post:

            raise RuntimeError(
                "متن نهایی خالی است."
            )

        # ====================================================
        # MEMORY
        # ====================================================

        memory.append(
            {
                "hash": text_hash(source),
                "source": source[
                    :MAX_SOURCE_MEMORY_CHARS
                ],
                "post": post,
                "title": result["title"],
                "url": url or "",
                "facts": result["fact_sheet"],
                "review": result["review"],
                "score": result["score"]
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
            ),
            "title": result["title"],
            "body": result["body"],
            "score": result["score"],
            "facts": result["fact_sheet"]
        }

        # ====================================================
        # PREVIEW
        # ====================================================

        if status:

            try:
                await status.delete()
            except Exception:
                pass

        preview = make_preview_text(
            result
        )

        if image_path:

            try:

                await message.answer_photo(
                    FSInputFile(
                        image_path
                    ),
                    caption=preview,
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard()
                )

            except Exception as error:

                log.warning(
                    "Image preview error: %s",
                    error
                )

                await message.answer(
                    preview,
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard()
                )

        else:

            await message.answer(
                preview,
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard()
            )

        await message.answer(
            "✅ خبر آماده شد.\n\n"
            "برای انتشار از «🚀 انتشار آماده» استفاده کن.",
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
            "❌ هنوز خبری برای انتشار آماده نیست.",
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
            "✅ خبر با موفقیت در کانال منتشر شد.",
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

    clear_state(
        message.from_user.id
    )

    await message.answer(
        "✨ <b>پنل مدیریت Gamefa</b>\n\n"
        "سیستم جدید پردازش خبر فعال است.\n\n"
        "هوش مصنوعی ابتدا کل مقاله را تحلیل می‌کند، "
        "اطلاعات مهم را استخراج می‌کند، خبر را می‌نویسد، "
        "سپس خروجی را دوباره با مقاله تطبیق می‌دهد.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )


# ============================================================
# COMMANDS
# ============================================================

@router.message(Command("stats"))
async def stats_command(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "📊 آمار ربات\n\n"
        f"📰 اخبار آرشیو: {len(memory)}\n"
        f"💾 ظرفیت حافظه: {MAX_MEMORY}\n"
        f"🤖 مدل AI: {MODEL}\n"
        f"🎯 حداقل امتیاز کیفیت: {MIN_QUALITY_SCORE}/100",
        reply_markup=main_keyboard()
    )


@router.message(Command("clear"))
async def clear_command(
    message: Message
):

    if not is_admin(message):
        return

    set_state(
        message.from_user.id,
        "confirm_clear"
    )

    await message.answer(
        "⚠️ مطمئنی می‌خواهی کل آرشیو پاک شود؟",
        reply_markup=confirm_keyboard()
    )


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
# MAIN MENU HANDLERS
# ============================================================

@router.message(
    F.text == "🔎 خبر جدید"
)
async def new_news_handler(
    message: Message
):

    if not is_admin(message):
        return

    set_state(
        message.from_user.id,
        "news_menu"
    )

    await message.answer(
        "🔎 <b>خبر جدید</b>\n\n"
        "نوع ورودی را انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=news_keyboard()
    )


@router.message(
    F.text == "📁 آرشیو"
)
async def archive_handler(
    message: Message
):

    if not is_admin(message):
        return

    set_state(
        message.from_user.id,
        "archive_menu"
    )

    await message.answer(
        "📁 <b>آرشیو</b>\n\n"
        "گزینه موردنظر را انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=archive_keyboard()
    )


@router.message(
    F.text == "📊 آمار"
)
async def stats_button_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "📊 <b>آمار ربات</b>\n\n"
        f"📰 تعداد اخبار: <b>{len(memory)}</b>\n"
        f"💾 ظرفیت: <b>{MAX_MEMORY}</b>\n"
        f"🤖 مدل: <code>{escape_html(MODEL)}</code>\n"
        f"🎯 حداقل کیفیت: <b>{MIN_QUALITY_SCORE}/100</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )


@router.message(
    F.text == "⚙️ تنظیمات"
)
async def settings_handler(
    message: Message
):

    if not is_admin(message):
        return

    set_state(
        message.from_user.id,
        "settings_menu"
    )

    await message.answer(
        "⚙️ <b>تنظیمات</b>\n\n"
        "یک بخش را انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard()
    )


# ============================================================
# NEWS MENU
# ============================================================

@router.message(
    F.text == "🔗 لینک Gamefa"
)
async def gamefa_link_handler(
    message: Message
):

    if not is_admin(message):
        return

    set_state(
        message.from_user.id,
        "waiting_gamefa_link"
    )

    await message.answer(
        "🔗 لینک کامل مقاله Gamefa را بفرست.\n\n"
        "مثال:\n"
        "https://gamefa.com/1373482/...",
        reply_markup=main_keyboard()
    )


@router.message(
    F.text == "📝 متن خبر"
)
async def news_text_handler(
    message: Message
):

    if not is_admin(message):
        return

    set_state(
        message.from_user.id,
        "waiting_news_text"
    )

    await message.answer(
        "📝 متن خبر را بفرست.\n\n"
        "هوش مصنوعی کل متن را تحلیل می‌کند.",
        reply_markup=main_keyboard()
    )


@router.message(
    F.text == "🚀 انتشار آماده"
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
# ARCHIVE
# ============================================================

@router.message(
    F.text == "📚 آخرین اخبار"
)
async def latest_archive_handler(
    message: Message
):

    if not is_admin(message):
        return

    if not memory:

        await message.answer(
            "📚 آرشیو خالی است.",
            reply_markup=archive_keyboard()
        )

        return

    latest = memory[-15:]

    lines = [
        "📚 <b>آخرین اخبار</b>",
        ""
    ]

    for index, item in enumerate(
        reversed(latest),
        1
    ):

        title = item.get(
            "title",
            ""
        )

        if not title:

            post = strip_html(
                item.get(
                    "post",
                    ""
                )
            )

            title = (
                post.splitlines()[0]
                if post
                else "خبر بدون عنوان"
            )

        score = item.get(
            "score",
            "-"
        )

        lines.append(
            f"{index}. {escape_html(title[:120])}"
        )

        lines.append(
            f"   🎯 امتیاز: {score}/100"
        )

    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=archive_keyboard()
    )


@router.message(
    F.text == "🗑 پاکسازی آرشیو"
)
async def clear_archive_handler(
    message: Message
):

    if not is_admin(message):
        return

    set_state(
        message.from_user.id,
        "confirm_clear"
    )

    await message.answer(
        "⚠️ <b>پاکسازی آرشیو</b>\n\n"
        f"تعداد اخبار فعلی: {len(memory)}\n\n"
        "آیا مطمئنی؟",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_keyboard()
    )


@router.message(
    F.text == "⚠️ بله، پاک کن"
)
async def clear_yes_handler(
    message: Message
):

    if not is_admin(message):
        return

    if get_state(
        message.from_user.id
    ) != "confirm_clear":

        return

    memory.clear()

    prepared.clear()

    save_memory()

    clear_state(
        message.from_user.id
    )

    await message.answer(
        "✅ آرشیو کاملاً پاک شد.",
        reply_markup=main_keyboard()
    )


@router.message(
    F.text == "❌ لغو"
)
async def clear_cancel_handler(
    message: Message
):

    if not is_admin(message):
        return

    clear_state(
        message.from_user.id
    )

    await message.answer(
        "❌ عملیات لغو شد.",
        reply_markup=main_keyboard()
    )


# ============================================================
# SETTINGS
# ============================================================

@router.message(
    F.text == "📢 کانال انتشار"
)
async def channel_setting_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "📢 <b>کانال انتشار</b>\n\n"
        f"<code>{escape_html(CHANNEL_ID)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard()
    )


@router.message(
    F.text == "🧠 مدل AI"
)
async def model_setting_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "🧠 <b>مدل AI</b>\n\n"
        f"<code>{escape_html(MODEL)}</code>\n\n"
        "برای تغییر مدل، مقدار OPENAI_MODEL را در Environment Variables تغییر بده.",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard()
    )


@router.message(
    F.text == "🖼 سیستم تصویر"
)
async def image_setting_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "🖼 <b>سیستم تصویر</b>\n\n"
        "سیستم تصویر چند مرحله دارد:\n\n"
        "1. og:image\n"
        "2. Twitter image\n"
        "3. تصاویر داخل مقاله\n"
        "4. data-src\n"
        "5. lazy-load images\n"
        "6. چند تصویر مختلف در صورت شکست تصویر اول\n\n"
        "تصویر تصادفی تولید نمی‌شود.",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard()
    )


@router.message(
    F.text == "✍️ قالب خبر"
)
async def format_setting_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "✍️ <b>قالب فعلی</b>\n\n"
        "• تیتر با فارسی شروع می‌شود\n"
        "• یک پاراگراف خبری\n"
        "• ۷ جمله اصلی\n"
        "• بدون رفتن به خط بعد بعد از نقطه\n"
        "• بدون تکرار جمله\n"
        "• بدون اطلاعات ساختگی\n"
        "• استخراج تاریخ و اعداد\n"
        "• بررسی نهایی AI\n"
        "• امتیازدهی کیفیت\n"
        "• اصلاح خودکار",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard()
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

    clear_state(
        message.from_user.id
    )

    await message.answer(
        "🏠 <b>پنل اصلی Gamefa</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )


# ============================================================
# TEXT INPUT
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

    state = get_state(
        user_id
    )

    # --------------------------------------------------------
    # Waiting for Gamefa URL
    # --------------------------------------------------------

    if state == "waiting_gamefa_link":

        url = extract_url(
            text
        )

        if not url:

            await message.answer(
                "❌ لینک معتبر پیدا نشد.\n"
                "لینک مقاله Gamefa را ارسال کن."
            )

            return

        if "gamefa.com" not in (
            urlparse(url).netloc.lower()
        ):

            await message.answer(
                "❌ فقط لینک Gamefa قابل پردازش است."
            )

            return

        clear_state(
            user_id
        )

        await process_news(
            message,
            url
        )

        return

    # --------------------------------------------------------
    # Waiting for manual news
    # --------------------------------------------------------

    if state == "waiting_news_text":

        clear_state(
            user_id
        )

        await process_news(
            message,
            text
        )

        return

    # --------------------------------------------------------
    # Direct URL
    # --------------------------------------------------------

    url = extract_url(
        text
    )

    if url:

        await process_news(
            message,
            text
        )

        return

    # --------------------------------------------------------
    # Otherwise
    # --------------------------------------------------------

    await message.answer(
        "برای پردازش خبر ابتدا «🔎 خبر جدید» را بزن.",
        reply_markup=main_keyboard()
    )


# ============================================================
# UNKNOWN MESSAGE
# ============================================================

@router.message()
async def unknown_handler(
    message: Message
):

    if not is_admin(message):
        return

    await message.answer(
        "❓ این نوع ورودی پشتیبانی نمی‌شود.",
        reply_markup=main_keyboard()
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
        "Gamefa AI News Bot started"
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
        "Memory: %s",
        len(memory)
    )

    log.info(
        "Quality threshold: %s",
        MIN_QUALITY_SCORE
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
