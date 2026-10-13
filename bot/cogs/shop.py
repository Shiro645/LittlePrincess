"""Boutique et dons entre joueurs : /boutique, /donner."""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from .. import timeutil
from ..services.collection import GameError
from ..services.players import credit_sync, debit_sync, ensure_sync, format_rewards
from ..ui import format as ui
from ..ui.format import num
from ._common import bot_of, owned_hero_autocomplete, resolve_hero

TYPE_REWARD = {"gems": "gems", "hero_ticket": "hero_tickets", "weapon_ticket": "weapon_tickets"}
PERIOD_FR = {"day": "par jour", "week": "par semaine", "none": "au total"}
CURRENCY_CHOICES = [app_commands.Choice(name="💎 Gemmes", value="gems"), app_commands.Choice(name="🪙 Or", value="gold")]


def period_key(cfg, period: str) -> str:
    if period == "day":
        return "d:" + timeutil.day_key(cfg)
    if period == "week":
        return "w:" + timeutil.week_key(cfg)
    return "all"


def find_item(cfg, item_id: str) -> dict:
    for item in cfg.shop["items"]:
        if item["id"] == item_id:
            return item
    raise GameError("Article inconnu.")


async def item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot = bot_of(interaction)
    return [app_commands.Choice(name=f"{i['name']} — {format_rewards(i['price'])}"[:100], value=i["id"])
            for i in bot.cfg.shop["items"] if current.lower() in i["name"].lower()][:25]


class Shop(commands.Cog):
    boutique = app_commands.Group(name="boutique", description="La boutique de la guilde")

    def __init__(self, bot):
        self.bot = bot

    @boutique.command(name="voir", description="Articles en vente")
    async def voir(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg
        player = await bot.players.get(interaction.user.id)
        e = ui.embed(cfg, "🛒 Boutique", f"Tu as 💎 {num(player['gems'])} · 🪙 {num(player['gold'])}")
        for item in cfg.shop["items"]:
            period = str(item.get("limit_period", "none"))
            bought = await bot.db.fetchval(
                "SELECT count FROM shop_purchases WHERE user_id = ? AND item_id = ? AND period = ?",
                (interaction.user.id, item["id"], period_key(cfg, period)), 0)
            limit = int(item.get("limit", 0) or 0)
            stock = f"{limit - bought}/{limit} {PERIOD_FR.get(period, '')}" if limit else "illimité"
            e.add_field(name=item["name"], value=f"Prix : {format_rewards(item['price'])}\nRestant : {stock}")
        e.set_footer(text="/boutique acheter pour acheter")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @boutique.command(name="acheter", description="Acheter un article")
    @app_commands.describe(article="L'article", quantite="Quantité", heros="Pour le Cristal d'éveil : le héros à éveiller")
    @app_commands.autocomplete(article=item_autocomplete, heros=owned_hero_autocomplete)
    async def acheter(self, interaction: discord.Interaction, article: str,
                      quantite: app_commands.Range[int, 1, 50] = 1, heros: Optional[str] = None) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg
        item = find_item(cfg, article)
        kind = item["type"]
        hero = resolve_hero(bot, heros) if heros else None
        if kind == "star_up" and hero is None:
            raise GameError("Précise le héros à éveiller avec l'option `heros`.")
        period = period_key(cfg, str(item.get("limit_period", "none")))
        limit = int(item.get("limit", 0) or 0)
        price = {k: int(v) * quantite for k, v in item["price"].items()}
        user_id = interaction.user.id

        def _tx(conn):
            ensure_sync(conn, cfg, user_id)
            bought = conn.execute("SELECT count FROM shop_purchases WHERE user_id = ? AND item_id = ? AND period = ?",
                                  (user_id, item["id"], period)).fetchone()
            bought = bought["count"] if bought else 0
            if limit and bought + quantite > limit:
                raise GameError(f"Limite atteinte : encore {max(0, limit - bought)} achat(s) possible(s) pour cette période.")
            if not debit_sync(conn, user_id, price):
                raise GameError(f"Pas assez de ressources : il faut {format_rewards(price)}.")
            detail = ""
            if kind in TYPE_REWARD:
                gained = {TYPE_REWARD[kind]: int(item.get("amount", 1)) * quantite}
                credit_sync(conn, user_id, gained)
                detail = format_rewards(gained)
            elif kind == "star_up":
                stars = 0
                for _ in range(quantite):
                    stars = bot.collection.star_up_sync(conn, user_id, hero.key)
                detail = f"{hero.name} passe à {ui.stars(stars)}"
                bot.progress.set_max_sync(conn, user_id, "max_hero_stars", stars)
            else:
                raise GameError(f"Type d'article inconnu : {kind}")
            conn.execute("INSERT INTO shop_purchases (user_id, item_id, period, count) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(user_id, item_id, period) DO UPDATE SET count = count + excluded.count",
                         (user_id, item["id"], period, quantite))
            unlocked = bot.progress.record_sync(conn, user_id, "shop_purchase", quantite)
            return detail, unlocked

        detail, unlocked = await bot.db.transaction(_tx)
        await interaction.response.send_message(
            f"🛒 Achat : **{item['name']}** ×{quantite} pour {format_rewards(price)} → {detail}")
        await bot.notify_achievements(interaction.user, unlocked, interaction.channel)

    @app_commands.command(name="donner", description="Donner des gemmes ou de l'or à un membre")
    @app_commands.describe(membre="Le bénéficiaire", monnaie="Gemmes ou or", montant="Montant")
    @app_commands.choices(monnaie=CURRENCY_CHOICES)
    async def donner(self, interaction: discord.Interaction, membre: discord.Member, monnaie: app_commands.Choice[str],
                     montant: app_commands.Range[int, 1, 10_000_000]) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg
        if not cfg.gifts.enabled:
            raise GameError("Les dons sont désactivés.")
        sender = interaction.user
        if membre.bot or membre.id == sender.id:
            raise GameError("Choisis un autre membre.")
        currency = monnaie.value
        cap = int(cfg.gifts.daily_cap.get(currency, 0))
        day = timeutil.day_key(cfg)

        def _tx(conn):
            player = ensure_sync(conn, cfg, sender.id)
            ensure_sync(conn, cfg, membre.id)
            if player["level"] < int(cfg.gifts.min_level):
                raise GameError(f"Il faut être niveau {cfg.gifts.min_level} pour faire des dons.")
            given = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM gifts WHERE sender = ? AND currency = ? AND day = ?",
                                 (sender.id, currency, day)).fetchone()[0]
            if given + montant > cap:
                raise GameError(f"Plafond de dons atteint : tu peux encore donner {num(max(0, cap - given))} "
                                f"{monnaie.name} aujourd'hui.")
            if not debit_sync(conn, sender.id, {currency: montant}):
                raise GameError("Tu n'as pas assez de ressources.")
            credit_sync(conn, membre.id, {currency: montant})
            conn.execute("INSERT INTO gifts (sender, receiver, currency, amount, day) VALUES (?, ?, ?, ?, ?)",
                         (sender.id, membre.id, currency, montant, day))
            return bot.progress.record_sync(conn, sender.id, "gift")

        unlocked = await bot.db.transaction(_tx)
        await interaction.response.send_message(
            f"🎁 {sender.mention} offre {format_rewards({currency: montant})} à {membre.mention} !",
            allowed_mentions=discord.AllowedMentions(users=[membre]))
        await bot.notify_achievements(sender, unlocked, interaction.channel)


async def setup(bot) -> None:
    await bot.add_cog(Shop(bot))
