"""Joueurs et monnaies (gemmes, or, tickets)."""

from __future__ import annotations

import sqlite3
from typing import Optional

from .context import AppContext

CURRENCIES = ("gems", "gold", "hero_tickets", "weapon_tickets")
EMOJI = {"gems": "💎", "gold": "🪙", "hero_tickets": "🎫", "weapon_tickets": "🎟️"}
LABELS = {"gems": "gemmes", "gold": "or", "hero_tickets": "ticket(s) héros", "weapon_tickets": "ticket(s) arme"}


def fmt_num(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def format_rewards(rewards: dict, separator: str = " · ") -> str:
    parts = [f"{EMOJI[k]} {fmt_num(v)}" for k, v in rewards.items() if k in CURRENCIES and v]
    return separator.join(parts) if parts else "rien"


def merge_rewards(*items: Optional[dict]) -> dict:
    total: dict = {}
    for item in items:
        for key, value in (item or {}).items():
            if key in CURRENCIES and value:
                total[key] = total.get(key, 0) + int(value)
    return total


def ensure_sync(conn: sqlite3.Connection, cfg, user_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO players (user_id, gems, gold) VALUES (?, ?, ?)",
            (user_id, int(cfg.economy.start_gems), int(cfg.economy.start_gold)),
        )
        conn.execute("INSERT OR IGNORE INTO titles (user_id, title) VALUES (?, ?)",
                     (user_id, _first_title(cfg)))
        row = conn.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    return row


def _first_title(cfg) -> str:
    titles = {int(k): v for k, v in cfg.leveling.titles_by_level.items()}
    return titles[min(titles)] if titles else "Novice"


def credit_sync(conn: sqlite3.Connection, user_id: int, rewards: dict) -> None:
    amounts = {k: int(rewards.get(k, 0) or 0) for k in CURRENCIES}
    if not any(amounts.values()):
        return
    conn.execute(
        "UPDATE players SET gems = gems + :gems, gold = gold + :gold, hero_tickets = hero_tickets + :hero_tickets, "
        "weapon_tickets = weapon_tickets + :weapon_tickets WHERE user_id = :uid",
        {**amounts, "uid": user_id},
    )


def debit_sync(conn: sqlite3.Connection, user_id: int, costs: dict) -> bool:
    """Débite si le joueur a assez. Retourne False sinon (rien n'est débité)."""
    amounts = {k: int(costs.get(k, 0) or 0) for k in CURRENCIES}
    cur = conn.execute(
        "UPDATE players SET gems = gems - :gems, gold = gold - :gold, hero_tickets = hero_tickets - :hero_tickets, "
        "weapon_tickets = weapon_tickets - :weapon_tickets WHERE user_id = :uid AND gems >= :gems AND gold >= :gold "
        "AND hero_tickets >= :hero_tickets AND weapon_tickets >= :weapon_tickets",
        {**amounts, "uid": user_id},
    )
    return cur.rowcount == 1


class Players:
    def __init__(self, app: AppContext):
        self.app = app

    async def get(self, user_id: int) -> sqlite3.Row:
        return await self.app.db.transaction(lambda conn: ensure_sync(conn, self.app.cfg, user_id))

    async def find(self, user_id: int) -> Optional[sqlite3.Row]:
        return await self.app.db.fetchone("SELECT * FROM players WHERE user_id = ?", (user_id,))

    async def credit(self, user_id: int, rewards: dict) -> None:
        def _tx(conn):
            ensure_sync(conn, self.app.cfg, user_id)
            credit_sync(conn, user_id, rewards)
        await self.app.db.transaction(_tx)

    async def debit(self, user_id: int, costs: dict) -> bool:
        def _tx(conn):
            ensure_sync(conn, self.app.cfg, user_id)
            return debit_sync(conn, user_id, costs)
        return await self.app.db.transaction(_tx)

    async def set_title(self, user_id: int, title: Optional[str]) -> None:
        await self.app.db.execute("UPDATE players SET title = ? WHERE user_id = ?", (title, user_id))

    async def titles(self, user_id: int) -> list[str]:
        rows = await self.app.db.fetchall("SELECT title FROM titles WHERE user_id = ? ORDER BY title", (user_id,))
        return [r["title"] for r in rows]
