"""Statistiques des joueurs, missions quotidiennes/hebdomadaires et succès."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import Optional

from .. import timeutil
from .context import AppContext
from .players import credit_sync, ensure_sync, merge_rewards

# Événement -> compteur incrémenté (pour les succès)
EVENT_STATS = {
    "summon": "summons",
    "three_star": "three_star_pulled",
    "wordle_win": "wordle_wins",
    "duel_win": "duel_wins",
    "boss_participation": "boss_participations",
    "boss_kill": "boss_kills",
    "gift": "gifts_sent",
}


@dataclass
class MissionState:
    mission: dict
    period: str
    weekly: bool
    progress: int
    claimed: bool

    @property
    def done(self) -> bool:
        return self.progress >= int(self.mission["target"])


def _sample(pool: list, count: int, seed: str) -> list:
    pool = list(pool)
    if count >= len(pool):
        return pool
    return random.Random(seed).sample(pool, count)


def check_achievements_sync(conn: sqlite3.Connection, cfg, user_id: int, stat: str, value: int) -> list[dict]:
    """Débloque les succès liés à `stat` atteints. Crédite les récompenses."""
    unlocked: list[dict] = []
    for ach in cfg.achievements:
        if ach["stat"] != stat or value < int(ach["target"]):
            continue
        cur = conn.execute("INSERT OR IGNORE INTO achievements (user_id, ach_id) VALUES (?, ?)", (user_id, ach["id"]))
        if cur.rowcount != 1:
            continue
        credit_sync(conn, user_id, ach.get("reward") or {})
        if ach.get("title"):
            conn.execute("INSERT OR IGNORE INTO titles (user_id, title) VALUES (?, ?)", (user_id, ach["title"]))
        unlocked.append(ach)
    return unlocked


def add_stat_sync(conn: sqlite3.Connection, user_id: int, stat: str, amount: int) -> int:
    conn.execute(
        "INSERT INTO stats (user_id, stat, value) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, stat) DO UPDATE SET value = value + excluded.value",
        (user_id, stat, amount),
    )
    return conn.execute("SELECT value FROM stats WHERE user_id = ? AND stat = ?", (user_id, stat)).fetchone()[0]


def max_stat_sync(conn: sqlite3.Connection, user_id: int, stat: str, value: int) -> int:
    conn.execute(
        "INSERT INTO stats (user_id, stat, value) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, stat) DO UPDATE SET value = MAX(value, excluded.value)",
        (user_id, stat, value),
    )
    return conn.execute("SELECT value FROM stats WHERE user_id = ? AND stat = ?", (user_id, stat)).fetchone()[0]


class Progress:
    def __init__(self, app: AppContext):
        self.app = app

    @property
    def cfg(self):
        return self.app.cfg

    # -- missions actives -----------------------------------------------------

    def daily_missions(self, day: Optional[str] = None) -> list[dict]:
        day = day or timeutil.day_key(self.cfg)
        return _sample(self.cfg.missions.daily_pool, int(self.cfg.missions.daily_count), f"daily-{day}")

    def weekly_missions(self, week: Optional[str] = None) -> list[dict]:
        week = week or timeutil.week_key(self.cfg)
        return _sample(self.cfg.missions.weekly_pool, int(self.cfg.missions.weekly_count), f"weekly-{week}")

    def _active(self) -> list[tuple[dict, str, bool]]:
        day, week = timeutil.day_key(self.cfg), timeutil.week_key(self.cfg)
        return [(m, f"d:{day}", False) for m in self.daily_missions(day)] + [
            (m, f"w:{week}", True) for m in self.weekly_missions(week)
        ]

    # -- événements -----------------------------------------------------------

    def record_sync(self, conn: sqlite3.Connection, user_id: int, event: str, amount: int = 1) -> list[dict]:
        """À appeler dans une transaction existante."""
        unlocked: list[dict] = []
        stat = EVENT_STATS.get(event)
        if stat:
            value = add_stat_sync(conn, user_id, stat, amount)
            unlocked += check_achievements_sync(conn, self.cfg, user_id, stat, value)
        for mission, period, _weekly in self._active():
            if mission["type"] != event:
                continue
            conn.execute(
                "INSERT INTO missions (user_id, period, mission_id, progress) VALUES (?, ?, ?, MIN(?, ?)) "
                "ON CONFLICT(user_id, period, mission_id) DO UPDATE SET progress = MIN(progress + ?, ?)",
                (user_id, period, mission["id"], amount, int(mission["target"]), amount, int(mission["target"])),
            )
        return unlocked

    async def record(self, user_id: int, event: str, amount: int = 1) -> list[dict]:
        def _tx(conn):
            ensure_sync(conn, self.cfg, user_id)
            return self.record_sync(conn, user_id, event, amount)
        return await self.app.db.transaction(_tx)

    def set_max_sync(self, conn: sqlite3.Connection, user_id: int, stat: str, value: int) -> list[dict]:
        new_value = max_stat_sync(conn, user_id, stat, int(value))
        return check_achievements_sync(conn, self.cfg, user_id, stat, new_value)

    async def set_max(self, user_id: int, stat: str, value: int) -> list[dict]:
        def _tx(conn):
            ensure_sync(conn, self.cfg, user_id)
            return self.set_max_sync(conn, user_id, stat, value)
        return await self.app.db.transaction(_tx)

    async def stat(self, user_id: int, stat: str) -> int:
        return await self.app.db.fetchval("SELECT value FROM stats WHERE user_id = ? AND stat = ?", (user_id, stat), 0)

    # -- missions -----------------------------------------------------------

    async def missions(self, user_id: int) -> list[MissionState]:
        states: list[MissionState] = []
        for mission, period, weekly in self._active():
            row = await self.app.db.fetchone(
                "SELECT progress, claimed FROM missions WHERE user_id = ? AND period = ? AND mission_id = ?",
                (user_id, period, mission["id"]),
            )
            states.append(MissionState(mission, period, weekly, row["progress"] if row else 0, bool(row and row["claimed"])))
        return states

    async def claim_missions(self, user_id: int) -> tuple[dict, list[dict]]:
        """Récupère les récompenses des missions terminées. Retourne (total, missions)."""
        active = self._active()

        def _tx(conn):
            ensure_sync(conn, self.cfg, user_id)
            claimed: list[dict] = []
            for mission, period, _weekly in active:
                cur = conn.execute(
                    "UPDATE missions SET claimed = 1 WHERE user_id = ? AND period = ? AND mission_id = ? "
                    "AND claimed = 0 AND progress >= ?",
                    (user_id, period, mission["id"], int(mission["target"])),
                )
                if cur.rowcount == 1:
                    credit_sync(conn, user_id, mission.get("reward") or {})
                    claimed.append(mission)
            return claimed

        claimed = await self.app.db.transaction(_tx)
        return merge_rewards(*(m.get("reward") for m in claimed)), claimed

    # -- succès ---------------------------------------------------------------

    async def unlocked_achievements(self, user_id: int) -> set[str]:
        rows = await self.app.db.fetchall("SELECT ach_id FROM achievements WHERE user_id = ?", (user_id,))
        return {r["ach_id"] for r in rows}
