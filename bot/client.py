"""Client Discord principal."""

from __future__ import annotations

import logging
import os
import traceback
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from . import __version__
from .config import USER_CONFIG, ConfigError, load_config, validate, write_json
from .db import Database
from .gamedata import GameData
from .services.collection import Collection, GameError
from .services.context import AppContext
from .services.leveling import Leveling, XpResult
from .services.players import Players, format_rewards
from .services.progress import Progress
from .ui import format as ui

log = logging.getLogger(__name__)

EXTENSIONS = (
    "bot.cogs.general",
    "bot.cogs.gacha",
    "bot.cogs.heroes",
    "bot.cogs.daily",
    "bot.cogs.leveling",
    "bot.cogs.wordle",
    "bot.cogs.colosseum",
    "bot.cogs.dummy",
    "bot.cogs.boss",
    "bot.cogs.shop",
    "bot.cogs.achievements",
    "bot.cogs.admin",
    "bot.cogs.settings",
)

# Command groups the staff can use in any channel
STAFF_GROUPS = {"admin", "bot", "config"}


def is_staff(cfg, member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    staff_roles = {int(r) for r in (cfg.roles.staff or []) if r}
    return any(role.id in staff_roles for role in member.roles)


async def reply(interaction: discord.Interaction, content: Optional[str] = None, *, ephemeral: bool = False,
                **kwargs) -> None:
    """Reply to an interaction whether or not it was already acknowledged."""
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=ephemeral, **kwargs)
    else:
        await interaction.response.send_message(content, ephemeral=ephemeral, **kwargs)


