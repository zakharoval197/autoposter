"""
publisher.py — публикация одобренного поста в канал.

Вызывается из moderator.py после нажатия «Опубликовать».
Прямой автопостинг без модерации здесь намеренно отсутствует (бриф п.6).
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import FSInputFile

logger = logging.getLogger("autoposter.publisher")

PUBLISH_RETRIES = 3
RETRY_DELAY_SEC = 5


async def publish_event(bot: Bot, event: dict, channel_id: str) -> None:
    """Публикует пост в канал: фото + текст (или просто текст).

    Ретраи при сетевых сбоях: api.telegram.org из РФ нестабилен,
    единичный таймаут не должен ронять публикацию.
    """
    text = event.get("post_text") or event.get("title")
    last_error = None
    for attempt in range(1, PUBLISH_RETRIES + 1):
        try:
            if event.get("image_path") and os.path.exists(event["image_path"]):
                await bot.send_photo(
                    channel_id, FSInputFile(event["image_path"]),
                    caption=text[:1024],
                )
            else:
                await bot.send_message(channel_id, text)
            logger.info(
                "ПУБЛИКАЦИЯ ✔ #%s «%s» в %s (попытка %d)",
                event.get("id"), event.get("title"), channel_id, attempt,
            )
            return
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.warning(
                "Публикация #%s: попытка %d/%d не удалась (%s)",
                event.get("id"), attempt, PUBLISH_RETRIES, str(e)[:120],
            )
            if attempt < PUBLISH_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SEC)
    raise last_error


def mark_published(db, event_id: int) -> None:
    db.update_event(
        event_id, status="published",
        published_at=datetime.now(timezone.utc).isoformat(),
    )
