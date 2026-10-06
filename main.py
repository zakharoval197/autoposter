"""
main.py — точка входа пайплайна «Куда сходить в Москве».

Запуск:
    python main.py run        # полный цикл: сбор -> фильтр -> картинка -> пост -> draft
    python main.py test       # демо на 3 фейковых событиях; текст — реальный Kimi API,
                              # если ключ задан; картинки всегда заглушки
    python main.py test --mock  # принудительный мок-текст даже при заданном ключе
    python main.py moderate   # бот-модератор (long polling, отдельный процесс)
    python main.py stats      # статистика по базе

Cron на VPS (1–2 поста в день, сбор вечером):
    17 17 * * *  cd /opt/autoposter && /usr/bin/python3 main.py run
Модератор держим постоянно: systemd unit или tmux + python main.py moderate
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

LOG_DIR = BASE_DIR / "logs"


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(os.getenv("LOG_LEVEL", "INFO"))

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "pipeline.log", maxBytes=2_000_000, backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def cmd_run(test_mode: bool = False) -> None:
    if test_mode:
        # Только КАРТИНКИ офлайн (заглушки, не тратим API на обложки).
        # Текст поста генерируется реальным Kimi API, если KIMI_API_KEY задан.
        os.environ["AUTOPOSTER_OFFLINE"] = "1"
    import database
    import fetcher
    import filter as event_filter
    import generator
    import image_processor

    db = database.Database(database.resolve_db_path())
    logger = logging.getLogger("autoposter.main")
    logger.info("=== Запуск пайплайна (test_mode=%s) ===", test_mode)

    # 1. сбор
    events = fetcher.fetch_fake() if test_mode else fetcher.fetch_all()

    # 1.5 сайты заведений (LLM-экстракция, venue_scraper.py).
    # AUTOPOSTER_VENUES=0 в .env — выключить.
    if not test_mode and os.getenv("AUTOPOSTER_VENUES", "1") != "0":
        import venue_scraper
        events += venue_scraper.fetch_venues()

    # 2. фильтр
    kept, dropped = event_filter.filter_events(events)
    for ev in dropped:
        if not db.exists(ev["external_id"]):
            ev["status"] = "filtered"
            db.add_event(ev)

    # 3. дедупликация (без записи — запись после лимита, см. ниже)
    fresh = []
    for ev in kept:
        if db.exists(ev["external_id"]):
            logger.info("Дубль, пропускаю: %s", ev.get("title"))
            continue
        # полнота данных: без даты или места пост бесполезен —
        # не тратим LLM и время модерации
        if not ev.get("date") or not ev.get("venue"):
            ev["status"] = "incomplete"
            db.add_event(ev)
            logger.info(
                "Неполные данные (нет даты/места), пропускаю: %s", ev.get("title")
            )
            continue
        fresh.append(ev)

    # Лимит черновиков за прогон: применяем ПОСЛЕ дедупликации
    # (иначе повторный прогон берёт те же первые N, которые уже
    # дубли, и не делает ничего) и ДО записи в базу (иначе события
    # сверх лимита застрянут в статусе new навсегда).
    # AUTOPOSTER_MAX_DRAFTS=0 — без лимита.
    max_drafts = int(os.getenv("AUTOPOSTER_MAX_DRAFTS", "5"))
    if max_drafts and len(fresh) > max_drafts:
        logger.info(
            "Лимит черновиков: %d из %d (AUTOPOSTER_MAX_DRAFTS)",
            max_drafts, len(fresh),
        )
        fresh = fresh[:max_drafts]

    new_events = []
    for ev in fresh:
        event_id = db.add_event(ev)
        if event_id:
            ev["id"] = event_id
            new_events.append(ev)

    # 4. скилл постмейкера — из отдельного файла
    skill_text = generator.load_skill()

    # 5. по каждому событию: картинка -> пост -> статус draft
    for ev in new_events:
        image_prompt = None
        if not ev.get("image_url") and not os.getenv("AUTOPOSTER_OFFLINE"):
            image_prompt = generator.generate_image_prompt(ev, skill_text)
        image_path, image_source = image_processor.get_image(ev, image_prompt)

        post_text = generator.generate_post(ev, skill_text)
        writer = generator.LAST_WRITER  # кто написал: kimi | gigachat | mock
        db.update_event(
            ev["id"],
            image_path=image_path, image_source=image_source,
            post_text=post_text, writer=writer, status="draft",
        )
        logger.info(
            "ЧЕРНОВИК ГОТОВ #%s «%s» | текст: %s | картинка: %s | %d знаков",
            ev["id"], ev.get("title"), writer, image_source, len(post_text),
        )
        if test_mode:
            print("\n" + "=" * 60)
            print(post_text)
            print("=" * 60)

    logger.info("=== Пайплайн завершён. Статистика: %s ===", db.stats())
    if not test_mode:
        logger.info("Черновики ждут «ок»: напиши боту /pending")
    db.close()


def main() -> None:
    setup_logging()
    # первичная настройка: если .env не заполнен — спросить интерактивно
    import setup_wizard
    setup_wizard.ensure_config()
    load_dotenv(BASE_DIR / ".env", override=True)  # подхватить свежие значения

    command = sys.argv[1] if len(sys.argv) > 1 else "help"

    if command == "run":
        cmd_run(test_mode=False)
    elif command == "test":
        if "--mock" in sys.argv:
            os.environ["AUTOPOSTER_MOCK"] = "1"  # принудительный мок даже с ключом
        cmd_run(test_mode=True)
    elif command == "moderate":
        import asyncio
        import moderator
        asyncio.run(moderator.main())
    elif command == "stats":
        import database
        db = database.Database(database.resolve_db_path())
        print("Статистика базы:", db.stats())
        db.close()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
