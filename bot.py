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

try:
ADMIN_ID = int(
os.getenv("ADMIN_ID", "0")
)
except Exception:
ADMIN_ID = 0

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

```
global memory

try:

    if not MEMORY_FILE.exists():
        memory = []
        return

    memory = json.loads(
        MEMORY_FILE.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(memory, list):
        memory = []

except Exception as error:

    log.warning(
        "Memory load error: %s",
        error
    )

    memory = []
```

def save_memory():

```
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
```

# ============================================================

# TEXT NORMALIZATION

# ============================================================

def norm(text):

```
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
```

def similarity(a, b):

```
a = set(
    norm(a).split()
)

b = set(
    norm(b).split()
)

if not a or not b:
    return 0

return len(
    a & b
) / len(
    a | b
)
```

def duplicate(text):

```
for item in memory:

    if similarity(
        text,
        item.get(
            "source",
            ""
        )
    ) >= 0.82:

        return True

return False
```

# ============================================================

# ADMIN

# ============================================================

def is_admin(message):

```
return bool(
    ADMIN_ID
    and message.from_user
    and message.from_user.id == ADMIN_ID
)
```

# ============================================================

# URL

# ============================================================

def extract_urls(text):

```
if not text:
    return []

matches = re.findall(
    r"https?://[^\s<>()]+",
    text
)

result = []

for url in matches:

    url = url.rstrip(
        ".,)]}"
    )

    if url not in result:
        result.append(url)

return result
```

def extract_url(text):

```
urls = extract_urls(text)

if urls:
    return urls[0]

return None
```

# ============================================================

# HTML

# ============================================================

def escape_html(text):

```
return html.escape(
    text or "",
    quote=False
)
```

# ============================================================

# PERSIAN DETECTION

# ============================================================

PERSIAN_RE = re.compile(
r"[\u0600-\u06FF]"
)

LATIN_RE = re.compile(
r"[A-Za-z]"
)

def starts_with_persian(text):

```
if not text:
    return False

clean = text.strip()

# حذف ایموجی‌های ابتدایی
clean = re.sub(
    r"^[🎮🎬📱🟣🔵🟢🟡🔴⚪⚫\s]+",
    "",
    clean
)

if not clean:
    return False

# اولین کاراکتر دارای معنی
for char in clean:

    if char.isalnum():

        return bool(
            PERSIAN_RE.search(char)
        )

return False
```

# ============================================================

# FIX PERSIAN START

# ============================================================

def force_persian_start(text):

```
"""
بررسی نهایی برای جلوگیری از شروع جمله با انگلیسی.

نکته:
این تابع قرار نیست نام انگلیسی را خودش ترجمه کند.
ترجمه و بازنویسی توسط AI انجام می‌شود.

اگر AI باز هم جمله‌ای را با انگلیسی شروع کند،
یک عبارت فارسی طبیعی اضافه می‌شود تا حداقل
ساختار نهایی فارسی باشد.
"""

if not text:
    return text

text = text.strip()

if starts_with_persian(text):
    return text

return (
    "این خبر درباره "
    + text
)
```

# ============================================================

# CLEAN AI OUTPUT

# ============================================================

def clean_ai_output(text):

````
text = text or ""

# حذف code fence
text = re.sub(
    r"```(?:text|markdown|plain)?",
    "",
    text,
    flags=re.I
)

text = text.replace(
    "```",
    ""
)

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

text = re.sub(
    r"\*(.*?)\*",
    r"\1",
    text,
    flags=re.S
)

# حذف امضای ربات
text = re.sub(
    r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
    "",
    text
)

return text.strip()
````

# ============================================================

# FORMAT SINGLE NEWS

# ============================================================

def format_single_news(
title,
body
):

