"""Construction des stats de combat d'une équipe à partir des héros possédés."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from ..gamedata import Effect, Hero, WeaponDef
from . import stats
from .combat import FighterSpec, skill_cd_turns


@dataclass
class OwnedUnit:
    hero: Hero
    level: int = 1
    stars: int = 1
    weapon: Optional[WeaponDef] = None
    weapon_level: int = 1
    weapon_stars: int = 1


@dataclass
class UnitStats:
    atk: int
    hp: int
    defense: int
    heal: int
    crit_chance: float
    crit_mult: float
    weapon_atk: int
    bonuses: dict


def _apply(acc: dict, effect: Effect, element: str) -> None:
    if effect.element and effect.element != element:
        return
    if effect.stat == "dmg_vs":
        acc["dmg_vs"][effect.vs_element] += effect.value
    else:
        acc[effect.stat] += effect.value


def unit_stats(cfg, unit: OwnedUnit, party: list[tuple[Effect, float]] = ()) -> UnitStats:
    """Stats d'un héros (niveau, étoiles, arme, buffs reçus)."""
    hero = unit.hero
    acc: dict = defaultdict(float)
    acc["dmg_vs"] = defaultdict(float)

    for effect in hero.self_effects:
        _apply(acc, effect, hero.element)

    weapon_atk = 0
    if unit.weapon:
        weapon_atk = stats.scale(cfg, unit.weapon.atk, unit.weapon_level, unit.weapon_stars)
        if unit.weapon.exclusive_hero == hero.key:
            for effect in unit.weapon.effects:
                _apply(acc, effect, hero.element)
            bonus = cfg.weapons.exclusive_bonus
            acc["atk"] += float(bonus.get("atk_pct", 0))
            acc["crit_chance"] += float(bonus.get("crit_chance", 0))

    for effect, factor in party:
        _apply(acc, Effect(effect.stat, effect.value * factor, effect.element, effect.vs_element), hero.element)

    base_atk = stats.scale(cfg, hero.atk, unit.level, unit.stars)
    atk = int((base_atk + weapon_atk) * (1 + acc["atk"] / 100))
    hp = int(stats.scale(cfg, hero.hp, unit.level, unit.stars) * (1 + acc["hp"] / 100))
    defense = int(stats.scale(cfg, hero.defense, unit.level, unit.stars) * (1 + acc["def"] / 100))
    heal = int(stats.scale(cfg, hero.heal, unit.level, unit.stars) * (1 + acc["heal"] / 100))
    crit_chance = float(cfg.combat.base_crit_chance) + acc["crit_chance"]
    crit_mult = float(cfg.combat.base_crit_multiplier) + acc["crit_mult"] / 100
    return UnitStats(atk, hp, defense, heal, min(crit_chance, 100.0), crit_mult, weapon_atk, acc)


def party_effects(cfg, units: list[OwnedUnit]) -> list[tuple[Effect, float]]:
    """Buffs d'équipe de tous les membres ; celui du leader (1er) est renforcé."""
    leader_mult = float(cfg.combat.leader_party_buff_multiplier)
    effects: list[tuple[Effect, float]] = []
    for index, unit in enumerate(units):
        factor = leader_mult if index == 0 else 1.0
        effects.extend((effect, factor) for effect in unit.hero.party_effects)
    return effects


def build_specs(cfg, units: list[OwnedUnit], owner_id: int = 0) -> list[FighterSpec]:
    party = party_effects(cfg, units)
    specs: list[FighterSpec] = []
    for unit in units:
        hero = unit.hero
        s = unit_stats(cfg, unit, party)
        b = s.bonuses
        role_key = "ranged_dmg" if hero.role in ("ranged", "support") else "melee_dmg"
        specs.append(FighterSpec(
            name=hero.name,
            element=hero.element,
            role=hero.role,
            atk=s.atk,
            hp=s.hp,
            defense=s.defense,
            heal=s.heal,
            crit_chance=s.crit_chance,
            crit_mult=s.crit_mult,
            skill_name=hero.skill_name or "Compétence d'arme",
            skill_mult=hero.skill_mult,
            skill_cd=skill_cd_turns(cfg, hero.skill_cd_seconds),
            skill_state=hero.skill_state,
            chain_name=hero.chain_name or "Chaîne",
            chain_start=hero.chain_start,
            chain_end=hero.chain_end,
            chain_mult=hero.chain_mult,
            dmg_pct=b["dmg"],
            skill_dmg_pct=b["skill_dmg"],
            role_dmg_pct=b[role_key],
            dmg_vs=dict(b["dmg_vs"]),
            owner_id=owner_id,
            hero_key=hero.key,
            image_url=hero.image_url,
        ))
    return specs


def power(cfg, units: list[OwnedUnit]) -> int:
    """Indicateur de puissance (pour l'affichage)."""
    party = party_effects(cfg, units)
    total = 0
    for unit in units:
        s = unit_stats(cfg, unit, party)
        total += s.atk * 4 + s.hp // 10 + s.defense * 3 + s.heal
    return total
