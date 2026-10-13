"""Logique pure des invocations (sans base de données ni Discord)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..config import int_keys
from ..gamedata import GameData

HERO = "hero"
WEAPON = "weapon"


@dataclass
class Banner:
    id: str                 # standard | featured | weapon
    kind: str               # hero | weapon
    name: str
    featured_hero: Optional[str] = None     # clé du héros vedette
    featured_weapon: Optional[str] = None   # clé de l'arme vedette


@dataclass
class Pull:
    kind: str
    key: str
    rarity: int
    featured: bool = False
    pity: bool = False


def week_start(now: datetime, weekday: int, hour: int) -> datetime:
    """Début de la « semaine de bannière » contenant `now`."""
    anchor = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    delta_days = (now.weekday() - weekday) % 7
    start = anchor - timedelta(days=delta_days)
    if start > now:
        start -= timedelta(days=7)
    return start


def featured_hero_key(cfg, data: GameData, now: datetime) -> Optional[str]:
    """Héros vedette de la semaine (rotation de la config, sinon tirage déterministe)."""
    start = week_start(now, int(cfg.gacha.featured_change_weekday), int(cfg.gacha.featured_change_hour))
    week_index = (start.toordinal() // 7)
    rotation = [str(k) for k in cfg.gacha.get("featured_rotation", []) if str(k) in data.heroes]
    if rotation:
        return rotation[week_index % len(rotation)]
    pool = data.heroes_by_rarity(3)
    if not pool:
        return None
    return random.Random(f"featured-{week_index}").choice(pool).key


def banners(cfg, data: GameData, now: datetime) -> dict[str, Banner]:
    featured = featured_hero_key(cfg, data, now)
    featured_weapon = None
    if featured:
        hero = data.hero(featured)
        if hero and hero.exclusive_weapon and hero.exclusive_weapon in data.weapons:
            featured_weapon = hero.exclusive_weapon
    featured_name = data.hero(featured).name if featured else "?"
    return {
        "standard": Banner("standard", HERO, "Bannière standard"),
        "featured": Banner("featured", HERO, f"Vedette : {featured_name}", featured_hero=featured),
        "weapon": Banner("weapon", WEAPON, "Bannière d'armes", featured_weapon=featured_weapon),
    }


def _roll_rarity(rng: random.Random, rates: dict[int, float]) -> int:
    total = sum(rates.values())
    roll = rng.uniform(0, total)
    acc = 0.0
    for rarity in sorted(rates, reverse=True):
        acc += rates[rarity]
        if roll < acc:
            return rarity
    return min(rates)


def roll(
    cfg,
    data: GameData,
    banner: Banner,
    count: int,
    pity_counter: int,
    rng: Optional[random.Random] = None,
) -> tuple[list[Pull], int]:
    """Effectue `count` invocations. Retourne les tirages et le nouveau compteur de pitié."""
    rng = rng or random.Random()
    is_hero = banner.kind == HERO
    rates = int_keys(cfg.gacha.hero_rates if is_hero else cfg.gacha.weapon_rates)
    top = max(rates)
    pity_limit = int(cfg.gacha.pity_hero if is_hero else cfg.gacha.pity_weapon)
    min_ten = int(cfg.gacha.ten_pull_min_hero_rarity if is_hero else cfg.gacha.ten_pull_min_weapon_rarity)
    featured_share = float(cfg.gacha.featured_share)

    pulls: list[Pull] = []
    for i in range(count):
        rarity = _roll_rarity(rng, rates)
        pity = False
        if rarity < top and pity_limit > 0 and pity_counter + 1 >= pity_limit:
            rarity, pity = top, True
        # Garantie du x10 : le dernier tirage remonte à la rareté minimale
        if count >= 10 and i == count - 1 and min_ten and all(p.rarity < min_ten for p in pulls) and rarity < min_ten:
            rarity = min_ten
        pity_counter = 0 if rarity == top else pity_counter + 1
        pulls.append(_pick(data, banner, rarity, featured_share, rng, pity))
    return pulls, pity_counter


def _pick(data: GameData, banner: Banner, rarity: int, featured_share: float, rng: random.Random, pity: bool) -> Pull:
    if banner.kind == HERO:
        pool = data.heroes_by_rarity(rarity)
        while not pool and rarity > 1:
            rarity -= 1
            pool = data.heroes_by_rarity(rarity)
        if rarity == 3 and banner.featured_hero and rng.random() < featured_share:
            return Pull(HERO, banner.featured_hero, 3, featured=True, pity=pity)
        hero = rng.choice(pool)
        return Pull(HERO, hero.key, hero.rarity, featured=hero.key == banner.featured_hero, pity=pity)
    pool = data.weapons_by_rarity(rarity)
    while not pool and rarity > 1:
        rarity -= 1
        pool = data.weapons_by_rarity(rarity)
    if rarity == 5 and banner.featured_weapon and rng.random() < featured_share:
        return Pull(WEAPON, banner.featured_weapon, 5, featured=True, pity=pity)
    weapon = rng.choice(pool)
    return Pull(WEAPON, weapon.key, weapon.rarity, featured=weapon.key == banner.featured_weapon, pity=pity)
