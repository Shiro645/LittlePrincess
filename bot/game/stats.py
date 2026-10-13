"""Calcul des stats selon le niveau et les étoiles, coûts d'amélioration, XP."""

from __future__ import annotations

from ..config import int_keys


def max_level(cfg, stars: int) -> int:
    table = int_keys(cfg.progression.max_level_by_stars)
    stars = max(min(table), min(stars, max(table)))
    return int(table[stars])


def level_factor(cfg, level: int) -> float:
    ref = int(cfg.progression.level_reference)
    lo = float(cfg.progression.level_factor_min)
    level = max(1, min(level, ref))
    if ref <= 1:
        return 1.0
    return lo + (1.0 - lo) * (level - 1) / (ref - 1)


def star_factor(cfg, stars: int) -> float:
    table = int_keys(cfg.progression.star_factor)
    stars = max(min(table), min(stars, max(table)))
    return float(table[stars])


def scale(cfg, base: float, level: int, stars: int) -> int:
    """Stat de l'API (niveau max, 5★) ramenée au niveau et aux étoiles actuels."""
    return int(round(base * level_factor(cfg, level) * star_factor(cfg, stars)))


def level_cost(cfg, level: int, kind: str = "hero") -> int:
    """Or nécessaire pour passer du niveau `level` à `level + 1`."""
    c = cfg.progression.hero_level_cost if kind == "hero" else cfg.progression.weapon_level_cost
    return int(round(c.base + c.mult * (level ** c.exponent)))


def cost_to_level(cfg, current: int, target: int, kind: str = "hero") -> int:
    return sum(level_cost(cfg, lvl, kind) for lvl in range(current, target))


def affordable_levels(cfg, current: int, cap: int, gold: int, wanted: int, kind: str = "hero") -> tuple[int, int]:
    """Nombre de niveaux qu'on peut payer (≤ wanted, ≤ cap) et leur coût."""
    levels, spent = 0, 0
    lvl = current
    while levels < wanted and lvl < cap:
        cost = level_cost(cfg, lvl, kind)
        if spent + cost > gold:
            break
        spent += cost
        lvl += 1
        levels += 1
    return levels, spent


# -- XP du compte --------------------------------------------------------------

def xp_to_next(cfg, level: int) -> int:
    c = cfg.leveling.xp_curve
    return int(c.a * level * level + c.b * level + c.c)


def apply_xp(cfg, level: int, xp: int, gained: int) -> tuple[int, int, list[int]]:
    """Ajoute de l'XP. Retourne (niveau, xp dans le niveau, niveaux atteints)."""
    xp += gained
    reached: list[int] = []
    while xp >= xp_to_next(cfg, level):
        xp -= xp_to_next(cfg, level)
        level += 1
        reached.append(level)
    return level, xp, reached
