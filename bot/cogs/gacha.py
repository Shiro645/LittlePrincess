"""Invocations : /invoquer et /bannieres."""

from __future__ import annotations

import random
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from .. import timeutil
from ..config import int_keys
from ..game import gacha
from ..game.gacha import HERO
from ..services.collection import GameError, add_pull_sync
from ..services.players import debit_sync, ensure_sync, format_rewards
from ..ui import format as ui
from ..ui.format import num
from ._common import bot_of

BANNER_CHOICES = [
    app_commands.Choice(name="Vedette (héros de la semaine)", value="featured"),
    app_commands.Choice(name="Standard (héros)", value="standard"),
    app_commands.Choice(name="Armes", value="weapon"),
]
COUNT_CHOICES = [app_commands.Choice(name="x1", value=1), app_commands.Choice(name="x10", value=10)]


class Gacha(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="invoquer", description="Invoquer des héros ou des armes")
    @app_commands.describe(banniere="La bannière", nombre="Nombre d'invocations",
                           ticket="Utiliser des tickets d'invocation au lieu des gemmes")
    @app_commands.choices(banniere=BANNER_CHOICES, nombre=COUNT_CHOICES)
    async def invoquer(self, interaction: discord.Interaction, banniere: app_commands.Choice[str],
                       nombre: app_commands.Choice[int], ticket: bool = False) -> None:
        bot = bot_of(interaction)
        cfg, data = bot.cfg, bot.data
        banner = gacha.banners(cfg, data, timeutil.now(cfg))[banniere.value]
        count = nombre.value
        is_hero = banner.kind == HERO
        if ticket:
            cost = {"hero_tickets" if is_hero else "weapon_tickets": count}
        else:
            cost = {"gems": int(cfg.gacha.cost_ten if count >= 10 else cfg.gacha.cost_single * count)}
        pity_col = "pity_hero" if is_hero else "pity_weapon"
        user_id = interaction.user.id
        rng = random.Random()

        def _tx(conn):
            player = ensure_sync(conn, cfg, user_id)
            if not debit_sync(conn, user_id, cost):
                raise GameError(f"Il te manque des ressources : coût {format_rewards(cost)}.")
            pulls, pity = gacha.roll(cfg, data, banner, count, player[pity_col], rng)
            conn.execute(f"UPDATE players SET {pity_col} = ? WHERE user_id = ?", (pity, user_id))
            outcomes = []
            for pull in pulls:
                base = data.hero(pull.key).rarity if is_hero else data.weapon(pull.key).rarity
                outcomes.append(add_pull_sync(conn, cfg, user_id, pull, base))
            unlocked = bot.progress.record_sync(conn, user_id, "summon", count)
            three = sum(1 for p in pulls if is_hero and p.rarity == 3)
            if three:
                unlocked += bot.progress.record_sync(conn, user_id, "three_star", three)
            heroes_owned = conn.execute("SELECT COUNT(*) FROM heroes WHERE user_id = ?", (user_id,)).fetchone()[0]
            max_stars = conn.execute("SELECT MAX(stars) FROM heroes WHERE user_id = ?", (user_id,)).fetchone()[0] or 0
            unlocked += bot.progress.set_max_sync(conn, user_id, "heroes_owned", heroes_owned)
            unlocked += bot.progress.set_max_sync(conn, user_id, "max_hero_stars", max_stars)
            return outcomes, pity, unlocked

        outcomes, pity, unlocked = await bot.db.transaction(_tx)

        top = max(int_keys(cfg.gacha.hero_rates if is_hero else cfg.gacha.weapon_rates))
        best = max(outcomes, key=lambda o: o.pull.rarity)
        lines = []
        for o in sorted(outcomes, key=lambda o: -o.pull.rarity):
            if is_hero:
                hero = data.hero(o.pull.key)
                name = f"{ui.ELEMENT_EMOJI.get(hero.element, '')} **{hero.name}**"
            else:
                weapon = data.weapon(o.pull.key)
                name = f"🗡️ **{weapon.name}**{' ✦' if weapon.is_exclusive else ''}"
            tag = " 🌟 *vedette*" if o.pull.featured else ""
            tag += " 🛡️ *pitié*" if o.pull.pity else ""
            if o.status == "new":
                lines.append(f"`{ui.stars(o.pull.rarity):<5}` {name} — **NOUVEAU**{tag}")
            elif o.status == "star_up":
                lines.append(f"`{ui.stars(o.pull.rarity):<5}` {name} — ⬆️ passe à {ui.stars(o.stars)}{tag}")
            else:
                lines.append(f"`{ui.stars(o.pull.rarity):<5}` {name} — ♻️ max ({format_rewards(o.converted)}){tag}")

        jackpot = best.pull.rarity >= top
        title = ("✨ Invocation légendaire !" if jackpot else "🎰 Résultat de l'invocation") + f" — {banner.name}"
        e = ui.embed(cfg, title, "\n".join(lines), colour=ui.RARITY_COLORS.get(best.pull.rarity))
        image = data.hero(best.pull.key).image_url if is_hero else data.weapon(best.pull.key).image_url
        if image:
            e.set_thumbnail(url=image)
        limit = int(cfg.gacha.pity_hero if is_hero else cfg.gacha.pity_weapon)
        e.set_footer(text=f"Payé : {format_rewards(cost)} • Pitié : {pity}/{limit}")
        await interaction.response.send_message(embed=e)
        await bot.notify_achievements(interaction.user, unlocked, interaction.channel)

    @app_commands.command(name="bannieres", description="Bannières actuelles, taux et pitié")
    async def bannieres(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        cfg, data = bot.cfg, bot.data
        now = timeutil.now(cfg)
        banners = gacha.banners(cfg, data, now)
        player = await bot.players.get(interaction.user.id)
        start = gacha.week_start(now, int(cfg.gacha.featured_change_weekday), int(cfg.gacha.featured_change_hour))
        change = start + timedelta(days=7)

        featured = data.hero(banners["featured"].featured_hero) if banners["featured"].featured_hero else None
        e = ui.embed(cfg, "🎰 Bannières")
        hero_rates = " · ".join(f"{ui.stars(r)} {p}%" for r, p in sorted(int_keys(cfg.gacha.hero_rates).items(), reverse=True))
        weapon_rates = " · ".join(f"{ui.stars(r)} {p}%" for r, p in sorted(int_keys(cfg.gacha.weapon_rates).items(), reverse=True))
        if featured:
            e.add_field(
                name=f"🌟 Vedette : {featured.name}",
                value=(f"{ui.hero_line(featured)} — {int(float(cfg.gacha.featured_share) * 100)} % des 3★ "
                       f"sont {featured.name}\nChangement {timeutil.discord_ts(change)}"),
                inline=False,
            )
            if featured.image_url:
                e.set_thumbnail(url=featured.image_url)
        e.add_field(name="Taux héros", value=hero_rates, inline=False)
        e.add_field(name="Taux armes", value=f"{weapon_rates}\n(5★ = armes exclusives des héros)", inline=False)
        e.add_field(name="Coût", value=f"x1 : 💎 {num(cfg.gacha.cost_single)} · x10 : 💎 {num(cfg.gacha.cost_ten)}\n"
                                       "ou 🎫/🎟️ tickets (option `ticket`)", inline=False)
        e.add_field(name="Ta pitié",
                    value=f"Héros : {player['pity_hero']}/{cfg.gacha.pity_hero} (3★ garanti)\n"
                          f"Armes : {player['pity_weapon']}/{cfg.gacha.pity_weapon} (5★ garanti)", inline=False)
        e.set_footer(text="Doublon = +1★ (max 5★), au-delà converti en gemmes et or")
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Gacha(bot))
