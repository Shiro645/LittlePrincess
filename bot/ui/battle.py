"""Affichage des combats : résumé et journal détaillé paginé."""

from __future__ import annotations

import discord

from ..game.combat import BattleResult, Fighter, fmt
from . import format as ui
from .views import Paginator


def fighter_line(f: Fighter) -> str:
    status = "💀" if not f.alive else f"❤️ {ui.progress_bar(f.hp, f.max_hp, 6)}"
    return (f"{status} **{f.name}** — {fmt(f.total_damage)} dégâts"
            + (f" · 💚 {fmt(f.healed)}" if f.healed else ""))


def log_pages(cfg, result: BattleResult, title: str) -> list[discord.Embed]:
    lines: list[str] = []
    for i, round_log in enumerate(result.round_logs, start=1):
        lines.append(f"__**Tour {i}**__")
        lines.extend(round_log or ["*(rien ne se passe)*"])
    return [ui.embed(cfg, f"📜 {title}", chunk) for chunk in ui.chunk_lines(lines, 3800)]


class LogView(discord.ui.View):
    """Bouton « Voir le combat » qui ouvre le journal détaillé en message privé (éphémère)."""

    def __init__(self, cfg, result: BattleResult, title: str, timeout: float = 900):
        super().__init__(timeout=timeout)
        self.cfg, self.result, self.title = cfg, result, title

    @discord.ui.button(label="Voir le combat", emoji="📜", style=discord.ButtonStyle.secondary)
    async def show(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pages = log_pages(self.cfg, self.result, self.title)
        view = Paginator(pages, interaction.user.id) if len(pages) > 1 else discord.utils.MISSING
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)
