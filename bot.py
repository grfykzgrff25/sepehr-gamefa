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
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
        )
    )

except Exception:
    ADMIN_ID = 0



MEMORY_FILE = Path(
    "news_memory.json"
)


MAX_MEMORY = 1500


IMAGE_DIR = Path(
    "gamefa_images"
)

IMAGE_DIR.mkdir(
    parents=True,
    exist_ok=True
)



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

            memory = data[
                -MAX_MEMORY:
            ]

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
                memory[
                    -MAX_MEMORY:
                ],
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



def word_similarity(
    a,
    b
):

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



def similarity(
    a,
    b
):

    return word_similarity(
        a,
        b
    )



def text_hash(text):

    normalized = norm(
        text
    )


    return hashlib.sha256(
        normalized.encode(
            "utf-8"
        )
    ).hexdigest()



def duplicate(
    text,
    title=""
):

    new_hash = text_hash(
        text
    )


    for item in memory:


        old_hash = item.get(
            "hash",
            ""
        )


        if old_hash == new_hash:

            return True



        old_title = item.get(
            "title",
            ""
        )


        if title and old_title:

            if similarity(
                title,
                old_title
            ) >= 0.88:

                return True



        old_source = item.get(
            "source",
            ""
        )


        if old_source:

            if similarity(
                text,
                old_source
            ) >= 0.84:

                return True



    return False



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



    return match.group(0).rstrip(
        ".,)]}"
    )



# ============================================================
# HTML / TEXT CLEAN
# ============================================================


def escape_html(text):

    return html.escape(
        text or "",
        quote=False
    )



def clean_text(text):

    text = text or ""


    text = re.sub(
        r"\s+",
        " ",
        text
    )


    return text.strip()



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
# PERSIAN START CONTROL
# ============================================================

PERSIAN_RE = re.compile(
    r"[\u0600-\u06FF]"
)


def starts_with_persian(text):

    if not text:
        return False


    clean = text.strip()


    clean = re.sub(
        r"^[🎮🎬📱📢📰🟣\s]+",
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
        "براساس گزارش منتشرشده، "
        + text
    )



# ============================================================
# CATEGORY
# ============================================================


def detect_category(text):

    low = (
        text or ""
    ).lower()



    game_words = [

        "بازی",
        "game",
        "gaming",
        "playstation",
        "xbox",
        "steam",
        "nintendo",
        "ps5",
        "ps4",
        "gta",
        "doom",
        "halo",
        "elden ring",
        "assassin",
        "sony",
        "microsoft"

    ]



    movie_words = [

        "فیلم",
        "سریال",
        "movie",
        "film",
        "series",
        "netflix",
        "marvel",
        "dc",
        "actor",
        "actress",
        "cinema"

    ]



    if any(
        x in low
        for x in game_words
    ):

        return "🎮"



    if any(
        x in low
        for x in movie_words
    ):

        return "🎬"



    return "📢"




# ============================================================
# AI OUTPUT CLEANER
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



    forbidden = [

        r"(?im)^\s*reviewer.*$",
        r"(?im)^\s*ai score.*$",
        r"(?im)^\s*accuracy score.*$",
        r"(?im)^\s*امتیاز دقت.*$",
        r"(?im)^\s*هوش مصنوعی.*$",
        r"(?im)^\s*اطلاعات استخراج شده.*$"

    ]



    for pattern in forbidden:

        text = re.sub(
            pattern,
            "",
            text
        )



    text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        text
    )



    return text.strip()




# ============================================================
# ARTICLE CLEANING
# ============================================================


REMOVE_SELECTORS = [

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

    ".related",
    ".related-posts",
    ".recommended",
    ".comments",
    ".comment",
    ".author-box",
    ".author-info",
    ".advertisement",
    ".ads",
    ".banner",
    ".sidebar",
    ".widget",
    ".social-share",
    ".breadcrumbs",
    ".navigation"

]



def remove_unwanted_elements(
    soup
):

    for selector in REMOVE_SELECTORS:

        try:

            for item in soup.select(
                selector
            ):

                item.decompose()


        except Exception:

            pass




def is_probably_noise(text):

    if not text:
        return True



    low = text.lower()



    noise = [

        "مطالب مرتبط",
        "مطالب پیشنهادی",
        "بیشتر بخوانید",
        "related",
        "recommended",
        "subscribe",
        "newsletter",
        "تبلیغات",
        "advertisement",
        "نویسنده",
        "author",
        "دیدگاه",
        "comments"

    ]



    return any(
        x in low
        for x in noise
    )




