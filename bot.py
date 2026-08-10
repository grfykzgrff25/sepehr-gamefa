import os
import re
import json
import html
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
Message,
FSInputFile,
InlineKeyboardMarkup,
InlineKeyboardButton,
CallbackQuery
)
from aiogram.client.default import DefaultBotProperties

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
os.getenv("ADMIN_ID", "0").strip()
)
except (ValueError, TypeError):
ADMIN_ID = 0

MEMORY_FILE = Path("news_memory.json")

MAX_MEMORY = 1500

memory = []

prepared = {}

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

```
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
```

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

```
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
```

def similarity(a, b):
a_words = set(
norm(a).split()
)

```
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
```

def duplicate(text):
for item in memory:

```
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
```

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
match = re.search(
r"https?://[^\s<>()]+",
text or ""
)

```
if not match:
    return None

return match.group(
    0
).rstrip(
    ".,)]}"
)
```

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

def starts_with_persian(text):
if not text:
return False

```
clean = text.strip()

clean = re.sub(
    r"^[🎮🎬📱🟣\s]+",
    "",
    clean
)

if not clean:
    return False

first = clean[0]

return bool(
    PERSIAN_RE.search(first)
)
```

def make_persian_start(
text,
is_title=False
):
if not text:
return ""

```
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
```

# ============================================================

# FORCE 7 LINES

# ============================================================

def split_into_seven_lines(text):
"""
متن خبر را در یک پاراگراف نگه می‌دارد،
اما آن را برای نمایش در تلگرام به ۷ خط تقسیم می‌کند.
"""

```
text = re.sub(
    r"\s+",
    " ",
    text or ""
).strip()

if not text:
    return []

words = text.split()

if len(words) < 7:
    return [text]

total_words = len(words)

target = max(
    8,
    total_words // 7
)

lines = []

current = []

for word in words:

    current.append(word)

    if (
        len(current) >= target
        and len(lines) < 6
    ):
        lines.append(
            " ".join(current)
        )
        current = []

if current:
    lines.append(
        " ".join(current)
    )

while len(lines) > 7:

    lines[-2] += " " + lines[-1]

    lines.pop()

return lines
```

# ============================================================

# FORMAT POST

# ============================================================

def format_post(ai_text):

```
ai_text = ai_text or ""

# حذف Markdown
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
    r"\*(.*?)\*",
    r"\1",
    ai_text,
    flags=re.S
)

# حذف امضای احتمالی
ai_text = re.sub(
    r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
    "",
    ai_text
)

# حذف خطوط خالی
lines = [
    x.strip()
    for x in ai_text.splitlines()
    if x.strip()
]

if not lines:
    return ""

# ========================================================
# TITLE
# ========================================================

title = lines[0]

title = re.sub(
    r"^[🎮🎬📱]\s*",
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

body = " ".join(
    lines[1:]
).strip()

# حذف ایموجی‌های اضافی ابتدای متن
body = re.sub(
    r"^\s*🟣\s*",
    "",
    body
).strip()

# اطمینان از فارسی بودن شروع بند
body = make_persian_start(
    body,
    is_title=False
)

# ========================================================
# CATEGORY
# ========================================================

full_text = (
    title
    + " "
    + body
).lower()

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
    "final fantasy",
    "devil may cry"
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
    "cinema",
    "سینما"
]

if any(
    word in full_text
    for word in gaming_words
):
    category = "🎮"

elif any(
    word in full_text
    for word in movie_words
):
    category = "🎬"

else:
    category = "📱"

# ========================================================
# 7 LINES
# ========================================================

seven_lines = split_into_seven_lines(
    body
)

formatted_body = []

for line in seven_lines:

    line = make_persian_start(
        line,
        is_title=False
    )

    formatted_body.append(
        "🟣 " + line
    )

# ========================================================
# FINAL
# ========================================================

final_title = (
    category
    + " "
    + title
)

result = (
    "<b>"
    + escape_html(final_title)
    + "</b>"
)

if formatted_body:

    result += (
        "\n\n"
        + "\n".join(
            escape_html(
                line
            )
            for line in formatted_body
        )
    )

result += (
    "\n\n"
    "<b>🆔 @Gamefa_official</b>"
)

return result
```

# ============================================================

# GAMEFA ARTICLE FETCH

# ============================================================

async def fetch_gamefa(url):

```
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

# ========================================================
# REMOVE
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

        image = (
            meta["content"]
            .strip()
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
```

# ============================================================

