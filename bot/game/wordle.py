"""Wordle de héros façon Loldle : comparaison des caractéristiques."""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..gamedata import GameData, Hero

OK, NO, UP, DOWN = "ok", "no", "up", "down"

ATTRIBUTES = (
    ("element", "Élément"),
    ("role", "Rôle"),
    ("rarity", "Rareté"),
    ("weapon_type", "Arme"),
    ("chain_start", "Chaîne (début)"),
    ("chain_end", "Chaîne (fin)"),
)


@dataclass
class Clue:
    attribute: str
    label: str
    value: object
    status: str


def compare(guess: Hero, answer: Hero) -> list[Clue]:
    clues = []
    for attr, label in ATTRIBUTES:
        g, a = getattr(guess, attr), getattr(answer, attr)
        if g == a:
            status = OK
        elif attr == "rarity":
            status = UP if a > g else DOWN
        else:
            status = NO
        clues.append(Clue(attr, label, g, status))
    return clues


def pick_answer(data: GameData, day: str, salt: str) -> str:
    keys = sorted(data.heroes)
    if not keys:
        raise ValueError("Aucun héros chargé")
    return random.Random(f"{salt}-{day}").choice(keys)
