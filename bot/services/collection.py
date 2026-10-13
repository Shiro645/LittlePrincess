"""Collection des joueurs : héros, armes, équipe, améliorations."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from ..game import stats
from ..game.gacha import HERO, Pull
from ..game.teams import OwnedUnit
from .context import AppContext
from .players import credit_sync, debit_sync, ensure_sync

log = logging.getLogger(__name__)


class GameError(Exception):
    """Erreur affichable telle quelle au joueur."""


@dataclass
class PullOutcome:
    pull: Pull
    status: str                  # new | star_up | converted
    stars: int
    converted: dict = field(default_factory=dict)


@dataclass
class LevelUpResult:
    old_level: int
    new_level: int
    cost: int
    cap: int


def add_pull_sync(conn: sqlite3.Connection, cfg, user_id: int, pull: Pull, base_rarity: int) -> PullOutcome:
    star_max = int(cfg.progression.star_max)
    table, key_col = ("heroes", "hero_key") if pull.kind == HERO else ("weapons", "weapon_key")
    row = conn.execute(f"SELECT stars FROM {table} WHERE user_id = ? AND {key_col} = ?", (user_id, pull.key)).fetchone()
    if row is None:
        conn.execute(f"INSERT INTO {table} (user_id, {key_col}, stars) VALUES (?, ?, ?)",
                     (user_id, pull.key, min(base_rarity, star_max)))
        return PullOutcome(pull, "new", min(base_rarity, star_max))
    if row["stars"] < star_max:
        conn.execute(f"UPDATE {table} SET stars = stars + 1 WHERE user_id = ? AND {key_col} = ?", (user_id, pull.key))
        return PullOutcome(pull, "star_up", row["stars"] + 1)
    conversion = cfg.gacha.duplicate_conversion.hero if pull.kind == HERO else cfg.gacha.duplicate_conversion.weapon
    reward = dict(conversion.get(base_rarity) or conversion.get(str(base_rarity)) or {})
    credit_sync(conn, user_id, reward)
    return PullOutcome(pull, "converted", row["stars"], reward)


class Collection:
    def __init__(self, app: AppContext):
        self.app = app

    @property
    def cfg(self):
        return self.app.cfg

    @property
    def data(self):
        return self.app.data

    # -- lecture ------------------------------------------------------------

    async def heroes(self, user_id: int) -> list[sqlite3.Row]:
        rows = await self.app.db.fetchall("SELECT * FROM heroes WHERE user_id = ?", (user_id,))
        return [r for r in rows if r["hero_key"] in self.data.heroes]

    async def hero(self, user_id: int, hero_key: str) -> Optional[sqlite3.Row]:
        return await self.app.db.fetchone("SELECT * FROM heroes WHERE user_id = ? AND hero_key = ?", (user_id, hero_key))

    async def weapons(self, user_id: int) -> list[sqlite3.Row]:
        rows = await self.app.db.fetchall("SELECT * FROM weapons WHERE user_id = ?", (user_id,))
        return [r for r in rows if r["weapon_key"] in self.data.weapons]

    async def weapon(self, user_id: int, weapon_key: str) -> Optional[sqlite3.Row]:
        return await self.app.db.fetchone("SELECT * FROM weapons WHERE user_id = ? AND weapon_key = ?",
                                          (user_id, weapon_key))

    async def hero_count(self, user_id: int) -> int:
        return len(await self.heroes(user_id))

    async def unit(self, user_id: int, hero_key: str) -> Optional[OwnedUnit]:
        units = await self.units(user_id, [hero_key])
        return units[0] if units else None

    async def units(self, user_id: int, hero_keys: list[str]) -> list[OwnedUnit]:
        units: list[OwnedUnit] = []
        for key in hero_keys:
            hero = self.data.hero(key)
            row = await self.hero(user_id, key)
            if hero is None or row is None:
                continue
            unit = OwnedUnit(hero, row["level"], row["stars"])
            if row["weapon_key"]:
                wrow = await self.weapon(user_id, row["weapon_key"])
                wdef = self.data.weapon(row["weapon_key"])
                if wrow and wdef:
                    unit.weapon, unit.weapon_level, unit.weapon_stars = wdef, wrow["level"], wrow["stars"]
            units.append(unit)
        return units

    # -- équipe -------------------------------------------------------------

    async def team_keys(self, user_id: int) -> list[str]:
        rows = await self.app.db.fetchall("SELECT hero_key FROM team WHERE user_id = ? ORDER BY slot", (user_id,))
        return [r["hero_key"] for r in rows if r["hero_key"] in self.data.heroes]

    async def team_units(self, user_id: int) -> list[OwnedUnit]:
        return await self.units(user_id, await self.team_keys(user_id))

    async def set_team(self, user_id: int, hero_keys: list[str]) -> None:
        size = int(self.cfg.combat.team_size)
        keys = [k for k in hero_keys if k]
        if not keys:
            raise GameError("Il faut au moins un héros dans l'équipe.")
        if len(keys) > size:
            raise GameError(f"Une équipe compte au maximum {size} héros.")
        if len(set(keys)) != len(keys):
            raise GameError("Un même héros ne peut pas apparaître deux fois dans l'équipe.")
        for key in keys:
            if await self.hero(user_id, key) is None:
                name = self.data.hero(key).name if self.data.hero(key) else key
                raise GameError(f"Tu ne possèdes pas **{name}**.")

        def _tx(conn):
            conn.execute("DELETE FROM team WHERE user_id = ?", (user_id,))
            conn.executemany("INSERT INTO team (user_id, slot, hero_key) VALUES (?, ?, ?)",
                             [(user_id, i, k) for i, k in enumerate(keys)])
        await self.app.db.transaction(_tx)

    # -- améliorations --------------------------------------------------------

    async def level_up(self, user_id: int, kind: str, key: str, wanted: int) -> LevelUpResult:
        """Monte un héros (kind='hero') ou une arme (kind='weapon') de `wanted` niveaux (selon l'or)."""
        table, key_col = ("heroes", "hero_key") if kind == "hero" else ("weapons", "weapon_key")

        def _tx(conn):
            player = ensure_sync(conn, self.cfg, user_id)
            row = conn.execute(f"SELECT level, stars FROM {table} WHERE user_id = ? AND {key_col} = ?",
                               (user_id, key)).fetchone()
            if row is None:
                raise GameError("Tu ne possèdes pas cet élément.")
            cap = stats.max_level(self.cfg, row["stars"])
            if row["level"] >= cap:
                raise GameError(f"Niveau maximum atteint pour {row['stars']}★ (niv. {cap}). "
                                "Obtiens un doublon pour gagner une étoile.")
            levels, cost = stats.affordable_levels(self.cfg, row["level"], cap, player["gold"], wanted, kind)
            if levels == 0:
                need = stats.level_cost(self.cfg, row["level"], kind)
                raise GameError(f"Pas assez d'or : il faut 🪙 {need:,} pour le prochain niveau.".replace(",", " "))
            if not debit_sync(conn, user_id, {"gold": cost}):
                raise GameError("Pas assez d'or.")
            conn.execute(f"UPDATE {table} SET level = level + ? WHERE user_id = ? AND {key_col} = ?",
                         (levels, user_id, key))
            return LevelUpResult(row["level"], row["level"] + levels, cost, cap)

        return await self.app.db.transaction(_tx)

    async def equip(self, user_id: int, hero_key: str, weapon_key: str) -> Optional[str]:
        """Équipe une arme. Retourne le nom du héros qui la portait avant (s'il y en a un)."""
        hero = self.data.hero(hero_key)
        weapon = self.data.weapon(weapon_key)
        if hero is None or weapon is None:
            raise GameError("Héros ou arme inconnu.")
        if weapon.weapon_type != hero.weapon_type:
            names = self.cfg.weapons.type_names
            raise GameError(f"**{hero.name}** utilise : {names.get(hero.weapon_type, hero.weapon_type)}. "
                            f"Cette arme est de type {names.get(weapon.weapon_type, weapon.weapon_type)}.")

        def _tx(conn):
            if conn.execute("SELECT 1 FROM heroes WHERE user_id = ? AND hero_key = ?", (user_id, hero_key)).fetchone() is None:
                raise GameError(f"Tu ne possèdes pas **{hero.name}**.")
            if conn.execute("SELECT 1 FROM weapons WHERE user_id = ? AND weapon_key = ?", (user_id, weapon_key)).fetchone() is None:
                raise GameError(f"Tu ne possèdes pas **{weapon.name}**.")
            previous = conn.execute(
                "SELECT hero_key FROM heroes WHERE user_id = ? AND weapon_key = ? AND hero_key != ?",
                (user_id, weapon_key, hero_key),
            ).fetchone()
            conn.execute("UPDATE heroes SET weapon_key = NULL WHERE user_id = ? AND weapon_key = ?", (user_id, weapon_key))
            conn.execute("UPDATE heroes SET weapon_key = ? WHERE user_id = ? AND hero_key = ?",
                         (weapon_key, user_id, hero_key))
            return previous["hero_key"] if previous else None

        previous_key = await self.app.db.transaction(_tx)
        if previous_key and self.data.hero(previous_key):
            return self.data.hero(previous_key).name
        return None

    async def unequip(self, user_id: int, hero_key: str) -> None:
        await self.app.db.execute("UPDATE heroes SET weapon_key = NULL WHERE user_id = ? AND hero_key = ?",
                                  (user_id, hero_key))

    def star_up_sync(self, conn: sqlite3.Connection, user_id: int, hero_key: str) -> int:
        row = conn.execute("SELECT stars FROM heroes WHERE user_id = ? AND hero_key = ?", (user_id, hero_key)).fetchone()
        if row is None:
            raise GameError("Tu ne possèdes pas ce héros.")
        if row["stars"] >= int(self.cfg.progression.star_max):
            raise GameError("Ce héros a déjà le maximum d'étoiles.")
        conn.execute("UPDATE heroes SET stars = stars + 1 WHERE user_id = ? AND hero_key = ?", (user_id, hero_key))
        return row["stars"] + 1

    # -- migration des héros de démarrage --------------------------------------

    async def migrate_seed_heroes(self) -> int:
        """Après la 1re synchro API : remplace les héros de démarrage par les vrais (même nom)."""
        hero_rows = await self.app.db.fetchall("SELECT * FROM heroes WHERE hero_key LIKE 'seed-%'")
        weapon_rows = await self.app.db.fetchall("SELECT * FROM weapons WHERE weapon_key LIKE 'ex-seed-%'")
        if not hero_rows and not weapon_rows:
            return 0
        resolve = self.data.resolve_seed_key

        def _tx(conn):
            moved = 0
            for row in hero_rows:
                uid, old = row["user_id"], row["hero_key"]
                new = resolve(old)
                if not new:
                    continue
                existing = conn.execute("SELECT 1 FROM heroes WHERE user_id = ? AND hero_key = ?", (uid, new)).fetchone()
                if existing:
                    conn.execute("UPDATE heroes SET stars = MAX(stars, ?), level = MAX(level, ?) "
                                 "WHERE user_id = ? AND hero_key = ?", (row["stars"], row["level"], uid, new))
                    conn.execute("DELETE FROM heroes WHERE user_id = ? AND hero_key = ?", (uid, old))
                else:
                    conn.execute("UPDATE heroes SET hero_key = ? WHERE user_id = ? AND hero_key = ?", (new, uid, old))
                if conn.execute("SELECT 1 FROM team WHERE user_id = ? AND hero_key = ?", (uid, new)).fetchone():
                    conn.execute("DELETE FROM team WHERE user_id = ? AND hero_key = ?", (uid, old))
                else:
                    conn.execute("UPDATE team SET hero_key = ? WHERE user_id = ? AND hero_key = ?", (new, uid, old))
                moved += 1
            for row in weapon_rows:
                uid, old = row["user_id"], row["weapon_key"]
                hero_key = resolve(old[len("ex-"):])
                new = f"ex-{hero_key}" if hero_key else None
                if not new or new not in self.data.weapons:
                    continue
                conn.execute("DELETE FROM weapons WHERE user_id = ? AND weapon_key = ?", (uid, old))
                conn.execute("INSERT INTO weapons (user_id, weapon_key, stars, level) VALUES (?, ?, ?, ?) "
                             "ON CONFLICT(user_id, weapon_key) DO UPDATE SET stars = MAX(stars, excluded.stars), "
                             "level = MAX(level, excluded.level)", (uid, new, row["stars"], row["level"]))
                conn.execute("UPDATE heroes SET weapon_key = ? WHERE user_id = ? AND weapon_key = ?", (new, uid, old))
            return moved

        moved = await self.app.db.transaction(_tx)
        log.info("%d héros de démarrage migrés vers les données de l'API", moved)
        return moved
