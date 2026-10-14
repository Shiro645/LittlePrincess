"""Administration : /bot (version, mise à jour, synchro) et /admin (gestion des joueurs, boss)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import updates
from ..services.collection import GameError
from ..services.players import CURRENCIES, LABELS, credit_sync, debit_sync, ensure_sync, format_rewards
from ..ui import format as ui
from ._common import bot_of, hero_autocomplete, resolve_hero, staff_only

log = logging.getLogger(__name__)

CURRENCY_CHOICES = [app_commands.Choice(name=f"{LABELS[c]}", value=c) for c in CURRENCIES]


class Admin(commands.Cog):
    botgrp = app_commands.Group(name="bot", description="Gestion du bot (staff)", guild_only=True)
    admin = app_commands.Group(name="admin", description="Commandes du staff", guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        self.started = time.time()
        self.syncing = False

    async def cog_load(self) -> None:
        self.housekeeping.start()

    async def cog_unload(self) -> None:
        self.housekeeping.cancel()

    # -- démarrage : annonce des nouveautés et synchro des héros ---------------

    @commands.Cog.listener()
    async def on_first_ready(self) -> None:
        if self.bot.config_problems:
            await self.bot.notify_staff(
                "⚠️ `config.json` contient des erreurs, le bot utilise les valeurs par défaut :\n• "
                + "\n• ".join(self.bot.config_problems[:10])[:1800]
                + "\nCorrige le fichier puis `/bot recharger`, ou importe une config valide avec `/config importer`.")
        await self.announce_new_version()
        if self.bot.data.needs_sync() and os.getenv("GTALES_API_TOKEN"):
            asyncio.create_task(self.sync_heroes(notify=True))
        elif self.bot.data.source != "api":
            log.warning("GTALES_API_TOKEN absent : le bot utilise les héros de démarrage (stats approximatives)")

    async def announce_new_version(self) -> None:
        bot = self.bot
        current = bot.version
        announced = await bot.db.get_meta("announced_version")
        update_channel = await bot.db.get_meta("update_channel")
        if update_channel:
            await bot.db.set_meta("update_channel", "")
            channel = bot.get_channel(int(update_channel))
            if announced == current:
                await bot.safe_send(channel, f"ℹ️ Redémarré, mais toujours en v{current} (pas de nouvelle image).")
            else:
                await bot.safe_send(channel, f"✅ Mise à jour terminée : **v{current}**")
        if announced == current:
            return
        sections = updates.changes_since(updates.read_changelog(), announced, current)
        await bot.db.set_meta("announced_version", current)
        if not sections:
            return
        e = ui.embed(bot.cfg, f"🆕 {bot.cfg.general.bot_name} v{current}", "Voici les nouveautés :")
        for version, title, body in sections[-5:]:
            e.add_field(name=f"v{version} {title}"[:256], value=body[:1024] or "—", inline=False)
        await bot.safe_send(bot.channel("announcements"), embed=e)

    async def sync_heroes(self, notify: bool = False) -> str:
        if self.syncing:
            raise GameError("Une synchronisation est déjà en cours.")
        self.syncing = True
        try:
            count = await self.bot.data.sync_from_api(os.getenv("GTALES_API_TOKEN", "").strip(), self.bot.user_agent)
            moved = await self.bot.collection.migrate_seed_heroes()
            message = f"✅ {count} héros synchronisés depuis gtales.top" + (f" ({moved} héros de démarrage convertis)" if moved else "")
            log.info(message)
            if notify:
                await self.bot.notify_staff(message)
            return message
        except Exception as exc:
            log.exception("Synchronisation des héros impossible")
            if notify:
                await self.bot.notify_staff(f"⚠️ Synchronisation des héros impossible : {exc}")
            raise GameError(f"Synchronisation impossible : {exc}") from exc
        finally:
            self.syncing = False

    @tasks.loop(hours=1)
    async def housekeeping(self) -> None:
        bot = self.bot
        try:
            hours = float(bot.cfg.update.check_interval_hours or 0)
            last = float(await bot.db.get_meta("last_update_check", "0") or 0)
            if hours > 0 and time.time() - last >= hours * 3600:
                await bot.db.set_meta("last_update_check", str(time.time()))
                await self.check_remote(notify=True)
            if bot.data.needs_sync() and os.getenv("GTALES_API_TOKEN") and bot.data.source == "api" and not self.syncing:
                await self.sync_heroes(notify=False)
        except GameError:
            pass
        except Exception:
            log.exception("Erreur dans la tâche de maintenance")

    @housekeeping.before_loop
    async def before_housekeeping(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)

    async def check_remote(self, notify: bool) -> Optional[str]:
        cfg = self.bot.cfg.update
        remote = await updates.fetch_remote_version(str(cfg.repository), str(cfg.branch), self.bot.user_agent)
        if remote and updates.is_newer(remote, self.bot.version):
            notified = await self.bot.db.get_meta("notified_version")
            if notify and notified != remote:
                await self.bot.db.set_meta("notified_version", remote)
                await self.bot.notify_staff(
                    f"🆕 La version **v{remote}** est disponible (actuelle : v{self.bot.version}). "
                    "Lance `/bot update` pour l'installer (laisse quelques minutes à GitHub pour construire l'image).")
        return remote

    # -- /bot ------------------------------------------------------------------

    @botgrp.command(name="version", description="Version et état du bot")
    @staff_only()
    async def version(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        data = bot.data
        uptime = int(time.time() - self.started)
        e = ui.embed(bot.cfg, f"🤖 {bot.cfg.general.bot_name} v{bot.version}")
        source = "API gtales.top" if data.source == "api" else "⚠️ données de démarrage (pas de token API)"
        synced = data.fetched_at.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC") if data.fetched_at else "jamais"
        e.add_field(name="Données", value=f"{len(data.heroes)} héros · {len(data.weapons)} armes\n{source}\nSynchro : {synced}")
        e.add_field(name="En ligne depuis", value=f"{uptime // 3600} h {uptime % 3600 // 60} min")
        players = await bot.db.fetchval("SELECT COUNT(*) FROM players", default=0)
        e.add_field(name="Joueurs", value=str(players))
        e.set_footer(text=ui.ATTRIBUTION)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @botgrp.command(name="verifier", description="Vérifier s'il existe une nouvelle version")
    @staff_only()
    async def verifier(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        remote = await self.check_remote(notify=False)
        current = self.bot.version
        if remote is None:
            text = "Impossible de récupérer la version distante (dépôt inaccessible ?)."
        elif updates.is_newer(remote, current):
            text = f"🆕 v{remote} disponible (actuelle v{current}). Lance `/bot update`."
        else:
            text = f"✅ Le bot est à jour (v{current})."
        await interaction.followup.send(text, ephemeral=True)

    @botgrp.command(name="update", description="Mettre à jour le bot (via Watchtower) et annoncer les nouveautés")
    @staff_only()
    async def update(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        await interaction.response.send_message(
            "🔄 Mise à jour demandée à Watchtower… Si une nouvelle image existe, le bot va redémarrer "
            "et annoncer les nouveautés.", ephemeral=True)
        await bot.db.set_meta("update_channel", str(interaction.channel_id))
        ok, detail = await updates.trigger_watchtower(str(bot.cfg.update.watchtower_url))
        # Si on arrive ici, le bot n'a pas été redémarré
        await bot.db.set_meta("update_channel", "")
        if ok:
            await interaction.followup.send(
                f"ℹ️ Watchtower n'a pas redémarré le bot : aucune nouvelle image trouvée (v{bot.version}). "
                "Si tu viens de publier une mise à jour, l'image est peut-être encore en construction sur GitHub.",
                ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Échec de la mise à jour : {detail}", ephemeral=True)

    @botgrp.command(name="sync", description="Resynchroniser les héros depuis l'API gtales.top")
    @staff_only()
    async def sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await self.sync_heroes()
        await interaction.followup.send(message, ephemeral=True)

    @botgrp.command(name="commandes", description="Resynchroniser les commandes / avec Discord")
    @staff_only()
    async def commandes(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        count, removed = await self.bot.sync_commands()
        text = f"✅ {count} commandes synchronisées sur le serveur."
        if removed:
            text += f"\n🧹 Anciennes commandes globales supprimées : {', '.join(f'`/{n}`' for n in removed)}"
        text += "\nSi Discord affiche encore d'anciennes commandes, recharge l'application (Ctrl+R)."
        await interaction.followup.send(text, ephemeral=True)

    @botgrp.command(name="recharger", description="Relire config.json après une modification à la main")
    @staff_only()
    async def recharger(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        bot.reload_config()
        await interaction.response.send_message("✅ Configuration rechargée.", ephemeral=True)

    # -- /admin ----------------------------------------------------------------

    @admin.command(name="donner", description="Donner des ressources à un membre")
    @app_commands.choices(ressource=CURRENCY_CHOICES)
    @staff_only()
    async def donner(self, interaction: discord.Interaction, membre: discord.Member,
                     ressource: app_commands.Choice[str], montant: app_commands.Range[int, 1, 100_000_000]) -> None:
        bot = bot_of(interaction)
        await bot.players.credit(membre.id, {ressource.value: montant})
        await interaction.response.send_message(f"✅ {membre.mention} reçoit {format_rewards({ressource.value: montant})}.",
                                                allowed_mentions=discord.AllowedMentions.none())

    @admin.command(name="retirer", description="Retirer des ressources à un membre")
    @app_commands.choices(ressource=CURRENCY_CHOICES)
    @staff_only()
    async def retirer(self, interaction: discord.Interaction, membre: discord.Member,
                      ressource: app_commands.Choice[str], montant: app_commands.Range[int, 1, 100_000_000]) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg

        def _tx(conn):
            player = ensure_sync(conn, cfg, membre.id)
            amount = min(montant, player[ressource.value])
            debit_sync(conn, membre.id, {ressource.value: amount})
            return amount

        amount = await bot.db.transaction(_tx)
        await interaction.response.send_message(f"✅ Retiré {format_rewards({ressource.value: amount})} à {membre.mention}.",
                                                allowed_mentions=discord.AllowedMentions.none())

    @admin.command(name="heros", description="Donner un héros à un membre (ou fixer ses étoiles)")
    @app_commands.autocomplete(heros=hero_autocomplete)
    @staff_only()
    async def heros(self, interaction: discord.Interaction, membre: discord.Member, heros: str,
                    etoiles: app_commands.Range[int, 1, 5] = 0) -> None:
        bot = bot_of(interaction)
        hero = resolve_hero(bot, heros)
        stars = etoiles or hero.rarity

        def _tx(conn):
            ensure_sync(conn, bot.cfg, membre.id)
            conn.execute("INSERT INTO heroes (user_id, hero_key, stars) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id, hero_key) DO UPDATE SET stars = excluded.stars",
                         (membre.id, hero.key, stars))

        await bot.db.transaction(_tx)
        await interaction.response.send_message(f"✅ {membre.mention} possède **{hero.name}** {ui.stars(stars)}.",
                                                allowed_mentions=discord.AllowedMentions.none())

    @admin.command(name="reset", description="Effacer toutes les données d'un membre")
    @staff_only()
    async def reset(self, interaction: discord.Interaction, membre: discord.Member, confirmer: bool = False) -> None:
        if not confirmer:
            await interaction.response.send_message(
                f"⚠️ Cela efface définitivement la progression de {membre.mention}. Relance avec `confirmer: True`.",
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        bot = bot_of(interaction)

        def _tx(conn):
            for table in ("players", "heroes", "weapons", "team", "stats", "missions", "achievements", "titles",
                          "wordle", "shop_purchases", "dummy_scores"):
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (membre.id,))

        await bot.db.transaction(_tx)
        await interaction.response.send_message(f"🗑️ Données de {membre.mention} effacées.", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @admin.command(name="boss-lancer", description="Lancer un raid de boss maintenant")
    @staff_only()
    async def boss_lancer(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("Boss")
        message = await cog.admin_start()  # type: ignore[union-attr]
        await interaction.response.send_message(message, ephemeral=True)

    @admin.command(name="boss-terminer", description="Terminer le boss en cours et distribuer les récompenses")
    @staff_only()
    async def boss_terminer(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("Boss")
        await interaction.response.defer(ephemeral=True)
        message = await cog.admin_finish()  # type: ignore[union-attr]
        await interaction.followup.send(message, ephemeral=True)

    @admin.command(name="annonce", description="Republier les nouveautés de la version actuelle")
    @staff_only()
    async def annonce(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        sections = updates.changes_since(updates.read_changelog(), None, bot.version)
        if not sections:
            raise GameError("Aucune entrée dans le CHANGELOG pour cette version.")
        version, title, body = sections[-1]
        e = ui.embed(bot.cfg, f"🆕 {bot.cfg.general.bot_name} v{version} {title}", body[:4000])
        channel = bot.channel("announcements") or interaction.channel
        await bot.safe_send(channel, embed=e)
        await interaction.response.send_message("📣 Annonce publiée.", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))