```
title = clean_ai_output(
    title
)

body = clean_ai_output(
    body
)

# --------------------------------------------------------
# TITLE
# --------------------------------------------------------

title = re.sub(
    r"^[🎮🎬📱]\s*",
    "",
    title
).strip()

# --------------------------------------------------------
# تعیین دسته
# --------------------------------------------------------

full_text = (
    title
    + " "
    + body
).lower()

game_words = [
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
    "assassin",
    "far cry",
    "minecraft",
    "fortnite",
    "call of duty",
    "battlefield",
    "final fantasy",
    "devil may cry"
]

movie_words = [
    "فیلم",
    "سریال",
    "بازیگر",
    "کارگردان",
    "movie",
    "film",
    "series",
    "season",
    "actor",
    "actress",
    "director",
    "netflix",
    "hbo",
    "disney",
    "amazon prime",
    "squid game"
]

if any(
    word in full_text
    for word in game_words
):

    category = "🎮"

elif any(
    word in full_text
    for word in movie_words
):

    category = "🎬"

else:

    category = "📱"

# --------------------------------------------------------
# TITLE
# --------------------------------------------------------

if not starts_with_persian(title):

    title = force_persian_start(
        title
    )

final_title = (
    category
    + " "
    + title
)

# --------------------------------------------------------
# BODY
# --------------------------------------------------------

# تبدیل خطوط به یک بند
body = re.sub(
    r"\s*\n+\s*",
    " ",
    body
)

body = re.sub(
    r"\s+",
    " ",
    body
).strip()

if body:

    # حذف 🟣 احتمالی AI
    body = re.sub(
        r"^\s*🟣\s*",
        "",
        body
    ).strip()

    # اطمینان نهایی
    if not starts_with_persian(body):

        body = force_persian_start(
            body
        )

    final_body = (
        "🟣 "
        + body
    )

else:

    final_body = ""

# --------------------------------------------------------
# FINAL
# --------------------------------------------------------

result = (
    "<b>"
    + escape_html(final_title)
    + "</b>"
)

if final_body:

    result += (
        "\n\n"
        + escape_html(
            final_body
        )
    )

result += (
    "\n\n"
    "<b>🆔 @Gamefa_official</b>"
)

return result
```

# ============================================================

# FORMAT MULTIPLE NEWS

# ============================================================

def format_post(ai_text):

```
ai_text = clean_ai_output(
    ai_text
)

if not ai_text:
    return ""

# --------------------------------------------------------
# حالت استاندارد AI:
#
# [TITLE]
# body
#
# [TITLE]
# body
#
# --------------------------------------------------------

blocks = re.split(
    r"\n\s*\n\s*\n+",
    ai_text
)

blocks = [
    block.strip()
    for block in blocks
    if block.strip()
]

# اگر AI بین خبرها فقط دو خط خالی نگذاشته باشد،
# تلاش می‌کنیم از تیترهای دسته‌بندی‌شده جدا کنیم.

if len(blocks) == 1:

    text = blocks[0]

    # تشخیص تیترهای جدید
    matches = list(
        re.finditer(
            r"(?m)^(?:🎮|🎬|📱)\s*",
            text
        )
    )

    if len(matches) > 1:

        blocks = []

        for index, match in enumerate(matches):

            start = match.start()

            if index + 1 < len(matches):

                end = matches[
                    index + 1
                ].start()

            else:

                end = len(text)

            block = text[
                start:end
            ].strip()

            if block:
                blocks.append(block)

# --------------------------------------------------------
# اگر هنوز فقط یک بلوک است، یک خبر است.
# --------------------------------------------------------

results = []

for block in blocks:

    lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip()
    ]

    if not lines:
        continue

    title = lines[0]

    body = " ".join(
        lines[1:]
    )

    # حذف ایموجی 🟣 از ابتدای بدن
    body = re.sub(
        r"^\s*🟣\s*",
        "",
        body
    ).strip()

    result = format_single_news(
        title,
        body
    )

    if result:
        results.append(
            result
        )

if not results:
    return ""

return "\n\n".join(
    results
)
```

# ============================================================

# GAMEFA ARTICLE FETCH

# ============================================================

async def fetch_gamefa(url):

```
parsed = urlparse(
    url
)

if (
    "gamefa.com"
    not in parsed.netloc.lower()
):

    raise ValueError(
        "فقط لینک‌های Gamefa پشتیبانی می‌شوند."
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

# --------------------------------------------------------
# REMOVE UNWANTED
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
        "aside"
    ]
):

    element.decompose()

# --------------------------------------------------------
# TITLE
# --------------------------------------------------------

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

# --------------------------------------------------------
# DESCRIPTION
# --------------------------------------------------------

description = ""

meta_options = [
    {
        "name": "description"
    },
    {
        "property": "og:description"
    },
    {
        "name": "twitter:description"
    }
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
# ORIGINAL IMAGE ONLY
# --------------------------------------------------------

image = ""

image_options = [
    {
        "property": "og:image"
    },
    {
        "name": "twitter:image"
    },
    {
        "property": "og:image:url"
    }
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

        candidate = (
            meta["content"]
            .strip()
        )

        if candidate:

            image = urljoin(
                final_url,
                candidate
            )

            break

# --------------------------------------------------------
# ARTICLE
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
```

# ============================================================

# AI PROMPT

# ============================================================

