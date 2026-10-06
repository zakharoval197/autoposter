"""
moderator.py — модерация черновиков в личке (aiogram 3.x).

Запускается отдельным процессом:  python main.py moderate
Бот рассылает админу черновики со статусом 'draft' с кнопками
«Опубликовать» / «Пропустить». Ничего не публикуется без «ок».
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import database
import publisher

logger = logging.getLogger("autoposter.moderator")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = database.resolve_db_path()  # якорит относительный путь на проект

# api.telegram.org из РФ часто недоступен напрямую. TELEGRAM_PROXY —
# локальный прокси VPN (Happ: http://127.0.0.1:10809 / socks5 10808).
from aiogram.client.session.aiohttp import AiohttpSession

_PROXY = os.getenv("TELEGRAM_PROXY", "")
_session = AiohttpSession(proxy=_PROXY) if _PROXY else None
bot = Bot(token=BOT_TOKEN, session=_session)
dp = Dispatcher()
db = database.Database(DB_PATH)


def _kb(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать",
                             callback_data=f"pub:{event_id}"),
        InlineKeyboardButton(text="⏭ Пропустить",
                             callback_data=f"skip:{event_id}"),
    ]])


async def send_draft(event: dict) -> None:
    """Присылает один черновик админу в личку."""
    caption = (
        f"Черновик #{event['id']} · источник: {event.get('source')}\n"
        f"текст: {event.get('writer') or '?'} · "
        f"картинка: {event.get('image_source')}\n\n"
        f"{event.get('post_text') or '(нет текста)'}"
    )
    if event.get("image_path") and os.path.exists(event["image_path"]):
        await bot.send_photo(
            ADMIN_ID, FSInputFile(event["image_path"]),
            caption=caption[:1024], reply_markup=_kb(event["id"]),
        )
    else:
        await bot.send_message(ADMIN_ID, caption, reply_markup=_kb(event["id"]))
    logger.info("МОДЕРАЦИЯ: черновик #%s отправлен админу", event["id"])


@dp.message(Command("pending"))
async def cmd_pending(message: Message) -> None:
    """Прислать все ожидающие черновики."""
    if message.from_user.id != ADMIN_ID:
        return
    drafts = db.get_by_status("draft")
    if not drafts:
        await message.answer("Очередь пуста — новых черновиков нет.")
        return
    for event in drafts:
        await send_draft(event)
    await message.answer(f"Отправил {len(drafts)} черновиков.")


@dp.callback_query(F.data.startswith("pub:"))
async def on_publish(cb: CallbackQuery) -> None:
    event_id = int(cb.data.split(":", 1)[1])
    event = db.get_event(event_id)
    if not event or event["status"] != "draft":
        await cb.answer("Черновик уже обработан.", show_alert=True)
        return
    logger.info("МОДЕРАЦИЯ: #%s одобрен, публикую", event_id)
    try:
        # статус НЕ меняем до фактической публикации:
        # при сбое сети черновик остаётся draft и кнопка сработает снова
        await publisher.publish_event(bot, event, CHANNEL_ID)
        publisher.mark_published(db, event_id)
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Опубликовано ✅")
    except Exception as e:  # noqa: BLE001
        logger.error("Публикация #%s не удалась: %s", event_id, e)
        await cb.answer(
            "Не удалось опубликовать (сеть?). Черновик не потерян — нажми ещё раз.",
            show_alert=True,
        )


@dp.callback_query(F.data.startswith("skip:"))
async def on_skip(cb: CallbackQuery) -> None:
    event_id = int(cb.data.split(":", 1)[1])
    db.update_event(event_id, status="skipped")
    logger.info("МОДЕРАЦИЯ: #%s пропущен", event_id)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Пропущено ⏭")


async def main() -> None:
    if not BOT_TOKEN or not ADMIN_ID:
        raise SystemExit("Задай BOT_TOKEN и ADMIN_ID в .env")
    logger.info("Модератор запущен. Команда /pending — прислать черновики.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