# ============================================================
# FETCH GAMEFA ARTICLE
# ============================================================


async def fetch_gamefa(
    url
):

    parsed = urlparse(
        url
    )



    if "gamefa.com" not in parsed.netloc.lower():

        raise ValueError(
            "فقط لینک Gamefa پشتیبانی می‌شود."
        )



    headers = {

        "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",

        "Accept-Language":
        "fa-IR,fa;q=0.9"

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



    remove_unwanted_elements(
        soup
    )



    # -------------------------------
    # TITLE
    # -------------------------------


    title = ""



    h1 = soup.find(
        "h1"
    )


    if h1:

        title = clean_text(
            h1.get_text(
                " ",
                strip=True
            )
        )



    elif soup.title:

        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
        )



    # -------------------------------
    # DESCRIPTION
    # -------------------------------


    description = ""



    for attrs in [

        {"name":"description"},
        {"property":"og:description"},
        {"name":"twitter:description"}

    ]:


        meta = soup.find(
            "meta",
            attrs=attrs
        )


        if meta and meta.get(
            "content"
        ):

            description = clean_text(
                meta["content"]
            )

            break




    # -------------------------------
    # IMAGE
    # -------------------------------


    images = []



    for attrs in [

        {"property":"og:image"},
        {"property":"og:image:url"},
        {"name":"twitter:image"}

    ]:


        meta = soup.find(
            "meta",
            attrs=attrs
        )


        if meta and meta.get(
            "content"
        ):

            images.append(
                urljoin(
                    final_url,
                    meta["content"]
                )
            )



    # -------------------------------
    # ARTICLE BODY
    # -------------------------------


    article = None



    for selector in [

        "article",
        ".entry-content",
        ".post-content",
        ".td-post-content",
        ".article-content"

    ]:


        found = soup.select_one(
            selector
        )


        if found:

            article = found
            break



    if article is None:

        article = soup




    paragraphs = []



    seen = set()



    for p in article.find_all(
        [
            "p",
            "h2",
            "h3"
        ]
    ):


        text = clean_text(
            p.get_text(
                " ",
                strip=True
            )
        )



        if len(text) < 35:

            continue



        if is_probably_noise(
            text
        ):

            continue



        key = norm(
            text
        )



        if key in seen:

            continue



        seen.add(
            key
        )


        paragraphs.append(
            text
        )



    body = "\n".join(
        paragraphs
    )



    body = body[:70000]



    # fallback image

    if not images:


        img = article.find(
            "img"
        )


        if img:

            src = (
                img.get("src")
                or img.get("data-src")
            )


            if src:

                images.append(
                    urljoin(
                        final_url,
                        src
                    )
                )




    return {

        "url": final_url,

        "title": title,

        "description": description,

        "body": body,

        "image":
            images[0]
            if images
            else ""

    }
    # ============================================================
# OPENAI CLIENT
# ============================================================


def get_ai_client():

    if not OPENAI_API_KEY:

        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده است."
        )


    return AsyncOpenAI(
        api_key=OPENAI_API_KEY
    )



# ============================================================
# FACT EXTRACTION PROMPT
# ============================================================


FACT_PROMPT = r"""
تو دستیار تحریریه Gamefa هستی.

وظیفه تو تولید خبر نیست.

فقط اطلاعات واقعی و مستقیم مقاله را استخراج کن.

موارد زیر را نادیده بگیر:

- مطالب مرتبط
- پیشنهادهای سایت
- تبلیغات
- اطلاعات نویسنده
- Reviewer
- متن‌های جانبی
- اطلاعات مربوط به AI

فقط موضوع اصلی مقاله را بررسی کن.

اطلاعات مهم:

- اتفاق اصلی
- نام افراد
- نام بازی
- نام فیلم
- شرکت‌ها
- سازندگان
- ناشر
- پلتفرم‌ها
- تاریخ عرضه
- قیمت
- حجم
- آمار
- اعداد مهم
- بازیگران
- کارگردان
- وضعیت پروژه

اگر اطلاعاتی وجود ندارد، چیزی نساز.

خروجی فقط JSON معتبر باشد.

ساختار:

{
"main_topic":"",
"main_event":"",
"facts":[
 {
  "fact":"",
  "importance":1,
  "type":""
 }
],
"dates":[],
"platforms":[],
"numbers":[],
"people":[],
"companies":[],
"status":""
}

importance بین 1 تا 5 باشد.
"""



# ============================================================
# EXTRACT FACTS
# ============================================================


