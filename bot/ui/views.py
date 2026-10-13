"""Vues réutilisables : pagination, confirmation."""

from __future__ import annotations

from typing import Optional

import discord


class Paginator(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], author_id: Optional[int] = None, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.index = 0
        self.author_id = author_id
        self.message: Optional[discord.Message] = None
        self._sync()

    def _sync(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1
        self.counter.label = f"{self.index + 1}/{len(self.pages)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("Ce menu ne t'appartient pas.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # pragma: no cover
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def send_pages(interaction: discord.Interaction, pages: list[discord.Embed], ephemeral: bool = False) -> None:
    """Envoie une ou plusieurs pages (avec boutons si plusieurs)."""
    if len(pages) == 1:
        if interaction.response.is_done():
            await interaction.followup.send(embed=pages[0], ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=pages[0], ephemeral=ephemeral)
        return
    view = Paginator(pages, interaction.user.id)
    if interaction.response.is_done():
        view.message = await interaction.followup.send(embed=pages[0], view=view, ephemeral=ephemeral, wait=True)
    else:
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=ephemeral)
        view.message = await interaction.original_response()