# AI PROMPT

# ============================================================

PROMPT = """
تو ویراستار حرفه‌ای اخبار کانال Gamefa هستی.

از اطلاعات داده‌شده یک پست خبری فارسی آماده انتشار بساز.

قوانین بسیار مهم:

1. خروجی فقط شامل تیتر و متن خبر باشد.

2. خط اول فقط تیتر باشد.

3. تیتر حتماً با فارسی شروع شود.

4. هیچ‌وقت تیتر را با نام انگلیسی شروع نکن.

مثال غلط:
Netflix نسخه آمریکایی Squid Game را لغو کرد

مثال درست:
نتفلیکس نسخه آمریکایی Squid Game را لغو کرد

5. متن خبر باید کاملاً فارسی و روان باشد.

6. متن خبر فقط یک پاراگراف باشد.

7. متن خبر باید نسبتاً طولانی و کامل باشد.

8. متن خبر باید برای نمایش در تلگرام تقریباً 7 خط مناسب باشد.

9. هیچ جمله‌ای نباید با نام انگلیسی شروع شود.

مثال غلط:
Brad Pitt در مصاحبه جدید...

مثال درست:
برد پیت در مصاحبه جدید...

10. اگر نام شخص یا شرکت انگلیسی است، در صورت نیاز نام اصلی را داخل جمله حفظ کن.

11. اگر نام بازی یا فیلم انگلیسی است، نام اصلی را داخل جمله حفظ کن.

12. نام انگلیسی نباید اولین کلمه یک جمله باشد.

13. اطلاعات ساختگی اضافه نکن.

14. اطلاعات خبر را تغییر نده.

15. Markdown استفاده نکن.

16. HTML استفاده نکن.

17. لینک ایجاد نکن.

18. منبع ایجاد نکن.

19. @Gamefa_official ایجاد نکن.

20. ایموجی 🟣 ایجاد نکن.

21. اگر خبر مربوط به بازی است، تیتر با 🎮 شروع شود.

22. اگر خبر مربوط به فیلم یا سریال است، تیتر با 🎬 شروع شود.

23. اگر خبر مربوط به فناوری، هوش مصنوعی، موبایل یا سخت‌افزار است، تیتر با 📱 شروع شود.

24. بعد از ایموجی دسته‌بندی، اولین کلمه واقعی تیتر باید فارسی باشد.

25. ابتدای متن خبر نیز حتماً باید فارسی باشد.

26. اگر متن منبع با انگلیسی شروع شده، جمله را به شکل طبیعی بازنویسی کن تا فارسی شروع شود.

27. فقط تیتر و یک پاراگراف متن خبر را تولید کن.
    """

# ============================================================

# AI GENERATE

# ============================================================

async def generate_news(source):

```
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
```

# ============================================================

# IMAGE DOWNLOAD

# ============================================================

async def download_image(url):

```
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
```

# ============================================================

# PROCESS SOURCE IMAGE

# ============================================================

async def find_source_image(
source_image
):

```
if not source_image:
    return None

return await download_image(
    source_image
)
```

# ============================================================

# MAIN PROCESS

# ============================================================

async def process_news(
message,
text
):

