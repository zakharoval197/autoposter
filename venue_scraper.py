"""
venue_scraper.py — мониторинг сайтов заведений через LLM-экстракцию.

Проблема: у клубов/залов нет RSS, афиша — HTML (Тильда, Битрикс, SPA).
Решение: скачиваем страницу афиши, чистим до текста, GigaChat извлекает
события в JSON (тест 06.10: ~3 с на страницу, точно, бесплатно).

Реестр заведений — VENUES ниже (редактируется руками).
Полный список со статусами живых/мёртвых сайтов — venues.md.

Особенности:
- beget: True — хостер Beget отдаёт JS-заглушку с кукой beget=begetok;
  ставим куку и повторяем запрос (kozlovclub, rodnya).
- insecure: True — битый сертификат, идём с verify=False (strelka, flacon).
- Текста < 400 знаков после чистки — вероятно SPA без контента, пропуск
  (такие заведения идут через Яндекс Афишу — Gipsy, Mutabor и др.).
"""

import json
import logging
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import generator
from fetcher import HEADERS, TIMEOUT, _norm_event

logger = logging.getLogger("autoposter.venues")

VENUES = [
    # --- проверены 06.10: живые, HTML парсится ---
    {"name": "16 Тонн", "url": "https://www.16tons.ru/club/afisha",
     "venue": "Клуб «16 Тонн»"},
    {"name": "Клуб Игоря Бутмана", "url": "https://moscow.butmanclub.ru/",
     "venue": "Клуб Игоря Бутмана"},
    {"name": "Зарядье", "url": "https://zaryadyehall.ru/afisha",
     "venue": "Концертный зал «Зарядье»"},
    {"name": "Noor Bar", "url": "https://noorbar.com/events",
     "venue": "Noor Bar"},
    {"name": "GAZ Clubare", "url": "https://gaz.team/clubare",
     "venue": "GAZ Clubare (Газгольдер)"},
    {"name": "Козлов Клуб", "url": "https://kozlovclub.ru/",
     "venue": "Козлов Клуб", "beget": True},
    # Родня: rodnya.ru — это отель в Ялте, не клуб. У студии Родня
    # сайта с афишей нет (контент в Telegram-канале) — не вносим.
    # Мёртвые/недоступные НЕ вносить сюда — см. venues.md
]

_EXTRACT_SYSTEM = """Ты извлекаешь события из текста страницы афиши московского заведения.
Отвечай СТРОГО JSON-массивом без markdown и комментариев:
[{"title": "...", "date": "YYYY-MM-DD" или null, "time": "HH:MM" или null,
  "price": "..." или null, "description": "1 предложение" или null,
  "link": "..." или null}]
Правила: только события с конкретной датой; только факты из текста, ничего
не выдумывай; максимум 8 событий; даты переводи в YYYY-MM-DD (год — ближайший
будущий, сегодня @@TODAY@@); относительные ссылки копируй как есть."""


def _get_html(venue: dict) -> str:
    """Скачивает страницу. Обходит Beget-заглушку (beget: True)
    и битые сертификаты (insecure: True)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    kwargs = {"timeout": TIMEOUT}
    if venue.get("insecure"):
        kwargs["verify"] = False
    resp = session.get(venue["url"], **kwargs)
    if venue.get("beget") and "begetok" in resp.text[:2000]:
        session.cookies.set("beget", "begetok")
        resp = session.get(venue["url"], **kwargs)
    resp.raise_for_status()
    return resp.text


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def _extract_events(venue: dict, text: str) -> list[dict]:
    """GigaChat извлекает события из текста страницы в JSON."""
    auth_key = os.getenv("GIGACHAT_AUTH_KEY", "")
    if not auth_key:
        raise RuntimeError("GIGACHAT_AUTH_KEY не задан — экстракция невозможна")
    import time as _time
    raw = generator._gigachat_completion(
        auth_key,
        os.getenv("GIGACHAT_MODEL", "GigaChat-Max"),
        [
            {"role": "system", "content": _EXTRACT_SYSTEM.replace(
                "@@TODAY@@", _time.strftime("%Y-%m-%d"))},
            {"role": "user", "content": text[:6000]},
        ],
    )
    raw = raw.strip().strip("`").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"LLM вернул не-JSON: {raw[:150]}")
    items = json.loads(raw[start:end + 1])
    events = []
    for it in items[:8]:
        if not it.get("title"):
            continue
        link = it.get("link")
        if link and not link.startswith("http"):
            link = urljoin(venue["url"], link)
        events.append(_norm_event(
            source=venue["name"],
            title=it.get("title"),
            venue=it.get("venue") or venue["venue"],
            date=it.get("date"),
            time_=it.get("time"),
            price=it.get("price"),
            description=it.get("description"),
            link=link,
            image_url=None,
            trusted=True,  # страница афиши заведения — не новостной шум
        ))
    return events


def fetch_venues(venues: list[dict] | None = None) -> list[dict]:
    """Собирает события со всех сайтов заведений. Ошибка одного
    заведения не валит остальные (лог + пропуск)."""
    venues = venues if venues is not None else VENUES
    all_events = []
    for venue in venues:
        name = venue["name"]
        try:
            html = _get_html(venue)
            text = _html_to_text(html)
            if len(text) < 400:
                logger.warning(
                    "ЗАВЕДЕНИЕ ✘ %s: текста %d знаков (SPA?) — пропуск",
                    name, len(text),
                )
                continue
            events = _extract_events(venue, text)
            logger.info("ЗАВЕДЕНИЕ ✔ %s: %d событий", name, len(events))
            all_events.extend(events)
        except Exception as e:  # noqa: BLE001 — не валим остальные заведения
            logger.warning("ЗАВЕДЕНИЕ ✘ %s: %s (пропущено)", name, str(e)[:150])
    logger.info("Сайты заведений: всего %d событий", len(all_events))
    return all_events
