"""
generator.py — генерация черновика поста через LLM API.

Провайдеры (выбираются по заполненным ключам в .env):
1. GigaChat (GIGACHAT_AUTH_KEY) — русскоязычный, бесплатный для физлиц.
2. Kimi / любой OpenAI-совместимый (KIMI_API_KEY + KIMI_BASE_URL).

Системный промпт НЕ зашит в код: загружается из skill_postmaker.md
(скилл постмейкера — см. бриф п.4). Если ключи не заданы —
мок-режим: шаблонный черновик без сети (для теста пайплайна).
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path

import requests
import urllib3

# GigaChat использует сертификат национального УЦ — отключаем
# предупреждения о verify=False (это штатный режим их API)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("autoposter.generator")

SKILL_PATH = Path(__file__).parent / "skill_postmaker.md"

# Кто написал последний сгенерированный пост: "kimi" | "gigachat" | "mock".
# main.py забирает сразу после generate_post() и пишет в events.writer
# (служебное поле для модерации, в канал не публикуется).
LAST_WRITER: str | None = None


def load_skill(path: Path = SKILL_PATH) -> str:
    """Загружает скилл постмейкера из отдельного файла."""
    text = path.read_text(encoding="utf-8")
    logger.info("Скилл постмейкера загружен из %s (%d знаков)", path, len(text))
    return text


def _user_prompt(event: dict) -> str:
    return (
        "Сделай пост по скиллу (формат A — одиночный анонс).\n\n"
        f"Название: {event.get('title')}\n"
        f"Место: {event.get('venue') or 'уточняется'}\n"
        f"Дата: {event.get('date') or 'уточняется'}\n"
        f"Время: {event.get('time') or 'уточняется'}\n"
        f"Цена: {event.get('price') or 'уточняется'}\n"
        f"Описание от площадки: {event.get('description') or 'нет'}\n"
        f"Ссылка: {event.get('link') or ''}\n"
        f"Источник афиши: {event.get('source') or ''}\n\n"
        "Напоминание: ссылку бери из данных как есть, ничего не выдумывай, "
        "без риторических вопросов, без служебных комментариев — только пост."
    )


# ------------------------------------------------------------------
# GigaChat (Сбер): своя OAuth-авторизация, токен живёт ~30 минут
# ------------------------------------------------------------------

_GIGACHAT_TOKEN = {"value": None, "expires_at": 0.0}


def _gigachat_token(auth_key: str) -> str:
    if _GIGACHAT_TOKEN["value"] and _GIGACHAT_TOKEN["expires_at"] > time.time() + 60:
        return _GIGACHAT_TOKEN["value"]
    resp = requests.post(
        "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        headers={
            "Authorization": f"Basic {auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"scope": os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")},
        timeout=30, verify=False,
    )
    resp.raise_for_status()
    data = resp.json()
    _GIGACHAT_TOKEN["value"] = data["access_token"]
    _GIGACHAT_TOKEN["expires_at"] = data.get("expires_at", 0) / 1000
    logger.info("GigaChat: получен новый access token")
    return data["access_token"]


def _gigachat_completion(auth_key: str, model: str, messages: list[dict]) -> str:
    token = _gigachat_token(auth_key)
    try:
        resp = requests.post(
            "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": model, "messages": messages},
            timeout=180, verify=False,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = e.response.text[:300] if e.response is not None else ""
        raise RuntimeError(f"{e} | ответ API: {body}") from e
    return resp.json()["choices"][0]["message"]["content"].strip()


def _chat_completion(api_key: str, base_url: str, model: str,
                     messages: list[dict]) -> str:
    """Единая точка вызова chat/completions.

    temperature отправляем только если задан KIMI_TEMPERATURE:
    некоторые модели (напр. kimi-k2) принимают строго temperature=1.

    Всегда стримим (SSE): thinking-модель «молча думает» 2–4 минуты,
    и agent-gw рвёт молчаливое соединение — без стриминга длинные
    генерации зависают до таймаута (поймано 06.10: первый чанк
    приходит за ~5 с и держит соединение живым).
    """
    # agent-gw.kimi.com/coding требует /v1 в пути: /coding/v1/chat/completions.
    # Без нормализации запасной провайдер молча 404-ил (поймано 06.10).
    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    payload = {"model": model, "messages": messages, "stream": True}
    temperature = os.getenv("KIMI_TEMPERATURE")
    if temperature:
        payload["temperature"] = float(temperature)
    try:
        with requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=(30, 90),  # connect / пауза между чанками
            stream=True,
        ) as resp:
            resp.raise_for_status()
            parts = []
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (ValueError, KeyError, IndexError):
                    continue
                if delta.get("content"):
                    parts.append(delta["content"])
    except requests.HTTPError as e:
        body = ""
        if e.response is not None:
            body = e.response.text[:300]
        raise RuntimeError(f"{e} | ответ API: {body}") from e
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("пустой ответ от LLM (стриминг завершился без контента)")
    return text


_META_PATTERNS = (
    "поставь картинку", "лог выбора", "выбор изображения",
    "картинку к посту", "редактору", "примечание:",
    "прим.:",
)


def _clean_post(text: str) -> str:
    """Убирает мусор из ответа модели.

    1. Обрамление ```...``` и цитаты '>' (модели копируют их из примеров скилла).
    2. Служебные хвосты: строки с мета-комментариями модели
       («поставь картинку», «лог выбора изображения» и т.п.).
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()

    lines = []
    for raw in text.splitlines():
        ln = raw.lstrip(">").lstrip()
        low = ln.lower()
        # строка целиком — служебная скобка в конце поста
        if ln.startswith("(") and any(p in low for p in _META_PATTERNS):
            continue
        if any(p in low for p in _META_PATTERNS):
            continue
        lines.append(ln)

    # обрезать пустые строки по краям
    cleaned = "\n".join(lines).strip()
    return cleaned