async def extract_facts(
    source
):

    client = get_ai_client()


    prompt = (

        "عنوان:\n"
        + source.get(
            "title",
            ""
        )

        +

        "\n\nتوضیحات:\n"
        + source.get(
            "description",
            ""
        )

        +

        "\n\nمتن مقاله:\n"
        + source.get(
            "body",
            ""
        )

    )



    response = await client.responses.create(

        model=MODEL,

        instructions=FACT_PROMPT,

        input=prompt,

        max_output_tokens=3000

    )



    raw = (
        response.output_text
        or ""
    ).strip()



    raw = re.sub(
        r"```json",
        "",
        raw,
        flags=re.I
    )


    raw = re.sub(
        r"```",
        "",
        raw
    ).strip()



    try:

        return json.loads(
            raw
        )


    except Exception:


        start = raw.find(
            "{"
        )

        end = raw.rfind(
            "}"
        )


        if start != -1 and end != -1:

            try:

                return json.loads(
                    raw[
                        start:end+1
                    ]
                )

            except Exception:

                pass



        raise RuntimeError(
            "JSON استخراج Fact نامعتبر است."
        )




# ============================================================
# NEWS GENERATION PROMPT
# ============================================================


NEWS_PROMPT = r"""
تو سردبیر اخبار Gamefa هستی.

از FACT های داده شده یک خبر فارسی تولید کن.

قوانین:

خروجی فقط شامل:

خط اول:
تیتر

خط دوم:
متن خبر

متن خبر باید دقیقاً 7 جمله باشد.

تمام 7 جمله باید داخل یک پاراگراف باشند.

بین جمله‌ها Enter نزن.


قوانین مهم:

فقط از FACT استفاده کن.

هیچ اطلاعاتی اضافه نکن.

هیچ چیزی حدس نزن.


نباید بنویسی:

طبق مقاله
طبق توضیحات صفحه
Reviewer
AI
هوش مصنوعی
Fact
اطلاعات استخراج شده


تیتر:

- فارسی شروع شود.
- کوتاه و خبری باشد.
- اگر نام انگلیسی در ابتدای تیتر است، قبلش عبارت فارسی قرار بده.


متن:

- روان و طبیعی باشد.
- نام‌های انگلیسی مهم حفظ شوند.
- تاریخ‌ها و اعداد مهم حذف نشوند.


خروجی فقط تیتر و متن خبر باشد.
"""




# ============================================================
# GENERATE NEWS
# ============================================================


