"""Héros, armes et équipe : /heros, /arme, /team."""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..game import stats
from ..game.combat import skill_cd_turns
from ..game.teams import OwnedUnit, party_effects, power, unit_stats
from ..gamedata import Hero
from ..services.collection import GameError
from ..ui import format as ui
from ..ui.format import num
from ..ui.views import send_pages
from ._common import (bot_of, hero_autocomplete, owned_hero_autocomplete, owned_weapon_autocomplete, resolve_hero,
                      resolve_weapon)

ELEMENT_CHOICES = [app_commands.Choice(name=v, value=k) for k, v in ui.ELEMENT_FR.items()]
ROLE_CHOICES = [app_commands.Choice(name=v, value=k) for k, v in ui.ROLE_FR.items()]


def hero_embed(bot, hero: Hero, unit: Optional[OwnedUnit] = None) -> discord.Embed:
    cfg = bot.cfg
    wtype = cfg.weapons.type_names.get(hero.weapon_type, hero.weapon_type)
    desc = (f"{ui.ELEMENT_EMOJI.get(hero.element, '')} {ui.ELEMENT_FR.get(hero.element, hero.element)} · "
            f"{ui.ROLE_EMOJI.get(hero.role, '')} {ui.ROLE_FR.get(hero.role, hero.role)} · "
            f"{ui.stars(hero.rarity)} · 🗡️ {wtype}")
    e = ui.embed(cfg, hero.name, desc, colour=ui.RARITY_COLORS.get(hero.rarity))
    if hero.image_url:
        e.set_thumbnail(url=hero.image_url)
    e.add_field(name="Stats max (niv. 100, 5★)",
                value=f"⚔️ {num(hero.atk)} · ❤️ {num(hero.hp)} · 🛡️ {num(hero.defense)}"
                      + (f" · 💚 {num(hero.heal)}" if hero.heal else ""), inline=False)
    if unit:
        s = unit_stats(cfg, unit)
        weapon = f"\n🗡️ {unit.weapon.name} {ui.stars(unit.weapon_stars)} niv.{unit.weapon_level}" if unit.weapon else ""
        e.add_field(name=f"Ton héros — {ui.stars(unit.stars)} niv. {unit.level}/{stats.max_level(cfg, unit.stars)}",
                    value=f"⚔️ {num(s.atk)} · ❤️ {num(s.hp)} · 🛡️ {num(s.defense)}"
                          + (f" · 💚 {num(s.heal)}" if s.heal else "") + f" · 🎯 {s.crit_chance:.0f}%{weapon}",
                    inline=False)
    if hero.party_text:
        e.add_field(name="Buff d'équipe", value=hero.party_text[:1024], inline=False)
    if hero.skill_name:
        state = f" → {ui.STATE_FR[hero.skill_state]}" if hero.skill_state else ""
        e.add_field(name=f"✨ {hero.skill_name}",
                    value=(f"{int(hero.skill_mult * 100)} % · recharge {skill_cd_turns(cfg, hero.skill_cd_seconds)} tours"
                           f"{state}\n{hero.skill_desc[:300]}"), inline=False)
    if hero.chain_name:
        e.add_field(name=f"🔗 {hero.chain_name}",
                    value=(f"{ui.STATE_FR.get(hero.chain_start)} → {ui.STATE_FR.get(hero.chain_end)} · "
                           f"{int(hero.chain_mult * 100)} %\n{hero.chain_desc[:300]}"), inline=False)
    if hero.ability_name:
        e.add_field(name=f"🌀 {hero.ability_name}", value=hero.ability_desc[:400] or "—", inline=False)
    if hero.exclusive_weapon and bot.data.weapon(hero.exclusive_weapon):
        e.add_field(name="Arme exclusive", value=f"✦ {bot.data.weapon(hero.exclusive_weapon).name}", inline=False)
    footer = ui.ATTRIBUTION + (" • données provisoires" if hero.seed else "")
    e.set_footer(text=footer)
    return e