def generate_post(event: dict, skill_text: str | None = None) -> str:
    """Генерирует черновик.

    Провайдер по приоритету: GigaChat (GIGACHAT_AUTH_KEY) →
    Kimi/OpenAI-совместимый (KIMI_API_KEY). Без ключей — мок
    (или принудительно флагом --mock). AUTOPOSTER_OFFLINE на текст
    не влияет: он про картинки.

    Fallback для Kimi-пути: api.moonshot.ai рвёт соединения на больших
    русских промптах — тогда пробуем EN-зеркало (skill_postmaker.en.md).
    SKILL_LANG=en — пропустить RU-попытку и сразу идти по зеркалу.

    Побочный эффект: выставляет LAST_WRITER (kimi | gigachat | mock).
    """
    global LAST_WRITER
    system_prompt = skill_text or load_skill()

    gigachat_key = os.getenv("GIGACHAT_AUTH_KEY", "")
    api_key = os.getenv("KIMI_API_KEY", "")

    if os.getenv("AUTOPOSTER_MOCK") or not (gigachat_key or api_key):
        reason = "флаг --mock" if os.getenv("AUTOPOSTER_MOCK") else "нет ключей LLM"
        logger.warning("Мок-режим генерации (%s)", reason)
        LAST_WRITER = "mock"
        return _mock_post(event)

    # --- порядок провайдеров: AUTOPOSTER_WRITER=kimi отдаёт посты Kimi ---
    # (06.10: Kimi через agent-gw пишет без выдумок, GigaChat галлюцинирует).
    # Запасная цепочка в любом случае: другой LLM -> мок.
    writer = os.getenv("AUTOPOSTER_WRITER", "gigachat").lower()

    if writer == "kimi" and api_key:
        text = _try_kimi(api_key, system_prompt, event)
        if text:
            LAST_WRITER = "kimi"
            return text
        logger.info("Kimi не справился — ухожу на GigaChat")

    # --- провайдер: GigaChat (русский скилл идёт нативно) ---
    if gigachat_key:
        try:
            text = _gigachat_completion(
                gigachat_key,
                os.getenv("GIGACHAT_MODEL", "GigaChat-Max"),
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": _user_prompt(event)},
                ],
            )
            text = _clean_post(text)
            logger.info(
                "ГЕНЕРАЦИЯ ✔ [GigaChat] «%s»: %d знаков",
                event.get("title"), len(text),
            )
            LAST_WRITER = "gigachat"
            return text
        except Exception as e:  # noqa: BLE001
            logger.warning("GigaChat не ответил (%s)", str(e)[:150])
            if not api_key:
                LAST_WRITER = "mock"
                return _mock_post(event)
            logger.info("Пробую запасной провайдер (KIMI_API_KEY)")

    # --- провайдер: Kimi / OpenAI-совместимый ---
    if api_key:
        text = _try_kimi(api_key, system_prompt, event)
        if text:
            LAST_WRITER = "kimi"
            return text

    logger.warning("Все провайдеры оборвались — мок-черновик")
    LAST_WRITER = "mock"
    return _mock_post(event)