async def generate_news(
    source,
    facts,
    retry_instruction=""
):

    client = get_ai_client()



    input_text = (

        "FACT ها:\n\n"

        +

        json.dumps(
            facts,
            ensure_ascii=False,
            indent=2
        )

        +

        "\n\nعنوان:\n"

        +

        source.get(
            "title",
            ""
        )

        +

        "\n\n"

        +

        retry_instruction

    )



    response = await client.responses.create(

        model=MODEL,

        instructions=NEWS_PROMPT,

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
# SAFE SENTENCE SPLITTER
# ============================================================


def split_sentences(
    text
):

    text = clean_ai_text(
        text
    )


    lines = [

        x.strip()

        for x in text.splitlines()

        if x.strip()

    ]



    if not lines:

        return "", []



    title = lines[0]



    body = " ".join(
        lines[1:]
    )



    body = re.sub(
        r"\s+",
        " ",
        body
    ).strip()



    # جداکننده امن جمله

    sentences = re.split(

        r'(?<=[.!؟])\s+',

        body

    )



    sentences = [

        x.strip()

        for x in sentences

        if x.strip()

    ]



    return title, sentences




# ============================================================
# VALIDATOR جدید
# ============================================================


FORBIDDEN_OUTPUT_TERMS = [

    "reviewer",
    "ai score",
    "accuracy score",
    "امتیاز دقت",
    "هوش مصنوعی",
    "fact",
    "اطلاعات استخراج شده",
    "متن کامل صفحه"

]



def validate_generated_output(
    generated
):

    title, sentences = split_sentences(
        generated
    )



    if not title:

        return False



    if len(sentences) < 5:

        return False



    if len(sentences) > 9:

        return False



    combined = (

        title
        +
        " "
        +
        " ".join(sentences)

    ).lower()



    for term in FORBIDDEN_OUTPUT_TERMS:


        if term.lower() in combined:

            return False



    if not starts_with_persian(
        title
    ):

        return False



    return True
    # ============================================================
# SENTENCE CLEAN
# ============================================================


def clean_sentence(
    sentence
):

    sentence = sentence.strip()


    sentence = re.sub(
        r"^[•\-\–\—\d\.\)]+\s*",
        "",
        sentence
    )


    sentence = re.sub(
        r"^[🎮🎬📢📱📰🟣]+\s*",
        "",
        sentence
    )


    return sentence.strip()




# ============================================================
# FIX ENGLISH START
# ============================================================


def fix_english_start(
    text,
    title=False
):

    if not text:

        return text



    text = text.strip()



    if starts_with_persian(
        text
    ):

        return text



    prefixes_title = [

        "بازی",
        "فیلم",
        "گزارش جدید درباره",
        "خبر جدید درباره"

    ]



    prefixes_text = [

        "براساس گزارش منتشرشده،",
        "طبق اطلاعات منتشرشده،",
        "در جدیدترین اخبار،"

    ]



    if title:

        return (
            prefixes_title[0]
            +
            " "
            +
            text
        )


    return (

        prefixes_text[0]
        +
        " "
        +
        text

    )




# ============================================================
# FORMAT POST
# ============================================================


def format_post(
    generated
):

    generated = clean_ai_text(
        generated
    )



    title, sentences = split_sentences(
        generated
    )



    sentences = [

        clean_sentence(
            x
        )

        for x in sentences

        if clean_sentence(x)

    ]



    # اگر بیشتر از ۷ جمله بود
    # فقط ۷ جمله اول

    if len(sentences) > 7:

        sentences = sentences[:7]



    if len(sentences) < 5:

        return ""



    title = fix_english_start(
        title,
        title=True
    )



    title = clean_sentence(
        title
    )



    body_parts = []



    for sentence in sentences:


        sentence = fix_english_start(
            sentence,
            title=False
        )


        body_parts.append(
            sentence
        )



    body = " ".join(
        body_parts
    )



    category = detect_category(

        title
        +
        " "
        +
        body

    )



    final_title = (

        category
        +
        " "
        +
        title

    )



    result = (

        "<b>"
        +
        escape_html(
            final_title
        )
        +
        "</b>\n\n"

        +

        "🟣 "
        +
        escape_html(
            body
        )

        +

        "\n\n"

        +

        "<b>🆔 @Gamefa_official</b>"

    )



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


        timeout = aiohttp.ClientTimeout(
            total=35
        )


        headers = {

            "User-Agent":
            "Mozilla/5.0"

        }



        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers
        ) as session:


            async with session.get(
                url
            ) as response:


                if response.status != 200:

                    return None



                data = await response.read()



        if len(data) < 1000:

            return None



        if len(data) > 15 * 1024 * 1024:

            return None




        ext = Path(
            urlparse(url).path
        ).suffix.lower()



        if ext not in [

            ".jpg",
            ".jpeg",
            ".png",
            ".webp"

        ]:

            ext = ".jpg"



        filename = (

            "gamefa_"

            +

            hashlib.md5(
                url.encode()
            ).hexdigest()

            +

            ext

        )



        path = (

            IMAGE_DIR
            /
            filename

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
    source
):

    image = source.get(
        "image",
        ""
    )


    if not image:

        return None



    return await download_image(
        image
    )




# ============================================================
# INLINE PUBLISH BUTTON
# ============================================================


def publish_keyboard():

    return InlineKeyboardMarkup(

        inline_keyboard=[

            [

                InlineKeyboardButton(

                    text="📢 انتشار در کانال",

                    callback_data="publish_current"

                )

            ]

        ]

    )




# ============================================================
# PREPARE PREVIEW
# ============================================================


async def prepare_preview(
    user_id,
    post,
    image_path
):

    prepared[user_id] = {

        "text":
            post,

        "image":
            str(image_path)
            if image_path
            else ""

    }




# ============================================================
# SEND PREVIEW (عکس + متن یک پیام)
# ============================================================


