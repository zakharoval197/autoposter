"""
setup_wizard.py — интерактивная первичная настройка.

При первом запуске любой команды проверяет .env.
Если KIMI_API_KEY не задан — спрашивает всё необходимое и сохраняет в .env.
При следующих запусках вопросы не повторяются.

Enter на API-ключе -> тестовый режим (посты с пометкой [МОК]).
В неинтерактивном окружении (cron) вопросы не задаются — только предупреждение.
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger("autoposter.setup")

ENV_PATH = Path(__file__).parent / ".env"

# (ключ, подсказка, обязательное)
QUESTIONS = [
    ("KIMI_API_KEY",
     "Введите API-ключ Kimi (или нажмите Enter для тестового режима)", False),
    ("BOT_TOKEN",
     "Введите Telegram Bot Token (получить у @BotFather)", False),
    ("CHANNEL_ID",
     "Введите ID канала (@username или -100...)", False),
    ("ADMIN_ID",
     "Введите ваш Telegram ID (получить у @userinfobot)", False),
]

DEFAULTS = {
    "KIMI_BASE_URL": "https://api.moonshot.cn/v1",
    "KIMI_MODEL": "kimi-latest",
    "CHANNEL_NAME": "Куда сходить в Москве",
    "DB_PATH": "data/events.db",
    "LOG_LEVEL": "INFO",
}


def _read_env(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def _write_env(path: Path, values: dict) -> None:
    lines = [
        "# Настройки автопостинга «Куда сходить в Москве»",
        "# Заполнено мастером первичной настройки",
        "",
    ]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_config() -> None:
    """Проверяет .env и при необходимости запускает мастер настройки."""
    values = _read_env(ENV_PATH)

    if values.get("KIMI_API_KEY") or values.get("SETUP_DONE") == "1":
        return  # всё настроено (или тестовый режим выбран осознанно), не спрашиваем

    if not sys.stdin.isatty():
        # cron / пайп: спросить нельзя — работаем в тестовом режиме
        logger.warning(
            "KIMI_API_KEY не задан и ввод невозможен (не терминал) — "
            "тестовый режим. Запусти python main.py test вручную для настройки."
        )
        return

    print("=" * 60)
    print("ПЕРВИЧНАЯ НАСТРОЙКА «Куда сходить в Москве»")
    print("Ответы сохранятся в .env, вопросы больше не повторятся.")
    print("=" * 60)

    for key, prompt, _required in QUESTIONS:
        existing = values.get(key, "")
        suffix = f" [{existing}]" if existing else ""
        try:
            entered = input(f"\n{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            entered = ""
        if entered:
            values[key] = entered

    # значения по умолчанию, которые спрашивать не нужно
    for key, value in DEFAULTS.items():
        values.setdefault(key, value)
    values["SETUP_DONE"] = "1"  # настройка пройдена — больше не спрашивать

    _write_env(ENV_PATH, values)

    if values.get("KIMI_API_KEY"):
        print("\nНастройки сохранены в .env. API-ключ задан — боевой режим.")
    else:
        print("\nНастройки сохранены в .env.")
        print("API-ключ не задан — ТЕСТОВЫЙ РЕЖИМ: посты с пометкой [МОК].")
        print("Когда получишь ключ — впиши его в .env в строку KIMI_API_KEY.")
    print("=" * 60)