def _try_kimi(api_key: str, system_prompt: str, event: dict) -> str | None:
    """Kimi/OpenAI-совместимый провайдер. None — не получилось.

    Fallback: если RU-скилл оборвался по сети — пробуем EN-зеркало
    (skill_postmaker.en.md). SKILL_LANG=en форсирует зеркало.
    """
    base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    model = os.getenv("KIMI_MODEL", "kimi-latest")

    attempts = [("RU", system_prompt)]
    en_mirror = SKILL_PATH.with_name("skill_postmaker.en.md")
    if en_mirror.exists():
        attempts.append(("EN", en_mirror.read_text(encoding="utf-8")))
    if os.getenv("SKILL_LANG", "").lower() == "en":
        attempts = [a for a in attempts if a[0] == "EN"] or attempts

    for lang, prompt in attempts:
        # agent-gw флакует: ~50% длинных запросов рвутся (06.10).
        # Одна повторная попытка через 5 с снимает большинство обрывов.
        for attempt in range(2):
            try:
                text = _chat_completion(
                    api_key, base_url, model,
                    [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": _user_prompt(event)},
                    ],
                )
                text = _clean_post(text)
                logger.info(
                    "ГЕНЕРАЦИЯ ✔ [Kimi %s-скилл] «%s»: %d знаков",
                    lang, event.get("title"), len(text),
                )
                return text
            except requests.ConnectionError as e:
                logger.warning(
                    "API оборвал соединение на %s-скилле, попытка %d/2 (%s)",
                    lang, attempt + 1, str(e)[:100],
                )
                if attempt == 0:
                    time.sleep(5)
                continue
            except Exception as e:  # noqa: BLE001 — 4xx/5xx: ретраить нет смысла
                logger.warning("Kimi API недоступен (%s)", str(e)[:150])
                return None
    return None


def generate_image_prompt(event: dict, skill_text: str | None = None) -> str:
    """Промпт для генерации обложки (Kandinsky/FLUX) по описанию события."""
    fallback = (
        f"атмосферная обложка для афиши: {event.get('title')}, "
        f"место: {event.get('venue')}, ночная Москва, кинематографичный свет"
    )
    api_key = os.getenv("KIMI_API_KEY", "")
    if not api_key:
        return fallback
    base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    model = os.getenv("KIMI_MODEL", "kimi-latest")
    try:
        return _chat_completion(
            api_key, base_url, model,
            [
                {"role": "system", "content": (
                    "Ты пишешь короткие визуальные промпты для генератора изображений. "
                    "Один абзац, без людей крупным планом, атмосфера события, "
                    "кинематографичный свет. Отвечай только промптом."
                )},
                {"role": "user", "content": (
                    f"Событие: {event.get('title')}. "
                    f"Место: {event.get('venue')}. "
                    f"Описание: {event.get('description') or ''}"
                )},
            ],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Промпт обложки через LLM не получен (%s) — шаблон", e)
        return fallback


def _mock_post(event: dict) -> str:
    """Шаблонный черновик для теста пайплайна без LLM."""
    title = event.get("title") or "Событие"
    venue = event.get("venue") or "место уточняется"
    date = event.get("date") or "дата уточняется"
    time_ = event.get("time") or ""
    price = event.get("price") or "вход уточняется"
    link = event.get("link") or ""
    src = event.get("source") or ""
    return (
        f"🔥 {title}\n\n"
        f"[МОК] Здесь будет живой текст по скиллу постмейкера: вайб, "
        f"факты, конкретика. Сейчас включён мок-режим генерации.\n\n"
        f"{date}{' · ' + time_ if time_ else ''} · {venue} · {price}\n"
        f"{link}\n"
        f"афиша: {src}"
    )
