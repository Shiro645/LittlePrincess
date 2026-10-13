"""Simulations prêtes à l'emploi : dummy et estimation des dégâts sur un boss."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from .combat import ACTION_AUTO, Battle, Fighter, FighterSpec, make_fighter

INFINITE_HP = 10 ** 15


def make_dummy(name: str, element: Optional[str], defense: int) -> Fighter:
    spec = FighterSpec(name=name, element=element, role="tank", atk=0, hp=INFINITE_HP, defense=defense)
    return Fighter(spec=spec, side=1, max_hp=INFINITE_HP, hp=INFINITE_HP, is_dummy=True)


@dataclass
class DummyRun:
    battle: Battle
    team: list[Fighter]
    per_round: list[int]

    @property
    def total(self) -> int:
        return sum(f.total_damage for f in self.team)


def run_dummy(cfg, specs: list[FighterSpec], turns: int, element: Optional[str] = None,
              defense: Optional[int] = None, rng: Optional[random.Random] = None) -> DummyRun:
    team = [make_fighter(s, 0, float(cfg.combat.hp_multiplier.dummy)) for s in specs]
    dummy = make_dummy("Dummy", element, int(cfg.dummy.defense if defense is None else defense))
    battle = Battle(cfg, team + [dummy], rng or random.Random())
    per_round: list[int] = []
    for _ in range(turns):
        before = sum(f.total_damage for f in team)
        battle.start_round()
        for f in team:
            battle.act(f, ACTION_AUTO)
        battle.end_round()
        per_round.append(sum(f.total_damage for f in team) - before)
    return DummyRun(battle, team, per_round)


def estimate_damage(cfg, specs: list[FighterSpec], turns: int, element: Optional[str], defense: int,
                    samples: int = 3) -> int:
    """Dégâts moyens d'une équipe sur `turns` tours contre une cible (sert aux PV adaptatifs du boss)."""
    if not specs:
        return 0
    totals = [run_dummy(cfg, specs, turns, element, defense, random.Random(i)).total for i in range(samples)]
    return int(sum(totals) / len(totals))
