"""Contexte partagé par les services : config, base de données, données de jeu."""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from ..gamedata import GameData


@dataclass
class AppContext:
    cfg: object
    db: Database
    data: GameData
