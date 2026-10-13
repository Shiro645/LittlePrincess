"""Outils communs aux cogs : autocomplétion, résolution des héros/armes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from ..gamedata import Hero, WeaponDef, normalize_name
from ..services.collection import GameError
from ..ui.format import ELEMENT_FR, ROLE_FR, stars

if TYPE_CHECKING:
    from ..client import LittlePrincess


def bot_of(interaction: discord.Interaction) -> "LittlePrincess":
    return interaction.client  # type: ignore[return-value]


def _hero_choice(hero: Hero, extra: str = "") -> app_commands.Choice[str]:
    label = f"{hero.name} — {stars(hero.rarity)} {ELEMENT_FR.get(hero.element, '')} {ROLE_FR.get(hero.role, '')}{extra}"
    return app_commands.Choice(name=label[:100], value=hero.key)


async def hero_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot = bot_of(interaction)
    return [_hero_choice(h) for h in bot.data.search_heroes(current, limit=25)]


async def owned_hero_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot = bot_of(interaction)
    rows = await bot.collection.heroes(interaction.user.id)
    info = {r["hero_key"]: r for r in rows}
    heroes = bot.data.search_heroes(current, keys=list(info), limit=25)
    return [_hero_choice(h, f" · {stars(info[h.key]['stars'])} niv.{info[h.key]['level']}") for h in heroes]


async def owned_weapon_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot = bot_of(interaction)
    rows = await bot.collection.weapons(interaction.user.id)
    norm = normalize_name(current)
    choices: list[app_commands.Choice[str]] = []
    for row in sorted(rows, key=lambda r: (-r["stars"], r["weapon_key"])):
        weapon = bot.data.weapon(row["weapon_key"])
        if weapon is None or (norm and norm not in normalize_name(weapon.name)):
            continue
        kind = bot.cfg.weapons.type_names.get(weapon.weapon_type, weapon.weapon_type)
        label = f"{weapon.name} — {stars(row['stars'])} niv.{row['level']} ({kind})"
        choices.append(app_commands.Choice(name=label[:100], value=weapon.key))
        if len(choices) >= 25:
            break
    return choices


def resolve_hero(bot: "LittlePrincess", value: str) -> Hero:
    hero = bot.data.find_hero(value)
    if hero is None:
        raise GameError(f"Héros introuvable : « {value} ». Utilise l'autocomplétion.")
    return hero


def resolve_weapon(bot: "LittlePrincess", value: str) -> WeaponDef:
    weapon = bot.data.weapon(value)
    if weapon is None:
        norm = normalize_name(value)
        matches = [w for w in bot.data.weapons.values() if normalize_name(w.name) == norm]
        weapon = matches[0] if matches else None
    if weapon is None:
        raise GameError(f"Arme introuvable : « {value} ». Utilise l'autocomplétion.")
    return weapon


def staff_only():
    """Décorateur : réservé aux administrateurs et au rôle staff."""

    async def predicate(interaction: discord.Interaction) -> bool:
        from ..client import is_staff
        return is_staff(bot_of(interaction).cfg, interaction.user)

    return app_commands.check(predicate)


def member_option_id(member: Optional[discord.abc.User], fallback: discord.abc.User) -> discord.abc.User:
    return member or fallback
