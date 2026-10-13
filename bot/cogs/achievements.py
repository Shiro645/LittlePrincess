"""Succès et titres : /succes, /titre."""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..services.players import format_rewards
from ..ui import format as ui
from ..ui.format import num
from ..ui.views import send_pages
from ._common import bot_of


async def title_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot = bot_of(interaction)
    titles = await bot.players.titles(interaction.user.id)
    return [app_commands.Choice(name=t, value=t) for t in titles if current.lower() in t.lower()][:25]


class Achievements(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="succes", description="Tes succès (ou ceux d'un membre)")
    async def succes(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        bot = bot_of(interaction)
        target = membre or interaction.user
        unlocked = await bot.progress.unlocked_achievements(target.id)
        lines = []
        for ach in bot.cfg.achievements:
            if ach["id"] in unlocked:
                lines.append(f"🏆 **{ach['name']}** — {ach['description']}")
            else:
                value = await bot.progress.stat(target.id, ach["stat"])
                target_value = int(ach["target"])
                extra = f" · titre « {ach['title']} »" if ach.get("title") else ""
                lines.append(f"🔒 **{ach['name']}** — {ach['description']} ({num(min(value, target_value))}/"
                             f"{num(target_value)})\n   ↳ {format_rewards(ach.get('reward') or {})}{extra}")
        title = f"🏆 Succès de {target.display_name} — {len(unlocked)}/{len(bot.cfg.achievements)}"
        pages = [ui.embed(bot.cfg, title, chunk) for chunk in ui.chunk_lines(lines, 3800)]
        await send_pages(interaction, pages, ephemeral=membre is None)

    @app_commands.command(name="titre", description="Choisir le titre affiché sur ton profil")
    @app_commands.describe(titre="Un titre débloqué (laisser vide pour retirer)")
    @app_commands.autocomplete(titre=title_autocomplete)
    async def titre(self, interaction: discord.Interaction, titre: Optional[str] = None) -> None:
        bot = bot_of(interaction)
        await bot.players.get(interaction.user.id)
        if titre is None:
            await bot.players.set_title(interaction.user.id, None)
            await interaction.response.send_message("Titre retiré.", ephemeral=True)
            return
        titles = await bot.players.titles(interaction.user.id)
        if titre not in titles:
            await interaction.response.send_message(
                "Tu n'as pas ce titre. Titres débloqués : " + (", ".join(titles) or "aucun"), ephemeral=True)
            return
        await bot.players.set_title(interaction.user.id, titre)
        await interaction.response.send_message(f"🏷️ Ton titre est maintenant « {titre} ».", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Achievements(bot))
