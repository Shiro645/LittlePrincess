"""Configuration from Discord (staff only).

`/config panneau` opens the interactive configuration panel as an ephemeral message
(see bot/ui/config_panel.py).
`/config exporter` / `/config importer` download and upload the whole config.json.

Every change goes through `Settings.edit()`: copy the current config, mutate the copy,
validate, save config.json (previous version kept in config.json.bak), reload, and log
the change in the staff channel.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Callable

import discord
from discord import app_commands
from discord.ext import commands

from ..config import ConfigError, deep_merge, dumps, load_defaults, plain
from ..ui.config_panel import ConfigPanelView, panel_embed
from ._common import bot_of, staff_only

log = logging.getLogger(__name__)

class Settings(commands.Cog):
    config = app_commands.Group(name="config", description="Configuration du bot (staff)", guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    async def edit(self, interaction: discord.Interaction, mutate: Callable[[dict], str]) -> str:
        """Apply `mutate` to a copy of the config, then validate, save and reload."""
        async with self.lock:
            data = plain(self.bot.cfg)
            summary = mutate(data)
            self.bot.save_config(data)
        log.info("Config changed by %s: %s", interaction.user, summary)
        await self.bot.notify_staff(f"⚙️ {interaction.user.mention} a modifié la config : {summary}"[:1900])
        return summary

    @config.command(name="panneau", description="Ouvrir le panneau de configuration (visible par toi seul)")
    @staff_only()
    async def panneau(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=panel_embed(self.bot), view=ConfigPanelView(self.bot),
                                                ephemeral=True)

    @config.command(name="exporter", description="Télécharger le fichier config.json actuel")
    @staff_only()
    async def exporter(self, interaction: discord.Interaction) -> None:
        content = dumps(plain(bot_of(interaction).cfg)) + "\n"
        await interaction.response.send_message(
            "📄 Configuration actuelle. Tu peux la modifier puis la renvoyer avec `/config importer`.",
            file=discord.File(io.BytesIO(content.encode("utf-8")), filename="config.json"), ephemeral=True)

    @config.command(name="importer", description="Remplacer la configuration par un fichier config.json")
    @app_commands.describe(fichier="Un fichier config.json (par exemple obtenu avec /config exporter)")
    @staff_only()
    async def importer(self, interaction: discord.Interaction, fichier: discord.Attachment) -> None:
        if fichier.size > 1_000_000:
            raise ConfigError("Fichier trop gros (1 Mo maximum).")
        try:
            imported = json.loads((await fichier.read()).decode("utf-8-sig"))
        except UnicodeDecodeError:
            raise ConfigError("Le fichier doit être un texte UTF-8.") from None
        except json.JSONDecodeError as exc:
            raise ConfigError(f"JSON invalide (ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}") from None
        if not isinstance(imported, dict):
            raise ConfigError("Le fichier doit contenir un objet JSON { … }.")
        merged = deep_merge(load_defaults(), imported)

        def mutate(data: dict) -> str:
            data.clear()
            data.update(merged)
            return f"nouveau fichier importé ({fichier.filename})"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Settings(bot))
