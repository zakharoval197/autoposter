"""
database.py — SQLite-хранилище событий канала «Куда сходить в Москве».

Таблица events — основа будущей базы заведений:
поле `markers` (JSON) зарезервировано под маркеры заведений
(шум, плотность, вайб, движняк в будни) из задачи №5 брифа.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger("autoposter.db")

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "events.db"


def resolve_db_path() -> Path:
    """DB_PATH из .env может быть относительным (data/events.db) —
    якорим на папку проекта. Иначе при запуске из планировщика задач
    (рабочая папка system32) база создастся в C:\\Windows\\System32."""
    p = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH)))
    return p if p.is_absolute() else Path(__file__).parent / p

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id   TEXT UNIQUE NOT NULL,      -- хеш источник+ссылка, защита от дублей
    title         TEXT NOT NULL,
    venue         TEXT,
    event_date    TEXT,
    event_time    TEXT,
    price         TEXT,
    description   TEXT,
    link          TEXT,
    source        TEXT,                       -- имя источника (RSS/сайт)
    image_url     TEXT,                       -- og:image или постер площадки
    image_path    TEXT,                       -- локальный файл после обработки
    image_source  TEXT,                       -- poster | generated | fallback
    post_text     TEXT,                       -- сгенерированный черновик
    writer        TEXT,                       -- кто написал пост: kimi | gigachat | mock
    status        TEXT NOT NULL DEFAULT 'new',
    -- new -> filtered | draft -> approved | skipped -> published
    markers       TEXT,                       -- JSON-маркеры заведений (будущее)
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    published_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_date   ON events(event_date);
"""


class Database:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # миграция 06.10: колонка writer для баз, созданных раньше
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(events)")}
        if "writer" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN writer TEXT")
            logger.info("Миграция: добавлена колонка writer")
        self.conn.commit()
        logger.info("База открыта: %s", self.db_path)

    # ---- запись ----

    def add_event(self, event: dict) -> int | None:
        """Добавляет событие. Возвращает id или None, если дубль."""
        markers = event.get("markers")
        try:
            cur = self.conn.execute(
                """INSERT INTO events
                   (external_id, title, venue, event_date, event_time, price,
                    description, link, source, image_url, image_path,
                    image_source, post_text, status, markers)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event["external_id"],
                    event["title"],
                    event.get("venue"),
                    event.get("date"),
                    event.get("time"),
                    event.get("price"),
                    event.get("description"),
                    event.get("link"),
                    event.get("source"),
                    event.get("image_url"),
                    event.get("image_path"),
                    event.get("image_source"),
                    event.get("post_text"),
                    event.get("status", "new"),
                    json.dumps(markers, ensure_ascii=False) if markers else None,
                ),
            )
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            logger.debug("Дубль пропущен: %s", event.get("title"))
            return None

    def update_event(self, event_id: int, **fields) -> None:
        allowed = {
            "title", "venue", "event_date", "event_time", "price",
            "description", "link", "source", "image_url", "image_path",
            "image_source", "post_text", "status", "markers", "published_at",
            "writer",
        }
        sets, vals = [], []
        for key, value in fields.items():
            if key in allowed:
                if key == "markers" and not isinstance(value, (str, type(None))):
                    value = json.dumps(value, ensure_ascii=False)
                sets.append(f"{key} = ?")
                vals.append(value)
        if not sets:
            return
        vals.append(event_id)
        self.conn.execute(
            f"UPDATE events SET {', '.join(sets)} WHERE id = ?", vals
        )
        self.conn.commit()

    # ---- чтение ----

    def get_event(self, event_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_status(self, status: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE status = ? ORDER BY id LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def exists(self, external_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM events WHERE external_id = ?", (external_id,)
        ).fetchone()
        return row is not None

    def stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) c FROM events GROUP BY status"
        ).fetchall()
        return {r["status"]: r["c"] for r in rows}

    def close(self):
        self.conn.close()
