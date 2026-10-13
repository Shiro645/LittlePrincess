"""Récompenses quotidiennes et missions : /daily, /missions."""

from __future__ import annotations

from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from .. import timeutil
from ..services.collection import GameError
from ..services.players import credit_sync, ensure_sync, format_rewards
from ..ui import format as ui
from ._common import bot_of


def daily_reward(cfg, streak: int) -> dict:
    rewards = cfg.daily.rewards
    base = rewards[(streak - 1) % len(rewards)]
    cycles = (streak - 1) // len(rewards)
    bonus = min(cycles * float(cfg.daily.streak_cycle_bonus_pct), float(cfg.daily.streak_cycle_bonus_max_pct))
    return {k: int(round(int(v) * (1 + bonus / 100))) for k, v in base.items()}


async def missions_embed(bot, user: discord.abc.User) -> tuple[discord.Embed, bool]:
    states = await bot.progress.missions(user.id)
    e = ui.embed(bot.cfg, f"📋 Missions de {user.display_name}")
    claimable = False
    for weekly in (False, True):
        lines = []
        for st in (s for s in states if s.weekly == weekly):
            target = int(st.mission["target"])
            if st.claimed:
                mark = "✅"
            elif st.done:
                mark = "🎁"
                claimable = True
            else:
                mark = "▫️"
            lines.append(f"{mark} **{st.mission['name']}** — {min(st.progress, target)}/{target} "
                         f"{ui.progress_bar(st.progress, target, 8)}\n   ↳ {format_rewards(st.mission.get('reward') or {})}")
        reset = timeutil.next_reset(bot.cfg)
        if weekly:
            day = timeutil.game_day(bot.cfg)
            reset = reset + timedelta(days=(6 - day.weekday()))
        name = ("📆 Hebdomadaires" if weekly else "☀️ Quotidiennes") + f" — fin {timeutil.discord_ts(reset)}"
        e.add_field(name=name[:256], value="\n".join(lines) or "—", inline=False)
    e.set_footer(text="🎁 = terminée, clique sur « Récupérer »")
    return e, claimable


class ClaimView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    @discord.ui.button(label="Récupérer", emoji="🎁", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Ce ne sont pas tes missions !", ephemeral=True)
            return
        bot = bot_of(interaction)
        total, claimed = await bot.progress.claim_missions(interaction.user.id)
        e, claimable = await missions_embed(bot, interaction.user)
        button.disabled = not claimable
        await interaction.response.edit_message(embed=e, view=self)
        if claimed:
            await interaction.followup.send(f"🎁 {len(claimed)} mission(s) récupérée(s) : {format_rewards(total)}",
                                            ephemeral=True)


class Daily(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="daily", description="Récupère ta récompense quotidienne")
    async def daily(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg
        today = timeutil.game_day(cfg)
        yesterday = (today - timedelta(days=1)).isoformat()
        user_id = interaction.user.id

        def _tx(conn):
            player = ensure_sync(conn, cfg, user_id)
            if player["daily_last"] == today.isoformat():
                raise GameError(f"Déjà récupéré aujourd'hui ! Prochain daily {timeutil.discord_ts(timeutil.next_reset(cfg))}.")
            streak = player["daily_streak"] + 1 if player["daily_last"] == yesterday else 1
            reward = daily_reward(cfg, streak)
            conn.execute("UPDATE players SET daily_last = ?, daily_streak = ? WHERE user_id = ?",
                         (today.isoformat(), streak, user_id))
            credit_sync(conn, user_id, reward)
            unlocked = bot.progress.record_sync(conn, user_id, "daily_claim")
            unlocked += bot.progress.set_max_sync(conn, user_id, "best_streak", streak)
            return streak, reward, unlocked

        streak, reward, unlocked = await bot.db.transaction(_tx)
        n = len(cfg.daily.rewards)
        position = (streak - 1) % n
        calendar = " ".join("🟩" if i < position else "🎁" if i == position else "⬜" for i in range(n))
        e = ui.embed(cfg, "☀️ Récompense quotidienne", f"Tu reçois {format_rewards(reward)}")
        e.add_field(name=f"🔥 Série : {streak} jour(s)", value=calendar, inline=False)
        e.set_footer(text="Reviens demain pour continuer ta série (le 7e jour rapporte gros) !")
        await interaction.response.send_message(embed=e)
        await bot.notify_achievements(interaction.user, unlocked, interaction.channel)

    @app_commands.command(name="missions", description="Tes missions quotidiennes et hebdomadaires")
    async def missions(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        await bot.players.get(interaction.user.id)
        e, claimable = await missions_embed(bot, interaction.user)
        view = ClaimView(interaction.user.id)
        view.claim.disabled = not claimable
        await interaction.response.send_message(embed=e, view=view, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Daily(bot))
