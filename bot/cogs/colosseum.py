"""Colisée : duels en direct entre joueurs, combat simulé, mise de gemmes."""

from __future__ import annotations

import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from .. import timeutil
from ..game.combat import Battle, make_fighter
from ..game.teams import build_specs, power
from ..services.collection import GameError
from ..services.leveling import add_xp_sync
from ..services.players import credit_sync, debit_sync, ensure_sync
from ..ui import format as ui
from ..ui.battle import LogView, fighter_line
from ..ui.format import num
from ._common import bot_of


class DuelView(discord.ui.View):
    def __init__(self, cog: "Colosseum", challenger: discord.Member, opponent: discord.Member, bet: int):
        super().__init__(timeout=float(cog.bot.cfg.colosseum.accept_timeout))
        self.cog, self.challenger, self.opponent, self.bet = cog, challenger, opponent, bet
        self.message: Optional[discord.Message] = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in (self.opponent.id, self.challenger.id):
            return True
        await interaction.response.send_message("Ce défi ne t'est pas destiné.", ephemeral=True)
        return False

    def _finish(self) -> None:
        self.done = True
        self.cog.pending.discard(self.challenger.id)
        self.cog.pending.discard(self.opponent.id)
        self.stop()

    @discord.ui.button(label="Accepter", emoji="⚔️", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Seul l'adversaire peut accepter.", ephemeral=True)
            return
        self._finish()
        await interaction.response.defer()
        try:
            embed, view = await self.cog.run_duel(self.challenger, self.opponent, self.bet, interaction.channel)
        except GameError as exc:
            await interaction.edit_original_response(content=f"❌ Duel annulé : {exc}", embed=None, view=None)
            return
        await interaction.edit_original_response(content=None, embed=embed, view=view)

    @discord.ui.button(label="Refuser", emoji="✖️", style=discord.ButtonStyle.danger)
    async def refuse(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Seul l'adversaire peut refuser.", ephemeral=True)
            return
        self._finish()
        await interaction.response.edit_message(content=f"🏳️ {self.opponent.display_name} a refusé le duel.",
                                                embed=None, view=None)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.challenger.id:
            await interaction.response.send_message("Seul le challenger peut annuler.", ephemeral=True)
            return
        self._finish()
        await interaction.response.edit_message(content="Défi annulé.", embed=None, view=None)

    async def on_timeout(self) -> None:
        if self.done:
            return
        self._finish()
        if self.message:
            try:
                await self.message.edit(content=f"⌛ {self.opponent.display_name} n'a pas répondu à temps.",
                                        embed=None, view=None)
            except discord.HTTPException:
                pass


class Colosseum(commands.Cog):
    colisee = app_commands.Group(name="colisee", description="Duels entre membres de la guilde")

    def __init__(self, bot):
        self.bot = bot
        self.pending: set[int] = set()

    async def duels_today(self, user_id: int) -> int:
        day = timeutil.day_key(self.bot.cfg)
        return await self.bot.db.fetchval(
            "SELECT COUNT(*) FROM duels WHERE day = ? AND (challenger = ? OR opponent = ?)", (day, user_id, user_id), 0)

    @colisee.command(name="defier", description="Défier un membre en duel (combat automatique)")
    @app_commands.describe(adversaire="Le membre à défier", mise="Gemmes misées par chacun (le gagnant remporte tout)")
    async def defier(self, interaction: discord.Interaction, adversaire: discord.Member,
                     mise: app_commands.Range[int, 0, 1_000_000] = 0) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg.colosseum
        challenger = interaction.user
        if adversaire.bot or adversaire.id == challenger.id:
            raise GameError("Choisis un autre membre de la guilde.")
        if not int(cfg.min_bet) <= mise <= int(cfg.max_bet):
            raise GameError(f"La mise doit être entre {num(cfg.min_bet)} et {num(cfg.max_bet)} gemmes.")
        if challenger.id in self.pending or adversaire.id in self.pending:
            raise GameError("Un défi est déjà en attente pour l'un de vous deux.")
        for member in (challenger, adversaire):
            if not await bot.collection.team_keys(member.id):
                raise GameError(f"{member.display_name} n'a pas d'équipe (`/team definir`).")
            if await self.duels_today(member.id) >= int(cfg.max_duels_per_day):
                raise GameError(f"{member.display_name} a atteint la limite de {cfg.max_duels_per_day} duels aujourd'hui.")
            player = await bot.players.get(member.id)
            if player["gems"] < mise:
                raise GameError(f"{member.display_name} n'a pas assez de gemmes pour cette mise.")

        units_a = await bot.collection.team_units(challenger.id)
        units_b = await bot.collection.team_units(adversaire.id)
        e = ui.embed(bot.cfg, "⚔️ Défi au Colisée !",
                     f"{challenger.mention} défie {adversaire.mention}"
                     + (f" avec une mise de 💎 **{num(mise)}** chacun !" if mise else " (sans mise) !"))
        e.add_field(name=f"{challenger.display_name} — {num(power(bot.cfg, units_a))}",
                    value="\n".join(ui.hero_line(u.hero, u.stars, u.level) for u in units_a))
        e.add_field(name=f"{adversaire.display_name} — {num(power(bot.cfg, units_b))}",
                    value="\n".join(ui.hero_line(u.hero, u.stars, u.level) for u in units_b))
        e.set_footer(text=f"{adversaire.display_name}, tu as {cfg.accept_timeout} s pour répondre.")
        view = DuelView(self, challenger, adversaire, mise)
        self.pending.update({challenger.id, adversaire.id})
        await interaction.response.send_message(content=adversaire.mention, embed=e, view=view,
                                                allowed_mentions=discord.AllowedMentions(users=[adversaire]))
        view.message = await interaction.original_response()

    async def run_duel(self, a: discord.Member, b: discord.Member, bet: int,
                       channel: Optional[discord.abc.Messageable] = None) -> tuple[discord.Embed, LogView]:
        bot = self.bot
        cfg = bot.cfg
        units_a = await bot.collection.team_units(a.id)
        units_b = await bot.collection.team_units(b.id)
        if not units_a or not units_b:
            raise GameError("une des équipes est vide.")
        hp = float(cfg.combat.hp_multiplier.duel)
        fighters = [make_fighter(s, 0, hp) for s in build_specs(cfg, units_a, a.id)]
        fighters += [make_fighter(s, 1, hp) for s in build_specs(cfg, units_b, b.id)]
        battle = Battle(cfg, fighters, random.Random())
        result = battle.run_auto(int(cfg.combat.max_rounds_duel))
        winner = {0: a, 1: b}.get(result.winner) if result.winner is not None else None
        day = timeutil.day_key(cfg)
        xp = int(cfg.leveling.minigame_xp.duel)

        def _tx(conn):
            for uid in (a.id, b.id):
                ensure_sync(conn, cfg, uid)
            if bet and not (debit_sync(conn, a.id, {"gems": bet}) and debit_sync(conn, b.id, {"gems": bet})):
                raise GameError("un des joueurs n'a plus assez de gemmes pour la mise.")
            if winner is not None:
                credit_sync(conn, winner.id, {"gems": bet * 2})
            else:
                credit_sync(conn, a.id, {"gems": bet})
                credit_sync(conn, b.id, {"gems": bet})
            conn.execute("INSERT INTO duels (challenger, opponent, bet, winner, day) VALUES (?, ?, ?, ?, ?)",
                         (a.id, b.id, bet, winner.id if winner else None, day))
            unlocked = {a.id: [], b.id: []}
            xp_results = {}
            for uid in (a.id, b.id):
                unlocked[uid] += bot.progress.record_sync(conn, uid, "duel")
                xp_results[uid] = add_xp_sync(conn, cfg, uid, xp)
            if winner is not None:
                unlocked[winner.id] += bot.progress.record_sync(conn, winner.id, "duel_win")
            return unlocked, xp_results

        unlocked, xp_results = await bot.db.transaction(_tx)

        if winner is not None:
            title = f"🏆 {winner.display_name} remporte le duel !"
            desc = f"en {result.rounds} tours" + (" (aux PV restants)" if result.timeout else "")
            if bet:
                desc += f" — gain : 💎 **{num(bet * 2)}**"
        else:
            title, desc = "🤝 Égalité parfaite !", "Les mises sont rendues."
        e = ui.embed(cfg, title, desc)
        e.add_field(name=a.display_name, value="\n".join(fighter_line(f) for f in result.side(0)))
        e.add_field(name=b.display_name, value="\n".join(fighter_line(f) for f in result.side(1)))
        mvp = max(result.fighters, key=lambda f: f.total_damage)
        e.set_footer(text=f"MVP : {mvp.name} ({num(mvp.total_damage)} dégâts)")
        if winner is not None:
            e.set_thumbnail(url=winner.display_avatar.url)
        for member in (a, b):
            result_xp = xp_results[member.id]
            result_xp.achievements += unlocked[member.id]
            await bot.handle_xp_result(member, result_xp, channel)
        return e, LogView(cfg, result, f"{a.display_name} vs {b.display_name}")

    @colisee.command(name="stats", description="Statistiques de duel")
    async def stats(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        bot = bot_of(interaction)
        target = membre or interaction.user
        row = await bot.db.fetchone(
            "SELECT COUNT(*) AS total, SUM(winner = :u) AS wins, "
            "SUM(CASE WHEN winner = :u THEN bet WHEN winner IS NOT NULL THEN -bet ELSE 0 END) AS net "
            "FROM duels WHERE challenger = :u OR opponent = :u", {"u": target.id})
        total, wins, net = row["total"] or 0, row["wins"] or 0, row["net"] or 0
        e = ui.embed(bot.cfg, f"⚔️ Colisée — {target.display_name}",
                     f"Duels : **{total}** · Victoires : **{wins}** · Défaites : **{total - wins}**\n"
                     f"Bilan des mises : 💎 {'+' if net >= 0 else ''}{num(net)}")
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(Colosseum(bot))
