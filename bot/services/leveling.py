"""XP du compte (activité Discord) et récompenses de niveau."""

from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

from .. import timeutil
from ..config import int_keys
from ..game import stats
from .context import AppContext
from .players import credit_sync, ensure_sync, merge_rewards
from .progress import check_achievements_sync, max_stat_sync


@dataclass
class XpResult:
    gained: int = 0
    old_level: int = 1
    new_level: int = 1
    rewards: dict = field(default_factory=dict)
    titles: list[str] = field(default_factory=list)
    achievements: list[dict] = field(default_factory=list)

    @property
    def leveled_up(self) -> bool:
        return self.new_level > self.old_level


def level_rewards(cfg, level: int) -> dict:
    reward = dict(cfg.leveling.reward_per_level)
    every = int(cfg.leveling.milestone_every or 0)
    if every and level % every == 0:
        reward = merge_rewards(reward, cfg.leveling.milestone_reward)
    return reward


def add_xp_sync(conn: sqlite3.Connection, cfg, user_id: int, amount: int, gold: int = 0) -> XpResult:
    player = ensure_sync(conn, cfg, user_id)
    level, xp, reached = stats.apply_xp(cfg, player["level"], player["xp"], amount)
    conn.execute("UPDATE players SET level = ?, xp = ?, total_xp = total_xp + ?, gold = gold + ? WHERE user_id = ?",
                 (level, xp, amount, gold, user_id))
    result = XpResult(amount, player["level"], level)
    titles = int_keys(cfg.leveling.titles_by_level)
    for lvl in reached:
        reward = level_rewards(cfg, lvl)
        credit_sync(conn, user_id, reward)
        result.rewards = merge_rewards(result.rewards, reward)
        if lvl in titles:
            cur = conn.execute("INSERT OR IGNORE INTO titles (user_id, title) VALUES (?, ?)", (user_id, titles[lvl]))
            if cur.rowcount:
                result.titles.append(titles[lvl])
    if reached:
        value = max_stat_sync(conn, user_id, "level", level)
        result.achievements = check_achievements_sync(conn, cfg, user_id, "level", value)
    return result


class Leveling:
    def __init__(self, app: AppContext):
        self.app = app

    @property
    def cfg(self):
        return self.app.cfg

    async def add_xp(self, user_id: int, amount: int, gold: int = 0) -> XpResult:
        return await self.app.db.transaction(lambda conn: add_xp_sync(conn, self.cfg, user_id, amount, gold))

    async def on_message(self, user_id: int, rng: Optional[random.Random] = None) -> Optional[XpResult]:
        """XP d'un message, si le cooldown anti-spam est écoulé."""
        cfg = self.cfg.leveling
        now = time.time()
        rng = rng or random.Random()

        def _tx(conn):
            player = ensure_sync(conn, self.cfg, user_id)
            if now - player["last_msg_xp"] < float(cfg.message_cooldown_seconds):
                return None
            conn.execute("UPDATE players SET last_msg_xp = ? WHERE user_id = ?", (now, user_id))
            amount = rng.randint(int(cfg.message_xp_min), int(cfg.message_xp_max))
            return add_xp_sync(conn, self.cfg, user_id, amount, int(cfg.message_gold))

        return await self.app.db.transaction(_tx)

    async def on_reaction(self, user_id: int) -> Optional[XpResult]:
        cfg = self.cfg.leveling
        now = time.time()
        today = timeutil.day_key(self.cfg)

        def _tx(conn):
            player = ensure_sync(conn, self.cfg, user_id)
            if now - player["last_react_xp"] < float(cfg.reaction_cooldown_seconds):
                return None
            earned_today = player["react_xp_today"] if player["react_day"] == today else 0
            amount = min(int(cfg.reaction_xp), int(cfg.reaction_daily_cap) - earned_today)
            if amount <= 0:
                return None
            conn.execute("UPDATE players SET last_react_xp = ?, react_day = ?, react_xp_today = ? WHERE user_id = ?",
                         (now, today, earned_today + amount, user_id))
            return add_xp_sync(conn, self.cfg, user_id, amount)

        return await self.app.db.transaction(_tx)
