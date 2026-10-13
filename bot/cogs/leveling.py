"""XP gagnée par l'activité sur le serveur et rôles de niveau."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ..config import int_keys

log = logging.getLogger(__name__)


class Leveling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _in_guild(self, guild_id) -> bool:
        return not self.bot.guild_id or guild_id == self.bot.guild_id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not self._in_guild(message.guild.id):
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        excluded = {int(c) for c in (self.bot.cfg.channels.xp_excluded or []) if c}
        channel_ids = {message.channel.id}
        if isinstance(message.channel, discord.Thread) and message.channel.parent_id:
            channel_ids.add(message.channel.parent_id)
        if channel_ids & excluded:
            return
        result = await self.bot.leveling.on_message(message.author.id)
        if result is None:
            return
        unlocked = await self.bot.progress.record(message.author.id, "messages")
        result.achievements += unlocked
        await self.bot.handle_xp_result(message.author, result, message.channel)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        member = payload.member
        if member is None or member.bot or not self._in_guild(payload.guild_id):
            return
        if getattr(payload, "message_author_id", None) == member.id:
            return  # pas d'XP en réagissant à ses propres messages
        result = await self.bot.leveling.on_reaction(member.id)
        if result is None:
            return
        result.achievements += await self.bot.progress.record(member.id, "reactions")
        channel = self.bot.get_channel(payload.channel_id)
        await self.bot.handle_xp_result(member, result, channel)

    async def apply_level_roles(self, member: discord.Member, level: int) -> None:
        roles_cfg = {lvl: int(rid) for lvl, rid in int_keys(self.bot.cfg.leveling.level_roles).items() if rid}
        if not roles_cfg:
            return
        reached = [lvl for lvl in roles_cfg if lvl <= level]
        if not reached:
            return
        if self.bot.cfg.leveling.keep_previous_roles:
            wanted = {roles_cfg[lvl] for lvl in reached}
        else:
            wanted = {roles_cfg[max(reached)]}
        managed = set(roles_cfg.values())
        current = {r.id for r in member.roles}
        to_add = [member.guild.get_role(r) for r in wanted - current]
        to_remove = [member.guild.get_role(r) for r in (managed - wanted) & current]
        try:
            if any(to_add):
                await member.add_roles(*[r for r in to_add if r], reason=f"Niveau {level}")
            if any(to_remove):
                await member.remove_roles(*[r for r in to_remove if r], reason=f"Niveau {level}")
        except discord.Forbidden:
            log.warning("Impossible de gérer les rôles de niveau : vérifie la permission « Gérer les rôles » "
                        "et que le rôle du bot est au-dessus des rôles de niveau.")
        except discord.HTTPException as exc:
            log.warning("Erreur lors de l'attribution des rôles de niveau : %s", exc)


async def setup(bot) -> None:
    await bot.add_cog(Leveling(bot))
