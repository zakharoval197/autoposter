"""
fetcher.py — сбор событий из источников (requests + BeautifulSoup).

Типы источников:
- rss  : RSS/Atom-ленты
- html : сайты под парсинг по CSS-селекторам
- json : открытые API (KudaGo — бонус, реально работает)

Список источников захардкожен в SOURCES — редактируется руками.
Сайты с капчей/защитой пропускаем и помечаем в логе (бриф, п.6).
"""

import email.utils
import hashlib
import logging
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("autoposter.fetcher")

HEADERS = {
    # Только ASCII: requests кодирует заголовки в latin-1,
    # кириллица в UA уронит ВЕСЬ запрос (поймано 05.10).
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "KudaSkitMskBot/1.0 (+afisha-channel)"
    )
}
TIMEOUT = 15
RETRIES = 3  # mos.ru и др. периодически режут первый коннект


def _get(url: str, **kwargs) -> requests.Response:
    """GET с ретраями: источники местами нестабильны (mos.ru таймаутит
    первый коннект). Между попытками пауза 2с, 4с, ..."""
    last_err = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            if attempt < RETRIES - 1:
                time.sleep(2 * (attempt + 1))
    raise last_err

# ------------------------------------------------------------------
# Реальные источники (проверены вживую 05.10.2026 — все отвечают 200,
# XML валидный, капчи нет). Мёртвые/пустые:
#   - kudago.com (API и HTML) — DNS резолвится, но TCP/TLS виснет:
#     похоже на блокировку/троттлинг со стороны сети. Закомментирован,
#     на зарубежном VPS может ожить.
#   - timeout.ru/feed — RSS есть, но item'ов ноль (ленты пустые).
#   - the-village.ru, thecity.msk.ru, gorky-park.ru — домены мертвы.
#   - afisha.ru, kassir.ru, timepad, mskagency — RSS нет (404).
# ------------------------------------------------------------------
SOURCES = [
    {
        "name": "Яндекс Афиша",
        "type": "ya_afisha",
        # Внутренний GraphQL API (разведан 05.10: /api/graphql?city=moscow,
        # нужны куки с главной страницы). Молодёжные рубрики:
        # party = вечеринки, standup = стендап, concert = концерты.
        "tags": ["party", "standup", "concert"],
        "limit": 8,          # событий на рубрику
        "days_ahead": 14,    # горизонт: ближайшие 2 недели
        # Структурированная афиша (не новости) — KEEP-фильтр не нужен.
        "trusted": True,
    },
    {
        "name": "mos.ru Афиша",
        "type": "rss",
        # Лента — именно афиша: ссылки вида /afisha/event/<id>,
        # pubDate = дата события, enclosure = обложка 1920x1080.
        "url": "https://www.mos.ru/rss",
        # Доверенный: это уже отобранная афиша Москвы, фильтр по
        # KEEP_KEYWORDS не нужен (иначе отсеивает всё — поймано 05.10).
        "trusted": True,
    },
    {
        "name": "Афиша Daily",
        "type": "rss",
        # ВАЖНО: слеш в конце обязателен, без него — 404.
        "url": "https://daily.afisha.ru/rss/",
    },
    {
        "name": "Москва 24",
        "type": "rss",
        "url": "https://www.m24.ru/rss.xml",
    },
    {
        "name": "Сноб",
        "type": "rss",
        "url": "https://snob.ru/rss/",
    },
    {
        "name": "InterMedia",
        "type": "rss",
        "url": "https://www.intermedia.ru/rss.xml",
    },
    {
        "name": "Jazz.ru",
        "type": "rss",
        "url": "https://www.jazz.ru/rss/",
    },
    # Закомментировано: с машины user'а kudago.com недоступен (таймаут
    # на TCP, не DNS). Перепроверить на VPS — там может работать.
    # {
    #     "name": "KudaGo API",
    #     "type": "json",
    #     "url": "https://kudago.com/public-api/v1.4/events/",
    #     "params": {
    #         "location": "msk",
    #         "fields": "id,title,description,place,dates,price,site_url,images",
    #         "expand": "place",
    #         "page_size": 10,
    #         "order_by": "-publication_date",
    #     },
    # },
]


def make_external_id(source: str, link: str) -> str:
    return hashlib.sha1(f"{source}|{link}".encode("utf-8")).hexdigest()[:16]


def _norm_event(source: str, title, venue, date, time_, price,
                description, link, image_url, trusted=False) -> dict:
    return {
        "external_id": make_external_id(source, link or title or ""),
        "title": (title or "").strip(),
        "venue": (venue or "").strip() or None,
        "date": (date or "").strip() or None,
        "time": (time_ or "").strip() or None,
        "price": (price or "").strip() or None,
        "description": (description or "").strip() or None,
        "link": link,
        "source": source,
        "image_url": image_url,
        "trusted": bool(trusted),
    }