```
url = extract_url(
    text
)

source_image = ""

article_title = ""

article_body = ""

source = text

# ========================================================
# URL
# ========================================================

if url:

    status_message = await message.answer(
        "⏳ <b>در حال دریافت خبر...</b>",
        parse_mode=ParseMode.HTML
    )

    try:

        article = await fetch_gamefa(
            url
        )

    except Exception as error:

        await status_message.edit_text(
            "❌ دریافت مقاله ناموفق بود.\n\n"
            + escape_html(
                str(error)[:1000]
            ),
            parse_mode=ParseMode.HTML
        )

        return

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

    await status_message.edit_text(
        "✍️ <b>در حال آماده‌سازی خبر...</b>",
        parse_mode=ParseMode.HTML
    )

else:

    await message.answer(
        "✍️ <b>در حال آماده‌سازی خبر...</b>",
        parse_mode=ParseMode.HTML
    )

# ========================================================
# DUPLICATE
# ========================================================

if duplicate(source):

    await message.answer(
        "⚠️ <b>خبر تکراری است.</b>\n\n"
        "این خبر یا خبر بسیار مشابه آن قبلاً دریافت شده.",
        parse_mode=ParseMode.HTML
    )

    return

# ========================================================
# AI
# ========================================================

try:

    generated = await generate_news(
        source
    )

except Exception as error:

    log.exception(
        "AI generation error"
    )

    await message.answer(
        "❌ خطا در ساخت متن خبر:\n"
        + escape_html(
            str(error)[:1200]
        ),
        parse_mode=ParseMode.HTML
    )

    return

post = format_post(
    generated
)

if not post:

    await message.answer(
        "❌ متن تولیدشده خالی است."
    )

    return

# ========================================================
# IMAGE
# ========================================================

await message.answer(
    "🖼 <b>در حال بررسی تصویر اصلی خبر...</b>",
    parse_mode=ParseMode.HTML
)

image_path = await find_source_image(
    source_image
)

# ========================================================
# MEMORY
# ========================================================

memory.append(
    {
        "source": source[:16000],
        "post": post,
        "url": url or ""
    }
)

save_memory()

# ========================================================
# PREPARE
# ========================================================

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

# ========================================================
# PREVIEW
# ========================================================

keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 انتشار در کانال",
                callback_data="publish_news"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔄 بازسازی متن",
                callback_data="regenerate_news"
            ),
            InlineKeyboardButton(
                text="❌ لغو",
                callback_data="cancel_news"
            )
        ]
    ]
)

if image_path:

    try:

        await message.answer_photo(
            FSInputFile(
                image_path
            ),
            caption=post,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

    except Exception as error:

        log.warning(
            "Photo preview failed: %s",
            error
        )

        await message.answer(
            post,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

else:

    await message.answer(
        post,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
```

# ============================================================

# MAIN MENU

# ============================================================

def main_menu():

```
return InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📝 ساخت خبر",
                callback_data="menu_create"
            ),
            InlineKeyboardButton(
                text="🚀 خبر آماده",
                callback_data="menu_ready"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 آمار",
                callback_data="menu_stats"
            ),
            InlineKeyboardButton(
                text="ℹ️ راهنما",
                callback_data="menu_help"
            )
        ],
        [
            InlineKeyboardButton(
                text="🧹 پاک‌سازی حافظه",
                callback_data="menu_clear"
            )
        ]
    ]
)
```

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

```
if not is_admin(message):

    await message.answer(
        "⛔ این ربات خصوصی است."
    )

    return

await message.answer(
    "🎮 <b>Gamefa News Bot</b>\n\n"
    "ربات آماده دریافت خبر است.\n\n"
    "از منوی زیر یکی از گزینه‌ها را انتخاب کن:",
    parse_mode=ParseMode.HTML,
    reply_markup=main_menu()
)
```

# ============================================================

# MENU BUTTONS

# ============================================================

@router.callback_query(
F.data == "menu_create"
)
async def menu_create(
callback: CallbackQuery
):

```
await callback.answer()

await callback.message.answer(
    "📝 <b>ساخت خبر</b>\n\n"
    "متن خبر یا لینک مقاله Gamefa را ارسال کن.\n\n"
    "اگر مقاله تصویر اصلی داشته باشد، همان تصویر استفاده می‌شود.\n"
    "اگر تصویر نداشته باشد، هیچ تصویر تصادفی جست‌وجو نمی‌شود.",
    parse_mode=ParseMode.HTML
)
```

@router.callback_query(
F.data == "menu_ready"
)
async def menu_ready(
callback: CallbackQuery
):

```
await callback.answer()

user_id = (
    callback.from_user.id
)

item = prepared.get(
    user_id
)

if not item:

    await callback.message.answer(
        "❌ هنوز خبری آماده انتشار نیست."
    )

    return

await callback.message.answer(
    "🚀 <b>یک خبر آماده انتشار داری.</b>\n\n"
    "از دکمه‌های زیر استفاده کن.",
    parse_mode=ParseMode.HTML,
    reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 انتشار",
                    callback_data="publish_news"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ لغو",
                    callback_data="cancel_news"
                )
            ]
        ]
    )
)
```

@router.callback_query(
F.data == "menu_stats"
)
async def menu_stats(
callback: CallbackQuery
):

```
await callback.answer()

await callback.message.answer(
    "📊 <b>آمار ربات</b>\n\n"
    "📰 تعداد خبرهای ذخیره‌شده: "
    + str(len(memory)),
    parse_mode=ParseMode.HTML
)
```

@router.callback_query(
F.data == "menu_help"
)
async def menu_help(
callback: CallbackQuery
):