class Tree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: LittlePrincess = interaction.client  # type: ignore[assignment]
        if interaction.type is discord.InteractionType.autocomplete:
            return True
        if interaction.guild is None:
            await interaction.response.send_message("Utilise le bot sur le serveur de la guilde.", ephemeral=True)
            return False
        allowed = {int(c) for c in (bot.cfg.channels.allowed or []) if c}
        if not allowed or is_staff(bot.cfg, interaction.user):
            return True
        command = interaction.command
        root = command.root_parent.name if command and command.root_parent else (command.name if command else "")
        if root in STAFF_GROUPS:
            return True
        channel = interaction.channel
        ids = {interaction.channel_id}
        if isinstance(channel, discord.Thread) and channel.parent_id:
            ids.add(channel.parent_id)
        if ids & allowed:
            return True
        mentions = " ".join(f"<#{c}>" for c in allowed)
        await interaction.response.send_message(f"Les commandes du bot sont à utiliser dans : {mentions}", ephemeral=True)
        return False

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, (GameError, ConfigError)):
            await reply(interaction, f"❌ {original}"[:2000], ephemeral=True)
            return
        if isinstance(error, app_commands.CheckFailure):
            await reply(interaction, "⛔ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        if isinstance(error, app_commands.CommandOnCooldown):
            await reply(interaction, f"⏳ Patiente encore {error.retry_after:.0f} s.", ephemeral=True)
            return
        log.error("Error in /%s", interaction.command.qualified_name if interaction.command else "?",
                  exc_info=original)
        try:
            await reply(interaction, "💥 Une erreur inattendue est survenue. Le staff peut consulter les logs.",
                        ephemeral=True)
        except discord.HTTPException:
            pass


class LittlePrincess(commands.Bot):
    def __init__(self, app: AppContext, guild_id: Optional[int]):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None, tree_cls=Tree)
        self.app = app
        self.guild_id = guild_id
        self.players = Players(app)
        self.collection = Collection(app)
        self.progress = Progress(app)
        self.leveling = Leveling(app)
        self.version = __version__
        self._ready_once = False
        # Problems found in config.json at startup (reported to the staff)
        self.config_problems: list[str] = []

    # -- shortcuts ------------------------------------------------------------

    @property
    def cfg(self):
        return self.app.cfg

    @property
    def db(self) -> Database:
        return self.app.db

    @property
    def data(self) -> GameData:
        return self.app.data

    @property
    def user_agent(self) -> str:
        return f"LittlePrincessBot/{self.version} (+https://github.com/{self.cfg.update.repository})"

    def reload_config(self) -> None:
        """Reload config.json (after a manual edit or a change made from Discord)."""
        cfg = load_config()
        problems = validate(cfg)
        if problems:
            raise ConfigError("config.json contient des erreurs :\n• " + "\n• ".join(problems[:15]))
        self.app.cfg = cfg
        self.app.data.cfg = cfg
        self.app.data.load()
        self.config_problems = []

    def save_config(self, data: dict) -> None:
        """Validate, save config.json (keeping a .bak copy) and apply the new config."""
        problems = validate(data)
        if problems:
            raise ConfigError("Modification refusée :\n• " + "\n• ".join(problems[:15]))
        write_json(USER_CONFIG, data)
        self.reload_config()

    # -- startup ---------------------------------------------------------------

    async def setup_hook(self) -> None:
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        await self.sync_commands()

    async def sync_commands(self) -> tuple[int, list[str]]:
        """Register the commands on the guild and remove leftover global commands.

        Global commands (for example from a previous bot that used the same Discord
        application) would show up next to ours, so they are deleted.
        Returns (number of synced commands, names of the removed global commands).
        """
        if not self.guild_id:
            synced = await self.tree.sync()
            log.warning("GUILD_ID not set: %d global commands (may take up to 1 h to appear)", len(synced))
            return len(synced), []
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("%d commands synced to guild %s", len(synced), self.guild_id)
        stale = await self.tree.fetch_commands()
        if not stale:
            return len(synced), []
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        names = [command.name for command in stale]
        log.info("Removed %d leftover global commands: %s", len(names), ", ".join(names))
        return len(synced), names

    async def on_ready(self) -> None:
        log.info("Logged in as %s (v%s)", self.user, self.version)
        if not self._ready_once:
            self._ready_once = True
            self.dispatch("first_ready")

    async def close(self) -> None:
        await super().close()
        await self.db.close()

    # -- channels and notifications ---------------------------------------------

    @property
    def guild(self) -> Optional[discord.Guild]:
        if self.guild_id:
            return self.get_guild(self.guild_id)
        return self.guilds[0] if self.guilds else None

    def channel(self, key: str) -> Optional[discord.abc.Messageable]:
        channel_id = int(self.cfg.channels.get(key) or 0)
        if not channel_id:
            return None
        channel = self.get_channel(channel_id)
        return channel if isinstance(channel, (discord.TextChannel, discord.Thread)) else None

    async def safe_send(self, channel: Optional[discord.abc.Messageable], *args, **kwargs) -> Optional[discord.Message]:
        if channel is None:
            return None
        try:
            return await channel.send(*args, **kwargs)
        except discord.HTTPException as exc:
            log.warning("Cannot send to %s: %s", getattr(channel, "id", "?"), exc)
            return None

    async def notify_achievements(self, user: discord.abc.User, unlocked: list[dict],
                                  fallback: Optional[discord.abc.Messageable] = None) -> None:
        if not unlocked:
            return
        channel = self.channel("achievements") or fallback
        lines = []
        for ach in unlocked:
            line = f"🏆 **{ach['name']}** — {ach['description']} ({format_rewards(ach.get('reward') or {})})"
            if ach.get("title"):
                line += f" · titre « {ach['title']} » débloqué"
            lines.append(line)
        await self.safe_send(channel, f"{user.mention} a débloqué :\n" + "\n".join(lines),
                             allowed_mentions=discord.AllowedMentions(users=[user]))

    async def handle_xp_result(self, member: discord.abc.User, result: Optional[XpResult],
                               fallback: Optional[discord.abc.Messageable] = None) -> None:
        """Announce a level-up, update level roles and report related achievements."""
        if result is None:
            return
        if result.leveled_up:
            cog = self.get_cog("Leveling")
            if cog is not None and isinstance(member, discord.Member):
                await cog.apply_level_roles(member, result.new_level)  # type: ignore[attr-defined]
            channel = self.channel("level_up") or fallback
            text = (f"🎉 {member.mention} passe au **niveau {result.new_level}** ! "
                    f"Récompense : {format_rewards(result.rewards)}")
            if result.titles:
                text += "\n🏷️ Nouveau titre : " + ", ".join(f"« {t} »" for t in result.titles) + " (`/titre`)"
            await self.safe_send(channel, text, allowed_mentions=discord.AllowedMentions(users=[member]))
        await self.notify_achievements(member, result.achievements, fallback)

    async def notify_staff(self, content: str) -> None:
        await self.safe_send(self.channel("admin"), content)

    async def display_name(self, user_id: int) -> str:
        guild = self.guild
        member = guild.get_member(user_id) if guild else None
        if member:
            return member.display_name
        user = self.get_user(user_id)
        return user.display_name if user else f"<{user_id}>"


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def format_exception(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-1500:]


__all__ = ["LittlePrincess", "reply", "is_staff", "ui", "setup_logging", "format_exception"]