# ------------------------------------------------------------------
# Парсеры по типам
# ------------------------------------------------------------------

def _parse_rss_date(raw: str | None) -> tuple[str | None, str | None]:
    """pubDate (RFC822) -> (YYYY-MM-DD, HH:MM) в локальном времени.

    У mos.ru pubDate — это дата/время самого события, у новостных
    лент — время публикации; в обоих случаях полезно для контекста.
    """
    if not raw:
        return None, None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except (TypeError, ValueError):
        return None, None


def _fetch_rss(source: dict) -> list[dict]:
    resp = _get(source["url"])
    soup = BeautifulSoup(resp.content, "xml")
    events = []
    for item in soup.find_all("item")[:15]:
        link_tag = item.find("link")
        # enclosure = обложка события (есть у mos.ru, daily.afisha, m24)
        enc = item.find("enclosure")
        image_url = enc.get("url") if enc and enc.get("url") else None
        date_str, time_str = _parse_rss_date(
            item.pubDate.text if item.find("pubDate") else None
        )
        desc_html = item.description.text if item.description else ""
        desc = BeautifulSoup(desc_html, "html.parser").text
        events.append(_norm_event(
            source=source["name"],
            title=item.title.text if item.title else "",
            venue=None,
            date=date_str,
            time_=time_str,
            price=None,
            description=desc[:500] or None,
            link=link_tag.text.strip() if link_tag else None,
            image_url=image_url,
        ))
    return events


def _fetch_html(source: dict) -> list[dict]:
    resp = _get(source["url"])
    soup = BeautifulSoup(resp.text, "html.parser")
    sel = source["selectors"]
    events = []
    for item in soup.select(sel["item"])[:10]:
        title_tag = item.select_one(sel["title"])
        link_tag = item.select_one(sel["link"])
        link = link_tag.get("href") if link_tag else None
        if link and link.startswith("/"):
            from urllib.parse import urljoin
            link = urljoin(source["url"], link)
        events.append(_norm_event(
            source=source["name"],
            title=title_tag.text if title_tag else "",
            venue=None, date=None, time_=None, price=None,
            description=item.text[:500],
            link=link, image_url=None,
        ))
    return events


def _fetch_json(source: dict) -> list[dict]:
    """KudaGo public API (на случай, если домен разблокируют на VPS)."""
    resp = _get(source["url"], params=source.get("params"))
    data = resp.json()
    events = []
    for item in data.get("results", [])[:10]:
        place = item.get("place") or {}
        dates = item.get("dates") or [{}]
        start = dates[0].get("start")
        date_str = time.strftime("%Y-%m-%d", time.localtime(start)) if start else None
        time_str = time.strftime("%H:%M", time.localtime(start)) if start else None
        images = item.get("images") or []
        image_url = images[0].get("image") if images else None
        desc = BeautifulSoup(item.get("description") or "", "html.parser").text
        events.append(_norm_event(
            source=source["name"],
            title=item.get("title"),
            venue=place.get("title"),
            date=date_str, time_=time_str,
            price=item.get("price"),
            description=desc[:500],
            link=item.get("site_url"),
            image_url=image_url,
        ))
    return events


# ------------------------------------------------------------------
# Яндекс Афиша: внутренний GraphQL API (разведано 05.10.2026).
#
# Как работает:
# 1. GET главной — получаем куки (afisha.sid, yandexuid и др.),
#    без них POST /api/graphql рвётся (RemoteDisconnected).
# 2. POST /api/graphql?city=moscow&version=<версия фронта>
#    &query_name=<имя операции>. Запросы НЕ персистентные:
#    произвольный валидный GraphQL проходит (проверено).
# 3. actualEvents(dates: {date, period}, tags: [...]) — выдача
#    по рубрикам за ближайшие N дней. Цены в копейках!
# ------------------------------------------------------------------

YA_GQL_URL = "https://afisha.yandex.ru/api/graphql?city=moscow&version=603.0"
YA_GQL_QUERY = """
query YouthEvents($today: Date!, $days: Int!, $paging: PagingInput, $tags: [String]) {
  events: actualEvents(dates: {date: $today, period: $days}, tags: $tags, paging: $paging) {
    items {
      event {
        id title url contentRating
        rubric { code name }
        image { image(size: s840x840_crop) { url } }
      }
      scheduleInfo {
        preview { text }
        collapsedText
        placePreview
        prices { value currency }
      }
    }
    paging { total }
  }
}
"""