PROMPT = """
تو ویراستار حرفه‌ای اخبار کانال Gamefa هستی.

وظیفه تو تبدیل اطلاعات ورودی به یک یا چند خبر فارسی آماده انتشار است.

قوانین بسیار مهم:

1. اگر ورودی شامل چند خبر است، هر خبر را جداگانه نگه دار.

2. هر خبر باید دقیقاً یک تیتر و یک بند متن داشته باشد.

3. تیتر هر خبر باید با متن فارسی شروع شود.

4. بند متن هر خبر باید با متن فارسی شروع شود.

5. هیچ جمله‌ای نباید با حروف انگلیسی شروع شود.

6. بسیار مهم:
   اگر نام شخص در ابتدای جمله انگلیسی بود، آن را به فارسی بنویس.

مثال:
Brad Pitt در گفت‌وگویی...
اشتباه است.

درست:
برد پیت در گفت‌وگویی...

مثال:
Hideki Kamiya گفت...
اشتباه است.

درست:
هیدکی کامییا گفت...

مثال:
David Fincher کارگردانی...
اشتباه است.

درست:
دیوید فینچر کارگردانی...

7. اگر نام شرکت در ابتدای جمله انگلیسی بود، جمله را طبیعی بازنویسی کن.

مثال:
Square Enix اعلام کرد...
اشتباه است.

درست:
شرکت Square Enix اعلام کرد...

8. اگر نام بازی در ابتدای جمله انگلیسی بود، یک عبارت فارسی قبل از آن قرار بده.

مثال:
Final Fantasy 7 Revelation احتمالاً...
اشتباه است.

درست:
بازی Final Fantasy 7 Revelation احتمالاً...

9. اگر نام فیلم در ابتدای جمله انگلیسی بود:

مثال:
Squid Game قرار است...
اشتباه است.

درست:
سریال Squid Game قرار است...

10. اگر نام سریال در ابتدای جمله انگلیسی بود، از عبارتی مانند «سریال»، «فصل جدید سریال» یا عبارت طبیعی مشابه استفاده کن.

11. فقط اضافه کردن «براساس گزارش‌ها» قبل از نام انگلیسی کافی نیست.

12. اگر Brad Pitt در ابتدای جمله است، نباید خروجی این باشد:

براساس گزارش‌ها، Brad Pitt...

بلکه باید باشد:

برد پیت...

13. نام‌های انگلیسی داخل جمله می‌توانند انگلیسی باقی بمانند.

مثال:
برد پیت درباره استفاده از AI در Hollywood صحبت کرد.

14. نام افراد مشهور را تا حد امکان با نگارش فارسی رایج بنویس.

15. نام شرکت‌ها و استودیوها را در صورتی که وسط جمله هستند، می‌توانی انگلیسی نگه داری.

16. متن خبر فارسی و روان باشد.

17. هیچ اطلاعات جدیدی اضافه نکن.

18. اطلاعات مهم ورودی را حذف نکن.

19. خبر را بیش از حد کوتاه نکن.

20. Markdown تولید نکن.

21. HTML تولید نکن.

22. لینک تولید نکن.

23. منبع تولید نکن.

24. @Gamefa_official تولید نکن.

25. ایموجی 🟣 تولید نکن.

26. برای خبر بازی، ابتدای تیتر از 🎮 استفاده کن.

27. برای خبر فیلم و سریال، ابتدای تیتر از 🎬 استفاده کن.

28. برای فناوری، هوش مصنوعی، موبایل و سخت‌افزار، ابتدای تیتر از 📱 استفاده کن.

29. بعد از ایموجی دسته‌بندی، اولین کلمه واقعی تیتر حتماً فارسی باشد.

30. متن هر خبر فقط یک بند باشد.

31. بین چند خبر یک خط خالی قرار بده.

32. خروجی فقط خبرهای آماده انتشار باشد.

33. قبل یا بعد از خروجی توضیح نده.

نمونه صحیح:

🎬 برد پیت درباره تأثیر هوش مصنوعی بر سینما صحبت کرد

برد پیت در گفت‌وگویی تازه با مجله Esquire درباره استفاده درست از هوش مصنوعی صحبت کرده و معتقد است این فناوری می‌تواند به ساخت فیلم‌های بیشتری با بودجه متوسط کمک کند.

نمونه صحیح دوم:

🎮 احتمال عرضه زودهنگام Final Fantasy 7 Revelation افزایش یافت

شرکت Square Enix در گزارش مالی جدید خود تغییری در پیش‌بینی درآمدهایش ایجاد نکرده و همین موضوع باعث شده گمانه‌زنی‌ها درباره زمان عرضه Final Fantasy 7 Revelation افزایش پیدا کند.

نمونه صحیح سوم:

🎮 هیدکی کامییا به بازگشت Devil May Cry اشاره کرد

هیدکی کامییا، کارگردان و خالق نخستین نسخه Devil May Cry، با پاسخی کوتاه در شبکه اجتماعی X بار دیگر توجه طرفداران این مجموعه را به خود جلب کرده است.
"""

# ============================================================

