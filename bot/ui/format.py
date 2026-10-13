"""Libellés, emojis et embeds communs."""

from __future__ import annotations

from typing import Optional

import discord

from ..gamedata import Hero, WeaponDef
from ..services.players import fmt_num

ELEMENT_EMOJI = {"basic": "⚪", "fire": "🔥", "water": "💧", "earth": "🌿", "light": "☀️", "dark": "🌑"}
ELEMENT_FR = {"basic": "Basique", "fire": "Feu", "water": "Eau", "earth": "Terre", "light": "Lumière", "dark": "Ténèbres"}
ROLE_EMOJI = {"warrior": "⚔️", "tank": "🛡️", "ranged": "🏹", "support": "💚"}
ROLE_FR = {"warrior": "Guerrier", "tank": "Tank", "ranged": "Distance", "support": "Soutien"}
STATE_FR = {"downed": "À terre", "airborne": "Projeté", "injured": "Blessé", None: "—"}
RARITY_COLORS = {1: 0x9E9E9E, 2: 0x42A5F5, 3: 0xFFC107, 4: 0xAB47BC, 5: 0xFF7043}

ATTRIBUTION = "Données : Guardian Tales TOP • gtales.top"


def color(cfg) -> discord.Color:
    try:
        return discord.Color(int(str(cfg.general.embed_color).lstrip("#"), 16))
    except ValueError:
        return discord.Color.blurple()


def embed(cfg, title: str = "", description: str = "", colour: Optional[int] = None) -> discord.Embed:
    return discord.Embed(title=title or None, description=description or None,
                         color=discord.Color(colour) if colour is not None else color(cfg))


def stars(n: int) -> str:
    return "★" * max(0, int(n))


def hero_label(hero: Hero) -> str:
    return f"{ELEMENT_EMOJI.get(hero.element, '')}{ROLE_EMOJI.get(hero.role, '')} {hero.name}"


def hero_line(hero: Hero, star_count: Optional[int] = None, level: Optional[int] = None) -> str:
    parts = [f"{ELEMENT_EMOJI.get(hero.element, '')}{ROLE_EMOJI.get(hero.role, '')} **{hero.name}**"]
    if star_count is not None:
        parts.append(stars(star_count))
    if level is not None:
        parts.append(f"niv. {level}")
    return " ".join(parts)


def weapon_line(cfg, weapon: WeaponDef, star_count: Optional[int] = None, level: Optional[int] = None) -> str:
    kind = cfg.weapons.type_names.get(weapon.weapon_type, weapon.weapon_type)
    excl = " ✦" if weapon.is_exclusive else ""
    parts = [f"🗡️ **{weapon.name}**{excl} ({kind})"]
    if star_count is not None:
        parts.append(stars(star_count))
    if level is not None:
        parts.append(f"niv. {level}")
    return " ".join(parts)


def progress_bar(current: float, maximum: float, width: int = 12) -> str:
    ratio = 0 if maximum <= 0 else max(0.0, min(1.0, current / maximum))
    filled = round(ratio * width)
    return "▰" * filled + "▱" * (width - filled)


def num(n: int) -> str:
    return fmt_num(n)


def chunk_lines(lines: list[str], limit: int = 3900) -> list[str]:
    """Découpe une liste de lignes en blocs de moins de `limit` caractères."""
    pages, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > limit and current:
            pages.append(current)
            current = ""
        current += line + "\n"
    if current:
        pages.append(current)
    return pages or [""]