async def send_preview(
    message,
    post,
    image_path
):


    if image_path and Path(image_path).exists():


        # کپشن تلگرام حداکثر ۱۰۲۴ کاراکتر است

        if len(post) <= 1024:


            await message.answer_photo(

                FSInputFile(
                    image_path
                ),

                caption=post,

                parse_mode=ParseMode.HTML,

                reply_markup=publish_keyboard()

            )


        else:


            # اگر متن خیلی طولانی بود

            short_caption = post[:1000] + "..."



            await message.answer_photo(

                FSInputFile(
                    image_path
                ),

                caption=short_caption,

                parse_mode=ParseMode.HTML,

                reply_markup=publish_keyboard()

            )



    else:


        await message.answer(

            post,

            parse_mode=ParseMode.HTML,

            reply_markup=publish_keyboard()

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
            "⏳ یک خبر در حال پردازش است."
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



        # دریافت مقاله


        if url:


            status = await message.answer(
                "⏳ در حال دریافت مقاله Gamefa..."
            )


            source = await fetch_gamefa(
                url
            )



        else:


            source = {

                "url":"",
                "title":"",
                "description":"",
                "body":text,
                "image":""

            }




        duplicate_text = (

            source.get(
                "title",
                ""
            )

            +

            "\n"

            +

            source.get(
                "body",
                ""
            )

        )



        if duplicate(
            duplicate_text,
            source.get(
                "title",
                ""
            )
        ):


            await message.answer(
                "⚠️ این خبر قبلاً پردازش شده است."
            )

            return




        # استخراج اطلاعات


        if status:

            await status.edit_text(
                "🧠 در حال استخراج اطلاعات مهم..."
            )



        facts = await extract_facts(
            source
        )



        # تولید خبر


        generated = await generate_news(
            source,
            facts
        )



        # اعتبارسنجی


        if not validate_generated_output(
            generated
        ):


            generated = await generate_news(

                source,

                facts,

                retry_instruction="""

خروجی قبلی اشتباه بود.

فقط:
یک تیتر فارسی
+
یک پاراگراف شامل ۷ جمله خبری

تولید کن.

هیچ توضیح اضافه ننویس.

"""

            )




        if not validate_generated_output(
            generated
        ):


            raise RuntimeError(
                "خروجی AI ساختار صحیح ندارد."
            )




        post = format_post(
            generated
        )



        if not post:


            raise RuntimeError(
                "فرمت خبر قابل تولید نیست."
            )




        # تصویر


        image_path = await find_best_image(
            source
        )



        # ذخیره حافظه


        memory.append({

            "hash":
            text_hash(
                duplicate_text
            ),

            "title":
            source.get(
                "title",
                ""
            ),

            "source":
            duplicate_text[:20000],

            "post":
            post

        })



        memory[:] = memory[
            -MAX_MEMORY:
        ]



        save_memory()




        await prepare_preview(

            user_id,

            post,

            image_path

        )




        if status:

            try:

                await status.delete()

            except:

                pass




        await send_preview(

            message,

            post,

            image_path

        )




    except Exception as error:


        log.exception(
            "News processing error"
        )



        if status:

            try:

                await status.delete()

            except:

                pass



        await message.answer(

            "❌ خطا هنگام پردازش خبر:\n\n"

            +

            str(error)[:1500]

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
            "❌ خبری آماده انتشار نیست."
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



        if image and Path(image).exists():


            if len(text) <= 1024:


                await message.bot.send_photo(

                    CHANNEL_ID,

                    FSInputFile(
                        image
                    ),

                    caption=text,

                    parse_mode=ParseMode.HTML

                )


            else:


                await message.bot.send_photo(

                    CHANNEL_ID,

                    FSInputFile(
                        image
                    ),

                    caption=text[:1000],

                    parse_mode=ParseMode.HTML

                )



        else:


            await message.bot.send_message(

                CHANNEL_ID,

                text,

                parse_mode=ParseMode.HTML

            )




        await message.answer(
            "✅ خبر منتشر شد."
        )



        prepared.pop(
            user_id,
            None
        )



    except Exception as error:


        await message.answer(

            "❌ خطا هنگام انتشار:\n"

            +

            str(error)

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

        return



    await message.answer(

        "✨ پنل مدیریت Gamefa",

        reply_markup=main_keyboard()

    )





# ============================================================
# CALLBACK PUBLISH
# ============================================================


@router.callback_query(
    F.data=="publish_current"
)

async def publish_callback(
    callback
):


    if not is_admin_id(
        callback.from_user.id
    ):

        return



    await publish_news(

        callback.message,

        callback.from_user.id

    )





# ============================================================
# MAIN KEYBOARD
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
                    text="⚙️ تنظیمات"
                )

            ]

        ],

        resize_keyboard=True

    )





# ============================================================
# MESSAGE HANDLER
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



    text = message.text.strip()



    menu = [

        "🔎 بررسی خبر جدید",

        "📁 آرشیو",

        "📊 آمار",

        "⚙️ تنظیمات"

    ]



    if text in menu:

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
            "BOT_TOKEN تنظیم نشده"
        )


    if not OPENAI_API_KEY:

        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده"
        )



    load_memory()



    bot = Bot(
        token=BOT_TOKEN
    )


    dp = Dispatcher()



    dp.include_router(
        router
    )



    log.info(
        "Gamefa Bot Started"
    )



    await dp.start_polling(
        bot
    )




if __name__ == "__main__":

    asyncio.run(
        main()
    )