```
await callback.answer()

await callback.message.answer(
    "ℹ️ <b>راهنمای Gamefa News Bot</b>\n\n"
    "1️⃣ لینک Gamefa را بفرست.\n\n"
    "2️⃣ ربات متن خبر را آماده می‌کند.\n\n"
    "3️⃣ اگر مقاله تصویر اصلی داشته باشد، همان تصویر استفاده می‌شود.\n\n"
    "4️⃣ اگر تصویر نداشته باشد، خبر بدون تصویر آماده می‌شود.\n\n"
    "5️⃣ قبل از انتشار می‌توانی پیش‌نمایش را ببینی.\n\n"
    "6️⃣ با دکمه «انتشار» خبر به کانال ارسال می‌شود.",
    parse_mode=ParseMode.HTML
)
```

@router.callback_query(
F.data == "menu_clear"
)
async def menu_clear(
callback: CallbackQuery
):

```
await callback.answer()

if not is_admin(
    callback.message
):
    return

keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ بله، پاک کن",
                callback_data="confirm_clear"
            ),
            InlineKeyboardButton(
                text="❌ لغو",
                callback_data="cancel_clear"
            )
        ]
    ]
)

await callback.message.answer(
    "⚠️ <b>پاک‌سازی حافظه</b>\n\n"
    "آیا مطمئنی می‌خواهی حافظه اخبار پاک شود؟",
    parse_mode=ParseMode.HTML,
    reply_markup=keyboard
)
```

# ============================================================

# CLEAR CONFIRM

# ============================================================

@router.callback_query(
F.data == "confirm_clear"
)
async def confirm_clear(
callback: CallbackQuery
):

```
await callback.answer()

if not is_admin(
    callback.message
):
    return

memory.clear()

save_memory()

await callback.message.answer(
    "✅ <b>حافظه با موفقیت پاک شد.</b>",
    parse_mode=ParseMode.HTML
)
```

@router.callback_query(
F.data == "cancel_clear"
)
async def cancel_clear(
callback: CallbackQuery
):

```
await callback.answer(
    "لغو شد."
)

await callback.message.answer(
    "❌ پاک‌سازی لغو شد."
)
```

# ============================================================

# PUBLISH

# ============================================================

async def publish_for_user(
bot,
user_id
):

```
item = prepared.get(
    user_id
)

if not item:
    return False, "خبر آماده‌ای وجود ندارد."

image = item.get(
    "image",
    ""
)

text = item.get(
    "text",
    ""
)

if not text:
    return False, "متن خبر خالی است."

try:

    if (
        image
        and Path(image).exists()
    ):

        try:

            await bot.send_photo(
                CHANNEL_ID,
                FSInputFile(
                    image
                ),
                caption=text,
                parse_mode=ParseMode.HTML
            )

        except Exception as photo_error:

            log.warning(
                "Photo publish failed: %s",
                photo_error
            )

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

    return True, "خبر منتشر شد."

except Exception as error:

    log.exception(
        "Publish error"
    )

    return False, str(error)
```

# ============================================================

# PUBLISH CALLBACK

# ============================================================

@router.callback_query(
F.data == "publish_news"
)
async def publish_callback(
callback: CallbackQuery
):

```
await callback.answer(
    "در حال انتشار..."
)

if not is_admin(
    callback.message
):
    return

user_id = (
    callback.from_user.id
)

success, result = await publish_for_user(
    callback.bot,
    user_id
)

if success:

    await callback.message.answer(
        "✅ <b>خبر با موفقیت منتشر شد.</b>\n\n"
        "📢 کانال: "
        + escape_html(
            CHANNEL_ID
        ),
        parse_mode=ParseMode.HTML
    )

    prepared.pop(
        user_id,
        None
    )

else:

    await callback.message.answer(
        "❌ <b>انتشار ناموفق بود.</b>\n\n"
        + escape_html(
            result[:1500]
        ),
        parse_mode=ParseMode.HTML
    )
```

# ============================================================

# CANCEL NEWS

# ============================================================

@router.callback_query(
F.data == "cancel_news"
)
async def cancel_news(
callback: CallbackQuery
):

```
await callback.answer(
    "خبر لغو شد."
)

user_id = (
    callback.from_user.id
)

prepared.pop(
    user_id,
    None
)

await callback.message.answer(
    "❌ <b>انتشار این خبر لغو شد.</b>",
    parse_mode=ParseMode.HTML
)
```

# ============================================================

# REGENERATE

# ============================================================

@router.callback_query(
F.data == "regenerate_news"
)
async def regenerate_news(
callback: CallbackQuery
):