class Heroes(commands.Cog):
    heros = app_commands.Group(name="heros", description="Tes héros")
    arme = app_commands.Group(name="arme", description="Tes armes")
    team = app_commands.Group(name="team", description="Ton équipe (le 1er héros est le leader)")

    def __init__(self, bot):
        self.bot = bot

    # -- /heros ---------------------------------------------------------------

    @heros.command(name="liste", description="Liste des héros possédés")
    @app_commands.describe(membre="Voir la collection d'un autre membre", element="Filtrer par élément",
                           role="Filtrer par rôle")
    @app_commands.choices(element=ELEMENT_CHOICES, role=ROLE_CHOICES)
    async def heros_liste(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None,
                          element: Optional[app_commands.Choice[str]] = None,
                          role: Optional[app_commands.Choice[str]] = None) -> None:
        bot = bot_of(interaction)
        target = membre or interaction.user
        rows = await bot.collection.heroes(target.id)
        items = []
        for row in rows:
            hero = bot.data.hero(row["hero_key"])
            if element and hero.element != element.value or role and hero.role != role.value:
                continue
            items.append((row["stars"], row["level"], hero))
        items.sort(key=lambda t: (-t[0], -t[1], t[2].name))
        lines = [ui.hero_line(h, s, lvl) for s, lvl, h in items]
        title = f"🦸 Héros de {target.display_name} ({len(items)}/{len(bot.data.heroes)})"
        pages = []
        for i in range(0, max(1, len(lines)), 20):
            chunk = lines[i:i + 20]
            pages.append(ui.embed(bot.cfg, title, "\n".join(chunk) or "Aucun héros. Essaie `/invoquer` !"))
        await send_pages(interaction, pages)

    @heros.command(name="info", description="Fiche d'un héros")
    @app_commands.describe(heros="Le héros")
    @app_commands.autocomplete(heros=hero_autocomplete)
    async def heros_info(self, interaction: discord.Interaction, heros: str) -> None:
        bot = bot_of(interaction)
        hero = resolve_hero(bot, heros)
        unit = await bot.collection.unit(interaction.user.id, hero.key)
        await interaction.response.send_message(embed=hero_embed(bot, hero, unit))

    @heros.command(name="ameliorer", description="Monter le niveau d'un héros (coûte de l'or)")
    @app_commands.describe(heros="Le héros", niveaux="Nombre de niveaux (autant que ton or le permet)")
    @app_commands.autocomplete(heros=owned_hero_autocomplete)
    async def heros_ameliorer(self, interaction: discord.Interaction, heros: str,
                              niveaux: app_commands.Range[int, 1, 100] = 1) -> None:
        bot = bot_of(interaction)
        hero = resolve_hero(bot, heros)
        result = await bot.collection.level_up(interaction.user.id, "hero", hero.key, niveaux)
        gained = result.new_level - result.old_level
        unlocked = await bot.progress.record(interaction.user.id, "hero_level_up", gained)
        unlocked += await bot.progress.set_max(interaction.user.id, "max_hero_level", result.new_level)
        note = "" if gained == niveaux else f" (limité par ton or ou le niveau max {result.cap})"
        await interaction.response.send_message(
            f"⬆️ **{hero.name}** passe du niveau {result.old_level} au **niveau {result.new_level}**{note} "
            f"pour 🪙 {num(result.cost)}.")
        await bot.notify_achievements(interaction.user, unlocked, interaction.channel)

    # -- /arme ----------------------------------------------------------------

    @arme.command(name="liste", description="Liste de tes armes")
    async def arme_liste(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        rows = await bot.collection.weapons(interaction.user.id)
        heroes = await bot.collection.heroes(interaction.user.id)
        holders = {r["weapon_key"]: bot.data.hero(r["hero_key"]).name for r in heroes if r["weapon_key"]}
        items = sorted(rows, key=lambda r: (-r["stars"], -r["level"], r["weapon_key"]))
        lines = []
        for r in items:
            weapon = bot.data.weapon(r["weapon_key"])
            holder = f" — équipée par {holders[weapon.key]}" if weapon.key in holders else ""
            lines.append(ui.weapon_line(bot.cfg, weapon, r["stars"], r["level"]) + holder)
        pages = [ui.embed(bot.cfg, f"🗡️ Tes armes ({len(items)})", "\n".join(lines[i:i + 20]) or "Aucune arme.")
                 for i in range(0, max(1, len(lines)), 20)]
        await send_pages(interaction, pages, ephemeral=True)

    @arme.command(name="info", description="Fiche d'une arme possédée")
    @app_commands.autocomplete(arme=owned_weapon_autocomplete)
    async def arme_info(self, interaction: discord.Interaction, arme: str) -> None:
        bot = bot_of(interaction)
        weapon = resolve_weapon(bot, arme)
        row = await bot.collection.weapon(interaction.user.id, weapon.key)
        e = ui.embed(bot.cfg, weapon.name, ui.weapon_line(bot.cfg, weapon), colour=ui.RARITY_COLORS.get(weapon.rarity))
        if weapon.image_url:
            e.set_thumbnail(url=weapon.image_url)
        e.add_field(name="ATK max", value=num(weapon.atk))
        if row:
            e.add_field(name="Ton arme", value=f"{ui.stars(row['stars'])} niv. {row['level']}/"
                                               f"{stats.max_level(bot.cfg, row['stars'])} · ATK "
                                               f"{num(stats.scale(bot.cfg, weapon.atk, row['level'], row['stars']))}")
        if weapon.exclusive_hero and bot.data.hero(weapon.exclusive_hero):
            e.add_field(name="Exclusive à", value=bot.data.hero(weapon.exclusive_hero).name, inline=False)
        if weapon.stats_text:
            e.add_field(name="Bonus (sur son héros)", value=weapon.stats_text[:1024], inline=False)
        if weapon.effect_text:
            e.add_field(name="Effet", value=weapon.effect_text[:1024], inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @arme.command(name="equiper", description="Équiper une arme sur un héros")
    @app_commands.autocomplete(heros=owned_hero_autocomplete, arme=owned_weapon_autocomplete)
    async def arme_equiper(self, interaction: discord.Interaction, heros: str, arme: str) -> None:
        bot = bot_of(interaction)
        hero = resolve_hero(bot, heros)
        weapon = resolve_weapon(bot, arme)
        previous = await bot.collection.equip(interaction.user.id, hero.key, weapon.key)
        extra = f" (retirée de {previous})" if previous else ""
        bonus = " ✦ bonus d'arme exclusive actif !" if weapon.exclusive_hero == hero.key else ""
        await interaction.response.send_message(f"🗡️ **{hero.name}** équipe **{weapon.name}**{extra}.{bonus}")

    @arme.command(name="retirer", description="Retirer l'arme d'un héros")
    @app_commands.autocomplete(heros=owned_hero_autocomplete)
    async def arme_retirer(self, interaction: discord.Interaction, heros: str) -> None:
        bot = bot_of(interaction)
        hero = resolve_hero(bot, heros)
        await bot.collection.unequip(interaction.user.id, hero.key)
        await interaction.response.send_message(f"**{hero.name}** n'a plus d'arme.", ephemeral=True)

    @arme.command(name="ameliorer", description="Monter le niveau d'une arme (coûte de l'or)")
    @app_commands.autocomplete(arme=owned_weapon_autocomplete)
    async def arme_ameliorer(self, interaction: discord.Interaction, arme: str,
                             niveaux: app_commands.Range[int, 1, 100] = 1) -> None:
        bot = bot_of(interaction)
        weapon = resolve_weapon(bot, arme)
        result = await bot.collection.level_up(interaction.user.id, "weapon", weapon.key, niveaux)
        await interaction.response.send_message(
            f"⬆️ **{weapon.name}** passe au **niveau {result.new_level}** pour 🪙 {num(result.cost)}.")

    # -- /team ----------------------------------------------------------------

    @team.command(name="voir", description="Afficher une équipe")
    async def team_voir(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        bot = bot_of(interaction)
        target = membre or interaction.user
        units = await bot.collection.team_units(target.id)
        if not units:
            await interaction.response.send_message(f"{target.display_name} n'a pas d'équipe. `/team definir`",
                                                     ephemeral=True)
            return
        party = party_effects(bot.cfg, units)
        e = ui.embed(bot.cfg, f"👥 Équipe de {target.display_name}", f"Puissance : **{num(power(bot.cfg, units))}**")
        for i, unit in enumerate(units):
            s = unit_stats(bot.cfg, unit, party)
            weapon = f"\n🗡️ {unit.weapon.name}" if unit.weapon else "\n🗡️ *aucune arme*"
            e.add_field(name=("👑 " if i == 0 else "") + f"{unit.hero.name} {ui.stars(unit.stars)} niv.{unit.level}",
                        value=f"⚔️ {num(s.atk)} ❤️ {num(s.hp)} 🛡️ {num(s.defense)}"
                              f"\n🔗 {ui.STATE_FR.get(unit.hero.chain_start)} → {ui.STATE_FR.get(unit.hero.chain_end)}"
                              f"{weapon}", inline=True)
        buffs = [f"{'👑 ' if i == 0 else ''}{u.hero.name} : {u.hero.party_text}" for i, u in enumerate(units) if u.hero.party_text]
        if buffs:
            e.add_field(name=f"Buffs d'équipe (leader ×{bot.cfg.combat.leader_party_buff_multiplier})",
                        value="\n".join(buffs)[:1024], inline=False)
        if units[0].hero.image_url:
            e.set_thumbnail(url=units[0].hero.image_url)
        await interaction.response.send_message(embed=e)

    @team.command(name="definir", description="Composer ton équipe (le 1er héros est le leader)")
    @app_commands.describe(leader="Le leader (son buff d'équipe est renforcé)", heros2="2e héros",
                           heros3="3e héros", heros4="4e héros")
    @app_commands.autocomplete(leader=owned_hero_autocomplete, heros2=owned_hero_autocomplete,
                               heros3=owned_hero_autocomplete, heros4=owned_hero_autocomplete)
    async def team_definir(self, interaction: discord.Interaction, leader: str, heros2: Optional[str] = None,
                           heros3: Optional[str] = None, heros4: Optional[str] = None) -> None:
        bot = bot_of(interaction)
        keys = [resolve_hero(bot, v).key for v in (leader, heros2, heros3, heros4) if v]
        size = int(bot.cfg.combat.team_size)
        if len(keys) > size:
            raise GameError(f"Une équipe compte au maximum {size} héros.")
        await bot.collection.set_team(interaction.user.id, keys)
        units = await bot.collection.team_units(interaction.user.id)
        lines = [("👑 " if i == 0 else "• ") + ui.hero_line(u.hero, u.stars, u.level) for i, u in enumerate(units)]
        await interaction.response.send_message(
            embed=ui.embed(bot.cfg, "✅ Équipe enregistrée",
                           "\n".join(lines) + f"\n\nPuissance : **{num(power(bot.cfg, units))}**"))


async def setup(bot) -> None:
    await bot.add_cog(Heroes(bot))
