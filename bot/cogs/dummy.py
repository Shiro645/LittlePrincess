"""Dummy : mannequin pour tester ses dégâts."""

from __future__ import annotations

import json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..game.simulate import run_dummy
from ..game.teams import build_specs
from ..services.collection import GameError
from ..services.leveling import add_xp_sync
from ..services.players import ensure_sync
from ..ui import format as ui
from ..ui.format import num
from ._common import bot_of, owned_hero_autocomplete, resolve_hero

ELEMENT_CHOICES = [app_commands.Choice(name=v, value=k) for k, v in ui.ELEMENT_FR.items()]
SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int]) -> str:
    if not values:
        return ""
    top = max(values) or 1
    return "".join(SPARK[min(len(SPARK) - 1, int(v / top * (len(SPARK) - 1)))] for v in values)


class Dummy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    dummy = app_commands.Group(name="dummy", description="Mannequin d'entraînement")

    @dummy.command(name="tester", description="Teste les dégâts de ton équipe (ou d'un héros) sur le mannequin")
    @app_commands.describe(heros="Tester un seul héros au lieu de l'équipe",
                           element="Élément du mannequin (sans élément = score classé)")
    @app_commands.autocomplete(heros=owned_hero_autocomplete)
    @app_commands.choices(element=ELEMENT_CHOICES)
    @app_commands.checks.cooldown(1, 20)
    async def tester(self, interaction: discord.Interaction, heros: Optional[str] = None,
                     element: Optional[app_commands.Choice[str]] = None) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg
        user_id = interaction.user.id
        if heros:
            hero = resolve_hero(bot, heros)
            units = await bot.collection.units(user_id, [hero.key])
            if not units:
                raise GameError(f"Tu ne possèdes pas **{hero.name}**.")
        else:
            units = await bot.collection.team_units(user_id)
            if not units:
                raise GameError("Tu n'as pas d'équipe : `/team definir` (ou précise un héros).")
        turns = int(cfg.dummy.turns)
        run = run_dummy(cfg, build_specs(cfg, units, user_id), turns, element.value if element else None)
        total = run.total
        ranked = heros is None and element is None

        def _tx(conn):
            ensure_sync(conn, cfg, user_id)
            record = False
            if ranked:
                best = conn.execute("SELECT best FROM dummy_scores WHERE user_id = ?", (user_id,)).fetchone()
                if best is None or total > best["best"]:
                    details = json.dumps({f.name: f.total_damage for f in run.team})
                    conn.execute("INSERT INTO dummy_scores (user_id, best, details) VALUES (?, ?, ?) "
                                 "ON CONFLICT(user_id) DO UPDATE SET best = excluded.best, details = excluded.details, "
                                 "at = datetime('now')", (user_id, total, details))
                    record = best is not None
            unlocked = bot.progress.record_sync(conn, user_id, "dummy")
            if ranked:
                unlocked += bot.progress.set_max_sync(conn, user_id, "best_dummy", total)
            xp = add_xp_sync(conn, cfg, user_id, int(cfg.leveling.minigame_xp.dummy))
            return record, unlocked, xp

        record, unlocked, xp = await bot.db.transaction(_tx)
        target = f"mannequin {ui.ELEMENT_EMOJI[element.value]} {element.name}" if element else "mannequin neutre"
        e = ui.embed(cfg, f"🎯 {num(total)} dégâts en {turns} tours",
                     f"Contre le {target} (DEF {cfg.dummy.defense}) — moyenne **{num(total // turns)}**/tour")
        for f in sorted(run.team, key=lambda f: -f.total_damage):
            share = f.total_damage / total * 100 if total else 0
            e.add_field(name=f"{f.name} — {num(f.total_damage)} ({share:.0f} %)",
                        value=f"⚔️ {num(f.damage['na'])} · ✨ {num(f.damage['skill'])} · 🔗 {num(f.damage['chain'])}",
                        inline=False)
        e.add_field(name="Dégâts par tour", value=f"`{sparkline(run.per_round)}` pic : {num(max(run.per_round))}",
                    inline=False)
        if record:
            e.set_footer(text="🏅 Nouveau record personnel !")
        elif not ranked:
            e.set_footer(text="Test libre (non classé : seul l'équipe sur le mannequin neutre compte)")
        await interaction.response.send_message(embed=e)
        xp.achievements += unlocked
        await bot.handle_xp_result(interaction.user, xp, interaction.channel)

    @dummy.command(name="classement", description="Meilleurs scores sur le mannequin")
    async def classement(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        rows = await bot.db.fetchall("SELECT user_id, best FROM dummy_scores ORDER BY best DESC LIMIT ?",
                                     (int(bot.cfg.dummy.leaderboard_size),))
        lines = [f"`{i + 1}.` **{await bot.display_name(r['user_id'])}** — {num(r['best'])}" for i, r in enumerate(rows)]
        await interaction.response.send_message(
            embed=ui.embed(bot.cfg, "🎯 Classement du dummy", "\n".join(lines) or "Aucun score."))


async def setup(bot) -> None:
    await bot.add_cog(Dummy(bot))