```
await callback.answer(
    "در حال بازسازی..."
)

user_id = (
    callback.from_user.id
)

item = prepared.get(
    user_id
)

if not item:

    await callback.message.answer(
        "❌ خبری برای بازسازی وجود ندارد."
    )

    return

old_text = item.get(
    "text",
    ""
)

try:

    generated = await generate_news(
        old_text
    )

    new_post = format_post(
        generated
    )

    if not new_post:

        await callback.message.answer(
            "❌ ساخت مجدد متن ناموفق بود."
        )

        return

    item["text"] = new_post

    prepared[user_id] = item

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 انتشار در کانال",
                    callback_data="publish_news"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 بازسازی دوباره",
                    callback_data="regenerate_news"
                ),
                InlineKeyboardButton(
                    text="❌ لغو",
                    callback_data="cancel_news"
                )
            ]
        ]
    )

    await callback.message.answer(
        new_post,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

except Exception as error:

    await callback.message.answer(
        "❌ خطا:\n"
        + escape_html(
            str(error)[:1200]
        ),
        parse_mode=ParseMode.HTML
    )
```

# ============================================================

# /STATS

# ============================================================

@router.message(
Command("stats")
)
async def stats_handler(
message: Message
):

```
if not is_admin(message):
    return

await message.answer(
    "📊 <b>آمار</b>\n\n"
    "📰 خبرهای ذخیره‌شده: "
    + str(len(memory)),
    parse_mode=ParseMode.HTML,
    reply_markup=main_menu()
)
```

# ============================================================

# /CLEAR

# ============================================================

@router.message(
Command("clear")
)
async def clear_handler(
message: Message
):

```
if not is_admin(message):
    return

memory.clear()

save_memory()

await message.answer(
    "✅ حافظه ربات پاک شد.",
    reply_markup=main_menu()
)
```

# ============================================================

# /PUBLISH

# ============================================================

@router.message(
Command("publish")
)
async def publish_handler(
message: Message
):

```
if not is_admin(message):
    return

user_id = (
    message.from_user.id
)

success, result = await publish_for_user(
    message.bot,
    user_id
)

if success:

    prepared.pop(
        user_id,
        None
    )

    await message.answer(
        "✅ خبر با موفقیت در کانال منتشر شد.",
        reply_markup=main_menu()
    )

else:

    await message.answer(
        "❌ " + result,
        reply_markup=main_menu()
    )
```

# ============================================================

# TEXT MESSAGE

# ============================================================

@router.message(
F.text
)
async def text_handler(
message: Message
):

```
if not is_admin(message):
    return

text = (
    message.text
    or ""
).strip()

if not text:
    return

# دستورات
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
        "❌ <b>خطا هنگام پردازش:</b>\n\n"
        + escape_html(
            str(error)[:1500]
        ),
        parse_mode=ParseMode.HTML
    )
```

# ============================================================

# PHOTO MESSAGE

# ============================================================

@router.message(
F.photo
)
async def photo_handler(
message: Message
):

```
if not is_admin(message):
    return

caption = (
    message.caption
    or ""
).strip()

if not caption:

    await message.answer(
        "📷 عکس دریافت شد.\n\n"
        "لطفاً متن خبر را در کپشن عکس بنویس."
    )

    return

try:

    # در صورت ارسال عکس دستی،
    # همان عکس کاربر را ذخیره می‌کنیم.

    photo = message.photo[-1]

    file = await message.bot.get_file(
        photo.file_id
    )

    path = Path(
        "manual_news_image.jpg"
    )

    await message.bot.download_file(
        file.file_path,
        destination=path
    )

    await process_news(
        message,
        caption
    )

    user_id = (
        message.from_user.id
    )

    if user_id in prepared:

        prepared[user_id][
            "image"
        ] = str(path)

except Exception as error:

    log.exception(
        "Photo processing error"
    )

    await message.answer(
        "❌ خطا:\n"
        + escape_html(
            str(error)[:1200]
        ),
        parse_mode=ParseMode.HTML
    )
```

# ============================================================

# MAIN

# ============================================================

async def main():

```
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
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dispatcher = Dispatcher()

dispatcher.include_router(
    router
)

log.info(
    "Gamefa News Bot started successfully."
)

await dispatcher.start_polling(
    bot,
    allowed_updates=dispatcher.resolve_used_update_types()
)
```

# ============================================================

# RUN

# ============================================================

if **name** == "**main**":
asyncio.run(
main()
)
