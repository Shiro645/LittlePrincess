"""Commandes générales : /aide, /profil, /classement."""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..game import stats
from ..game.teams import power
from ..ui import format as ui
from ..ui.format import num
from ._common import bot_of

HELP_SECTIONS = [
    ("🎰 Invocations", "`/invoquer` invoquer des héros ou des armes\n`/bannieres` bannières, taux et pitié"),
    ("🦸 Héros & armes", "`/heros liste` · `/heros info` · `/heros ameliorer`\n`/arme liste` · `/arme equiper` · "
                       "`/arme retirer` · `/arme ameliorer`"),
    ("👥 Équipe", "`/team voir` · `/team definir` (le 1er héros est le leader)"),
    ("📅 Quotidien", "`/daily` récompense du jour\n`/missions` missions du jour et de la semaine"),
    ("🧩 Wordle", "`/wordle deviner` trouve le héros du jour · `/wordle classement`"),
    ("⚔️ Colisée", "`/colisee defier` duel avec mise de gemmes · `/colisee stats`"),
    ("👹 Boss", "Chaque samedi ! `/boss info` · `/boss solo` (rattrapage) · `/boss classement`"),
    ("🎯 Dummy", "`/dummy tester` teste tes dégâts · `/dummy classement`"),
    ("🛒 Divers", "`/boutique voir` · `/boutique acheter` · `/donner` · `/succes` · `/titre` · "
                 "`/profil` · `/classement`"),
]

LEADERBOARDS = [
    app_commands.Choice(name="Niveau du compte", value="niveau"),
    app_commands.Choice(name="Collection de héros", value="collection"),
    app_commands.Choice(name="Duels gagnés", value="colisee"),
    app_commands.Choice(name="Dégâts sur le dummy", value="dummy"),
    app_commands.Choice(name="Dégâts sur les boss", value="boss"),
]


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="aide", description="Liste des commandes du bot")
    async def aide(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        e = ui.embed(bot.cfg, f"📖 {bot.cfg.general.bot_name} — aide",
                     "Un petit Guardian Tales pour la guilde : invoque, améliore, compose ton équipe et "
                     "affronte le boss du samedi !")
        for name, value in HELP_SECTIONS:
            e.add_field(name=name, value=value, inline=False)
        footer = f"v{bot.version} • {ui.ATTRIBUTION}"
        if bot.data.source != "api":
            footer += " • ⚠️ données provisoires (token API non configuré)"
        e.set_footer(text=footer)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="profil", description="Affiche ton profil (ou celui d'un membre)")
    @app_commands.describe(membre="Le membre à afficher")
    async def profil(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        bot = bot_of(interaction)
        target = membre or interaction.user
        if membre and membre.id != interaction.user.id:
            player = await bot.players.find(target.id)
            if player is None:
                await interaction.response.send_message(f"{target.display_name} n'a pas encore commencé l'aventure.",
                                                         ephemeral=True)
                return
        else:
            player = await bot.players.get(target.id)
        heroes = await bot.collection.heroes(target.id)
        weapons = await bot.collection.weapons(target.id)
        units = await bot.collection.team_units(target.id)
        unlocked = await bot.progress.unlocked_achievements(target.id)
        wins = await bot.progress.stat(target.id, "duel_wins")
        duels = await bot.db.fetchval("SELECT COUNT(*) FROM duels WHERE (challenger = ? OR opponent = ?) AND winner IS NOT NULL",
                                      (target.id, target.id), 0)

        need = stats.xp_to_next(bot.cfg, player["level"])
        title = f" — « {player['title']} »" if player["title"] else ""
        e = ui.embed(bot.cfg, f"{target.display_name}{title}")
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="Niveau", value=f"**{player['level']}** {ui.progress_bar(player['xp'], need)} "
                                         f"{num(player['xp'])}/{num(need)} XP", inline=False)
        e.add_field(name="Monnaies", value=f"💎 {num(player['gems'])}\n🪙 {num(player['gold'])}\n"
                                           f"🎫 {player['hero_tickets']} · 🎟️ {player['weapon_tickets']}")
        e.add_field(name="Collection", value=f"🦸 {len(heroes)}/{len(bot.data.heroes)} héros\n"
                                             f"🗡️ {len(weapons)} armes\n"
                                             f"🏆 {len(unlocked)}/{len(bot.cfg.achievements)} succès")
        e.add_field(name="Activité", value=f"🔥 Série daily : {player['daily_streak']} j\n"
                                           f"⚔️ Duels : {wins} V / {max(0, duels - wins)} D")
        if units:
            team_lines = [("👑 " if i == 0 else "• ") + ui.hero_line(u.hero, u.stars, u.level) for i, u in enumerate(units)]
            e.add_field(name=f"Équipe — puissance {num(power(bot.cfg, units))}", value="\n".join(team_lines), inline=False)
        else:
            e.add_field(name="Équipe", value="Aucune équipe : `/team definir`", inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="classement", description="Classements de la guilde")
    @app_commands.describe(categorie="Le classement à afficher")
    @app_commands.choices(categorie=LEADERBOARDS)
    async def classement(self, interaction: discord.Interaction, categorie: app_commands.Choice[str]) -> None:
        bot = bot_of(interaction)
        queries = {
            "niveau": ("SELECT user_id, level || ' (' || total_xp || ' XP)' AS v FROM players "
                       "ORDER BY total_xp DESC LIMIT 15"),
            "collection": ("SELECT user_id, COUNT(*) || ' héros · ' || SUM(stars) || '★' AS v FROM heroes "
                           "GROUP BY user_id ORDER BY COUNT(*) DESC, SUM(stars) DESC LIMIT 15"),
            "colisee": "SELECT user_id, value || ' victoires' AS v FROM stats WHERE stat = 'duel_wins' "
                       "ORDER BY value DESC LIMIT 15",
            "dummy": "SELECT user_id, best AS v FROM dummy_scores ORDER BY best DESC LIMIT 15",
            "boss": "SELECT user_id, SUM(damage) AS v FROM boss_damage GROUP BY user_id ORDER BY SUM(damage) DESC LIMIT 15",
        }
        rows = await bot.db.fetchall(queries[categorie.value])
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            value = row["v"]
            if isinstance(value, int):
                value = f"{num(value)} dégâts"
            rank = medals[i] if i < 3 else f"`{i + 1}.`"
            lines.append(f"{rank} **{await bot.display_name(row['user_id'])}** — {value}")
        e = ui.embed(bot.cfg, f"🏅 Classement — {categorie.name}", "\n".join(lines) or "Personne pour l'instant !")
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(General(bot))
