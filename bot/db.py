"""Base SQLite asynchrone.

Toutes les requêtes passent par un unique thread dédié : les opérations sont
donc sérialisées, et `transaction()` exécute une fonction complète de manière
atomique (BEGIN … COMMIT), sans risque de double dépense de gemmes.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    user_id         INTEGER PRIMARY KEY,
    gems            INTEGER NOT NULL DEFAULT 0,
    gold            INTEGER NOT NULL DEFAULT 0,
    hero_tickets    INTEGER NOT NULL DEFAULT 0,
    weapon_tickets  INTEGER NOT NULL DEFAULT 0,
    level           INTEGER NOT NULL DEFAULT 1,
    xp              INTEGER NOT NULL DEFAULT 0,
    total_xp        INTEGER NOT NULL DEFAULT 0,
    title           TEXT,
    pity_hero       INTEGER NOT NULL DEFAULT 0,
    pity_weapon     INTEGER NOT NULL DEFAULT 0,
    daily_last      TEXT,
    daily_streak    INTEGER NOT NULL DEFAULT 0,
    last_msg_xp     REAL NOT NULL DEFAULT 0,
    last_react_xp   REAL NOT NULL DEFAULT 0,
    react_day       TEXT,
    react_xp_today  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS heroes (
    user_id     INTEGER NOT NULL,
    hero_key    TEXT NOT NULL,
    stars       INTEGER NOT NULL,
    level       INTEGER NOT NULL DEFAULT 1,
    weapon_key  TEXT,
    obtained_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, hero_key)
);

CREATE TABLE IF NOT EXISTS weapons (
    user_id     INTEGER NOT NULL,
    weapon_key  TEXT NOT NULL,
    stars       INTEGER NOT NULL,
    level       INTEGER NOT NULL DEFAULT 1,
    obtained_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, weapon_key)
);

CREATE TABLE IF NOT EXISTS team (
    user_id  INTEGER NOT NULL,
    slot     INTEGER NOT NULL,
    hero_key TEXT NOT NULL,
    PRIMARY KEY (user_id, slot)
);

CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER NOT NULL,
    stat    TEXT NOT NULL,
    value   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, stat)
);

CREATE TABLE IF NOT EXISTS missions (
    user_id    INTEGER NOT NULL,
    period     TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    progress   INTEGER NOT NULL DEFAULT 0,
    claimed    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, period, mission_id)
);

CREATE TABLE IF NOT EXISTS achievements (
    user_id     INTEGER NOT NULL,
    ach_id      TEXT NOT NULL,
    unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, ach_id)
);

CREATE TABLE IF NOT EXISTS titles (
    user_id INTEGER NOT NULL,
    title   TEXT NOT NULL,
    PRIMARY KEY (user_id, title)
);

CREATE TABLE IF NOT EXISTS wordle (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    guesses TEXT NOT NULL DEFAULT '[]',
    solved  INTEGER NOT NULL DEFAULT 0,
    rewarded INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS shop_purchases (
    user_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    period  TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, item_id, period)
);

CREATE TABLE IF NOT EXISTS gifts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sender   INTEGER NOT NULL,
    receiver INTEGER NOT NULL,
    currency TEXT NOT NULL,
    amount   INTEGER NOT NULL,
    day      TEXT NOT NULL,
    at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS duels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    challenger INTEGER NOT NULL,
    opponent   INTEGER NOT NULL,
    bet        INTEGER NOT NULL,
    winner     INTEGER,
    day        TEXT NOT NULL,
    at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS boss_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    week       TEXT NOT NULL UNIQUE,
    boss_id    TEXT NOT NULL,
    hp_max     INTEGER NOT NULL DEFAULT 0,
    hp         INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL,
    killed     INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    solo_end   TEXT NOT NULL,
    channel_id INTEGER,
    rewarded   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS boss_damage (
    event_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    damage   INTEGER NOT NULL DEFAULT 0,
    mode     TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS dummy_scores (
    user_id INTEGER PRIMARY KEY,
    best    INTEGER NOT NULL,
    details TEXT,
    at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        self._conn: Optional[sqlite3.Connection] = None

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    # -- cycle de vie -------------------------------------------------------

    async def connect(self) -> None:
        def _connect() -> None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current < SCHEMA_VERSION:
                # Les futures migrations iront ici (if current < 2: ...)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn = conn

        await self._run(_connect)
        log.info("Base de données ouverte : %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            conn = self._conn
            self._conn = None
            await self._run(conn.close)
        self._executor.shutdown(wait=False)

    # -- requêtes -----------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Base de données non connectée")
        return self._conn

    async def execute(self, sql: str, params: tuple | dict = ()) -> int:
        """Exécute une requête, retourne le nombre de lignes modifiées."""
        return await self._run(lambda: self.conn.execute(sql, params).rowcount)

    async def insert(self, sql: str, params: tuple | dict = ()) -> int:
        return await self._run(lambda: self.conn.execute(sql, params).lastrowid)

    async def fetchone(self, sql: str, params: tuple | dict = ()) -> Optional[sqlite3.Row]:
        return await self._run(lambda: self.conn.execute(sql, params).fetchone())

    async def fetchall(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return await self._run(lambda: self.conn.execute(sql, params).fetchall())

    async def fetchval(self, sql: str, params: tuple | dict = (), default: Any = None) -> Any:
        row = await self.fetchone(sql, params)
        return row[0] if row is not None and row[0] is not None else default

    async def transaction(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Exécute fn(conn) dans une transaction. Une exception annule tout."""

        def _tx() -> T:
            conn = self.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result

        return await self._run(_tx)

    # -- méta (clé / valeur) --------------------------------------------------

    async def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return await self.fetchval("SELECT value FROM meta WHERE key = ?", (key,), default)

    async def set_meta(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
