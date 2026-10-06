"""
image_processor.py — картинка к посту.

Приоритет (бриф п.5А):
1. Постер площадки: og:image со страницы события (или image_url из источника).
2. Fallback: генерация через Kandinsky (FusionBrain API).
3. Последний рубеж: локальная заглушка-градиент.
Поверх ЛЮБОЙ картинки — фирменная плашка: название канала + дата (Pillow).

Пост без изображения на модерацию не уходит — get_image() всегда
возвращает файл. Лог выбора источника пишется в поле image_source.
"""

import logging
import os
import time
import uuid
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("autoposter.image")

IMAGES_DIR = Path(__file__).parent / "data" / "images"
MAX_WIDTH = 1280
MAX_BYTES = 1_000_000  # 1 МБ
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "Куда сходить в Москве")
HEADERS = {"User-Agent": "Mozilla/5.0 KudaSkitMskBot/1.0"}

FONT_CANDIDATES = [
    "arial.ttf", "Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ------------------------------------------------------------------
# Шаг 1: постер площадки
# ------------------------------------------------------------------

def parse_og_image(url: str) -> str | None:
    """Вытаскивает og:image со страницы события."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.find("meta", property="og:image")
        if tag and tag.get("content"):
            return tag["content"]
    except Exception as e:  # noqa: BLE001
        logger.warning("og:image не получен с %s: %s", url, e)
    return None


def _download_and_normalize(image_url: str, out_path: Path) -> bool:
    """Скачивает картинку, сжимает до <=1 МБ и <=1280px, сохраняет JPEG."""
    try:
        resp = requests.get(image_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        if img.width > MAX_WIDTH:
            ratio = MAX_WIDTH / img.width
            img = img.resize((MAX_WIDTH, int(img.height * ratio)))
        quality = 90
        while quality >= 40:
            buf = BytesIO()
            img.save(buf, "JPEG", quality=quality)
            if buf.tell() <= MAX_BYTES:
                out_path.write_bytes(buf.getvalue())
                return True
            quality -= 10
        out_path.write_bytes(buf.getvalue())
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось скачать постер %s: %s", image_url, e)
        return False


# ------------------------------------------------------------------
# Шаг 2: генерация через Kandinsky (FusionBrain)
# ------------------------------------------------------------------

def generate_kandinsky(prompt: str, out_path: Path) -> bool:
    api_key = os.getenv("KANDINSKY_API_KEY", "")
    secret = os.getenv("KANDINSKY_SECRET_KEY", "")
    if not api_key or not secret:
        logger.info("Ключи Kandinsky не заданы — генерация пропущена")
        return False
    base = "https://api-key.fusionbrain.ai/key/api/v1"
    headers = {"X-Key": f"Key {api_key}", "X-Secret": f"Secret {secret}"}
    try:
        models = requests.get(f"{base}/pipelines", headers=headers, timeout=15).json()
        pipeline_id = models[0]["id"]
        resp = requests.post(
            f"{base}/pipelines/{pipeline_id}/run",
            headers=headers,
            data={"type": "GENERATE", "numImages": 1,
                  "width": MAX_WIDTH, "height": 720,
                  "generateParams": f'{{"query": "{prompt}"}}'},
            timeout=30,
        ).json()
        task_id = resp["uuid"]
        for _ in range(30):  # до ~5 минут
            time.sleep(10)
            check = requests.get(
                f"{base}/pipelines/{pipeline_id}/status/{task_id}",
                headers=headers, timeout=15,
            ).json()
            if check.get("status") == "DONE":
                import base64
                img_bytes = base64.b64decode(check["result"]["files"][0])
                img = Image.open(BytesIO(img_bytes)).convert("RGB")
                img.save(out_path, "JPEG", quality=90)
                return True
            if check.get("status") == "FAIL":
                return False
    except Exception as e:  # noqa: BLE001
        logger.warning("Kandinsky не сгенерировал: %s", e)
    return False


# ------------------------------------------------------------------
# Шаг 3: локальная заглушка (последний рубеж, пост без картинки не уходит)
# ------------------------------------------------------------------

def make_placeholder(title: str, out_path: Path) -> None:
    img = Image.new("RGB", (MAX_WIDTH, 720), (24, 24, 32))
    draw = ImageDraw.Draw(img)
    # простой вертикальный градиент
    for y in range(720):
        c = (24 + y // 18, 24 + y // 24, 32 + y // 12)
        draw.line([(0, y), (MAX_WIDTH, y)], fill=c)
    font = _load_font(48)
    draw.text((60, 330), title[:60], font=font, fill=(240, 240, 240))
    img.save(out_path, "JPEG", quality=90)


# ------------------------------------------------------------------
# Фирменная плашка
# ------------------------------------------------------------------

def add_brand_frame(image_path: Path, event_date: str | None) -> None:
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    bar_h = max(56, img.height // 10)
    y0 = img.height - bar_h
    draw.rectangle([(0, y0), (img.width, img.height)], fill=(10, 10, 14, 200))
    font = _load_font(int(bar_h * 0.42))
    label = CHANNEL_NAME
    if event_date:
        label += f"  ·  {event_date}"
    draw.text((24, y0 + (bar_h - font.size) // 2), label,
              font=font, fill=(255, 255, 255))
    img.save(image_path, "JPEG", quality=90)


# ------------------------------------------------------------------
# Главная функция
# ------------------------------------------------------------------

def get_image(event: dict, image_prompt: str | None = None) -> tuple[str, str]:
    """
    Возвращает (image_path, image_source).
    image_source: poster | generated | fallback
    AUTOPOSTER_OFFLINE=1 — полностью без сети (тестовый режим main.py test).
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = IMAGES_DIR / f"{event['external_id']}-{uuid.uuid4().hex[:6]}.jpg"

    if os.getenv("AUTOPOSTER_OFFLINE"):
        make_placeholder(event.get("title") or "Афиша", out_path)
        logger.info("КАРТИНКА: заглушка (офлайн-режим) для «%s»", event.get("title"))
        add_brand_frame(out_path, event.get("date"))
        return str(out_path), "fallback"

    # 1. постер площадки
    poster_url = event.get("image_url")
    if not poster_url and event.get("link"):
        poster_url = parse_og_image(event["link"])
    if poster_url and _download_and_normalize(poster_url, out_path):
        source = "poster"
        logger.info("КАРТИНКА: постер площадки для «%s»", event.get("title"))
    # 2. генерация
    elif image_prompt and generate_kandinsky(image_prompt, out_path):
        source = "generated"
        logger.info("КАРТИНКА: сгенерирована (Kandinsky) для «%s»", event.get("title"))
    # 3. заглушка
    else:
        make_placeholder(event.get("title") or "Афиша", out_path)
        source = "fallback"
        logger.info("КАРТИНКА: заглушка для «%s»", event.get("title"))

    add_brand_frame(out_path, event.get("date"))
    logger.info("Фирменная плашка наложена: %s", out_path)
    return str(out_path), source