def _fetch_ya_afisha(source: dict) -> list[dict]:
    session = requests.Session()
    session.headers.update({
        **HEADERS,
        "Accept": "*/*",
        "Referer": "https://afisha.yandex.ru/moscow",
        "Origin": "https://afisha.yandex.ru",
        "X-Parent-Request-Id": "1",
    })
    # куки: без первого GET на сайт GraphQL молча рвёт соединение
    resp = session.get("https://afisha.yandex.ru/moscow", timeout=TIMEOUT)
    resp.raise_for_status()

    limit = source.get("limit", 8)
    events = []
    for tag in source.get("tags", ["party", "standup"]):
        payload = {
            "operationName": "YouthEvents",
            "query": YA_GQL_QUERY,
            "variables": {
                "today": time.strftime("%Y-%m-%d"),
                "days": int(source.get("days_ahead", 14)),
                "tags": [tag],
                "paging": {"offset": 0, "limit": limit},
            },
        }
        resp = session.post(
            YA_GQL_URL + "&query_name=YouthEvents", json=payload, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        events_data = (data.get("data") or {}).get("events")
        if not events_data:
            # queryErrors без данных — фатально; частичные ошибки
            # с данными терпим (places у них периодически 500-ит)
            raise ValueError(f"GraphQL ошибка ({tag}): {str(data)[:200]}")
        for it in (events_data.get("items") or [])[:limit]:
            ev = it.get("event") or {}
            si = it.get("scheduleInfo") or {}
            prices = [p["value"] // 100 for p in (si.get("prices") or [])]
            price = f"от {min(prices)}₽" if prices else None
            when = ((si.get("preview") or {}).get("text")
                    or si.get("collapsedText"))
            where = si.get("placePreview")
            rubric = (ev.get("rubric") or [{}])[0].get("name") or ""
            rating = ev.get("contentRating") or ""
            desc_parts = [p for p in [
                f"{rubric}{', ' + rating if rating else ''}".strip(", "),
                f"Когда: {when}" if when else None,
                f"Где: {where}" if where else None,
                f"Цена: {price}" if price else None,
            ] if p]
            events.append(_norm_event(
                source=source["name"],
                title=ev.get("title"),
                venue=where,
                date=when, time_=None,
                price=price,
                description=". ".join(desc_parts),
                link=("https://afisha.yandex.ru" + ev["url"]) if ev.get("url") else None,
                image_url=((ev.get("image") or {}).get("image") or {}).get("url"),
            ))
    return events


FETCHERS = {"rss": _fetch_rss, "html": _fetch_html, "json": _fetch_json,
            "ya_afisha": _fetch_ya_afisha}


# ------------------------------------------------------------------
# Основная функция
# ------------------------------------------------------------------

def fetch_all(sources: list[dict] | None = None) -> list[dict]:
    sources = sources or SOURCES
    all_events = []
    for source in sources:
        name = source["name"]
        try:
            fetcher = FETCHERS[source["type"]]
            events = fetcher(source)
            for ev in events:
                ev["trusted"] = bool(source.get("trusted"))
            logger.info("ПАРСИНГ ✔ %s: %d событий", name, len(events))
            all_events.extend(events)
        except Exception as e:  # noqa: BLE001 — источник не должен валить пайплайн
            logger.warning("ПАРСИНГ ✘ %s: %s (источник пропущен)", name, e)
    logger.info("Парсинг завершён: всего %d сырых событий", len(all_events))
    return all_events


# ------------------------------------------------------------------
# Фейковые данные для демонстрации (python main.py test)
# ------------------------------------------------------------------

FAKE_EVENTS = [
    {
        "external_id": "fake-001",
        "title": "Ночь техно в «Мутаборе»: сет A.Fruit",
        "venue": "Мутабор, Артплей",
        "date": "2026-10-10",
        "time": "23:00",
        "price": "вход свободный до 22:00, после — 1500₽",
        "description": (
            "Футуристичное техно во дворе «Армы». Визуалы от студии Tundra, "
            "три танцпола, гардероб бесплатный. Вечеринка 18+."
        ),
        "link": "https://example.com/mutabor-afruit",
        "source": "FakeSource",
        "image_url": None,
    },
    {
        "external_id": "fake-002",
        "title": "Выставка «Цифровые сны» в ЦСИ «Гараж»",
        "venue": "Центр современного искусства «Гараж», Крымский Вал, 9",
        "date": "2026-11-15",
        "time": "12:00",
        "price": "бесплатно по регистрации",
        "description": (
            "Работы 12 молодых художников, работающих с нейросетевыми "
            "технологиями. Интерактивные инсталляции, генеративное искусство."
        ),
        "link": "https://example.com/garage-digital-dreams",
        "source": "FakeSource",
        "image_url": None,
    },
    {
        "external_id": "fake-003",
        "title": "Скидки 50% на франшизу кофеен — инвестируй в кофе",
        "venue": None,
        "date": None,
        "time": None,
        "price": None,
        "description": "Реклама франшизы, промокод INSIDE50, розыгрыш для подписчиков.",
        "link": "https://example.com/spam",
        "source": "FakeSource",
        "image_url": None,
    },
]


def fetch_fake() -> list[dict]:
    logger.info("ТЕСТОВЫЙ РЕЖИМ: %d фейковых событий", len(FAKE_EVENTS))
    return list(FAKE_EVENTS)
