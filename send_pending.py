"""
send_pending.py — одноразовая отправка всех черновиков админу в личку.

Использует moderator.send_draft (та же карточка с кнопками, что и /pending).
Нужен, когда бот-модератор не запущен как процесс: сообщения дойдут
и будут ждать в чате, но кнопки оживут только после запуска
`python main.py moderate` (обработчики кнопок живут в нём).

Запуск:  python send_pending.py            — все черновики
         python send_pending.py --since N  — только с id >= N
         (удобно после прогона: не дублировать уже отправленные)
"""

import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("autoposter.send_pending")


async def main() -> None:
    import moderator  # noqa: PLC0415 — импорт после load_dotenv

    drafts = moderator.db.get_by_status("draft")
    if "--since" in sys.argv:
        since = int(sys.argv[sys.argv.index("--since") + 1])
        drafts = [d for d in drafts if d["id"] >= since]
    if not drafts:
        logger.info("Черновиков нет — отправлять нечего.")
        return
    logger.info("Отправляю %d черновиков админу %s...",
                len(drafts), moderator.ADMIN_ID)
    sent, failed = 0, 0
    for event in drafts:
        try:
            await moderator.send_draft(event)
            sent += 1
        except Exception as e:  # noqa: BLE001 — один сбой не должен ронять рассылку
            failed += 1
            logger.error("Черновик #%s не ушёл: %s", event.get("id"), e)
    await moderator.bot.session.close()
    logger.info("Готово: отправлено %d, ошибок %d.", sent, failed)


if __name__ == "__main__":
    asyncio.run(main())