# AI GENERATION

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
    max_output_tokens=2500
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
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151 Safari/537.36"
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

                log.warning(
                    "Image status: %s",
                    response.status
                )

                return None

            content_type = (
                response.headers.get(
                    "Content-Type",
                    ""
                ).lower()
            )

            if "image" not in content_type:

                log.warning(
                    "Not an image: %s",
                    content_type
                )

                return None

            data = await response.read()

    # ----------------------------------------------------
    # SIZE
    # ----------------------------------------------------

    if not (
        1000
        < len(data)
        <= 15 * 1024 * 1024
    ):

        return None

    # ----------------------------------------------------
    # EXTENSION
    # ----------------------------------------------------

    if (
        "jpeg" in content_type
        or "jpg" in content_type
    ):

        extension = ".jpg"

    elif "webp" in content_type:

        extension = ".webp"

    elif "png" in content_type:

        extension = ".png"

    else:

        extension = ".jpg"

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

# PROCESS NEWS

# ============================================================

async def process_news(
message,
text
):

```
urls = extract_urls(
    text
)

source = text

source_image = ""

article_title = ""

article_body = ""

# ========================================================
# LINK
# ========================================================

if urls:

    url = urls[0]

    parsed = urlparse(
        url
    )

    if (
        "gamefa.com"
        not in parsed.netloc.lower()
    ):

        await message.answer(
            "❌ فعلاً فقط لینک‌های Gamefa پشتیبانی می‌شوند."
        )

        return

    await message.answer(
        "⏳ در حال دریافت خبر از Gamefa..."
    )

    article = await fetch_gamefa(
        url
    )

    article_title = article.get(
        "title",
        ""
    )

    article_body = article.get(
        "body",
        ""
    )

    source_image = article.get(
        "image",
        ""
    )

    source = (
        "URL:\n"
        + article.get(
            "url",
            ""
        )
        + "\n\n"
        + "TITLE:\n"
        + article_title
        + "\n\n"
        + "DESCRIPTION:\n"
        + article.get(
            "description",
            ""
        )
        + "\n\n"
        + "ARTICLE:\n"
        + article_body
    )

else:

    # متن مستقیم
    # هیچ تصویری ندارد.

    source_image = ""

# ========================================================
# DUPLICATE
# ========================================================

if duplicate(source):

    await message.answer(
        "⚠️ این خبر یا یک خبر بسیار مشابه "
        "قبلاً دریافت شده است."
    )

    return

# ========================================================
# AI
# ========================================================

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

# ========================================================
# IMAGE
# ========================================================

image_path = None

if source_image:

    await message.answer(
        "🖼 تصویر اصلی خبر پیدا شد؛ در حال دریافت..."
    )

    image_path = await download_image(
        source_image
    )

    if image_path:

        log.info(
            "Using original Gamefa image."
        )

    else:

        log.info(
            "Original image failed. "
            "Switching to text-only."
        )

else:

    log.info(
        "No source image. "
        "No image search will be performed."
    )

# ========================================================
# MEMORY
# ========================================================

memory.append(
    {
        "source": source[:16000],
        "post": post,
        "url": (
            urls[0]
            if urls
            else ""
        )
    }
)

save_memory()

# ========================================================
# PREPARE
# ========================================================

prepared[
    message.from_user.id
] = {
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
            "Preview photo failed: %s",
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

# ========================================================
# READY
# ========================================================

await message.answer(
    "✅ خبر آماده انتشار است.\n\n"
    "برای ارسال به کانال:\n"
    "/publish"
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
async def start(
message: Message
):

```
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
```

# ============================================================

# STATS

# ============================================================

@router.message(
Command("stats")
)
async def stats(
message: Message
):

```
if not is_admin(message):
    return

await message.answer(
    "📊 تعداد خبرهای ذخیره‌شده: "
    + str(len(memory))
)
```

# ============================================================

# CLEAR

# ============================================================

@router.message(
Command("clear")
)
async def clear(
message: Message
):

```
if not is_admin(message):
    return

memory.clear()

save_memory()

await message.answer(
    "✅ حافظه ربات پاک شد."
)
```

# ============================================================

# PUBLISH

# ============================================================

@router.message(
Command("publish")
)
async def publish(
message: Message
):

```
if not is_admin(message):
    return

item = prepared.get(
    message.from_user.id
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
                "Channel photo failed: %s",
                error
            )

            # اگر عکس ارسال نشد،
            # متن به تنهایی ارسال می‌شود.

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

    # ====================================================
    # CLEAN
    # ====================================================

    prepared.pop(
        message.from_user.id,
        None
    )

    await message.answer(
        "✅ خبر با موفقیت در کانال منتشر شد."
    )

except Exception as error:

    log.exception(
        "Publish error"
    )

    await message.answer(
        "❌ خطا هنگام انتشار:\n"
        + str(error)[:1500]
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

try:

    text = (
        message.text
        or ""
    ).strip()

    if not text:

        await message.answer(
            "❌ متن خبر خالی است."
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
```

# ============================================================

# RUN

# ============================================================

if **name** == "**main**":

```
asyncio.run(
    main()
)
