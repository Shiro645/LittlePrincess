"""Configuration depuis Discord : /config (réservé au staff).

Toutes les modifications passent par la même étape : copie de la config actuelle,
modification, validation, enregistrement dans config.json (ancienne version gardée
dans config.json.bak), rechargement immédiat et trace dans le salon staff.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..client import is_staff
from ..config import (ConfigError, coerce_value, deep_merge, delete_path, dumps, get_path, has_path, help_for,
                      iter_paths, load_defaults, plain, set_path, split_path, type_name)
from ..ui import format as ui
from ._common import bot_of, staff_only

log = logging.getLogger(__name__)

CHANNEL_FUNCTIONS = [
    app_commands.Choice(name="Annonces des nouveautés", value="announcements"),
    app_commands.Choice(name="Alertes du staff", value="admin"),
    app_commands.Choice(name="Montées de niveau", value="level_up"),
    app_commands.Choice(name="Succès débloqués", value="achievements"),
    app_commands.Choice(name="Boss du samedi", value="boss"),
    app_commands.Choice(name="Victoires du wordle", value="wordle"),
]
CHANNEL_LISTS = [
    app_commands.Choice(name="Salons autorisés pour les commandes", value="allowed"),
    app_commands.Choice(name="Salons qui ne rapportent pas d'XP", value="xp_excluded"),
]
LIST_ACTIONS = [
    app_commands.Choice(name="Ajouter", value="add"),
    app_commands.Choice(name="Retirer", value="remove"),
    app_commands.Choice(name="Vider la liste", value="clear"),
]
RESET_ALL = "tout"


def short(value: Any, limit: int = 60) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def path_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot = bot_of(interaction)
    if not is_staff(bot.cfg, interaction.user):
        return []
    query = current.strip().lower().replace("/", ".")
    paths = list(iter_paths(plain(bot.cfg)))
    if query:
        matches = [(p, v) for p, v in paths if query in p.lower()]
        matches.sort(key=lambda pv: (not pv[0].lower().startswith(query), pv[0].count("."), len(pv[0])))
    else:
        matches = [(p, v) for p, v in paths if "." not in p]
    choices = []
    for path, value in matches[:25]:
        if isinstance(value, dict):
            label = f"{path}  {{…}}"
        elif isinstance(value, list):
            label = f"{path}  [{len(value)}]"
        else:
            label = f"{path} = {short(value, 50)}"
        choices.append(app_commands.Choice(name=label[:100], value=path[:100]))
    return choices


def _list_update(values: list, item: Optional[int], action: str, label: str) -> tuple[list, str]:
    values = list(values)
    if action == "clear":
        return [], "liste vidée"
    if item is None:
        raise ConfigError(f"Précise le {label} à {'ajouter' if action == 'add' else 'retirer'}.")
    if action == "add":
        if item in values:
            raise ConfigError(f"Ce {label} est déjà dans la liste.")
        return values + [item], "ajout"
    if item not in values:
        raise ConfigError(f"Ce {label} n'est pas dans la liste.")
    values.remove(item)
    return values, "retrait"


class Settings(commands.Cog):
    config = app_commands.Group(name="config", description="Configuration du bot (staff)", guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    # -- mécanique commune ------------------------------------------------------

    async def edit(self, interaction: discord.Interaction, mutate: Callable[[dict], str]) -> str:
        """Applique `mutate` sur une copie de la config, valide, enregistre et recharge."""
        async with self.lock:
            data = plain(self.bot.cfg)
            summary = mutate(data)
            self.bot.save_config(data)
        log.info("Config modifiée par %s : %s", interaction.user, summary)
        await self.bot.notify_staff(f"⚙️ {interaction.user.mention} a modifié la config : {summary}"[:1900])
        return summary

    # -- consultation -----------------------------------------------------------

    @config.command(name="voir", description="Afficher la configuration ou un paramètre")
    @app_commands.describe(parametre="Ex. gacha.pity_hero ou boss (laisser vide pour le sommaire)")
    @app_commands.autocomplete(parametre=path_autocomplete)
    @staff_only()
    async def voir(self, interaction: discord.Interaction, parametre: Optional[str] = None) -> None:
        bot = bot_of(interaction)
        data = plain(bot.cfg)
        if not parametre:
            lines = [f"**{key}** — {help_for(key) or ''}"[:200] for key in data]
            e = ui.embed(bot.cfg, "⚙️ Configuration", "\n".join(lines))
            e.set_footer(text="/config voir <section> pour le détail · /config modifier pour changer une valeur")
            await interaction.response.send_message(embed=e, ephemeral=True)
            return
        path = ".".join(split_path(parametre))
        value = get_path(data, path)
        defaults = load_defaults()
        e = ui.embed(bot.cfg, f"⚙️ {path}", help_for(path) or "")
        rendered = dumps(value)
        file = discord.utils.MISSING
        if len(rendered) > 1000:
            file = discord.File(io.BytesIO(rendered.encode("utf-8")), filename=f"{path}.json")
            e.add_field(name="Valeur", value="Trop longue pour l'aperçu : voir le fichier joint.", inline=False)
        else:
            e.add_field(name="Valeur", value=f"```json\n{rendered}\n```", inline=False)
        if not isinstance(value, (dict, list)):
            e.add_field(name="Type", value=type_name(value))
            if has_path(defaults, path):
                e.add_field(name="Par défaut", value=f"`{short(get_path(defaults, path))}`")
        e.set_footer(text=f"/config modifier parametre:{path} valeur:…")
        await interaction.response.send_message(embed=e, file=file, ephemeral=True)

    @config.command(name="exporter", description="Télécharger le fichier config.json actuel")
    @staff_only()
    async def exporter(self, interaction: discord.Interaction) -> None:
        content = dumps(plain(bot_of(interaction).cfg)) + "\n"
        await interaction.response.send_message(
            "📄 Configuration actuelle. Tu peux la modifier puis la renvoyer avec `/config importer`.",
            file=discord.File(io.BytesIO(content.encode("utf-8")), filename="config.json"), ephemeral=True)

    # -- modification -----------------------------------------------------------

    @config.command(name="modifier", description="Modifier un paramètre")
    @app_commands.describe(parametre="Ex. gacha.pity_hero, boss.hour, shop.items.0.price.gold",
                           valeur="Nouvelle valeur : nombre, true/false, texte, ou JSON pour une liste/un objet")
    @app_commands.autocomplete(parametre=path_autocomplete)
    @staff_only()
    async def modifier(self, interaction: discord.Interaction, parametre: str, valeur: str) -> None:
        def mutate(data: dict) -> str:
            path = ".".join(split_path(parametre))
            current = get_path(data, path) if has_path(data, path) else None
            new = coerce_value(valeur, current)
            if new == current:
                raise ConfigError("La valeur est déjà celle-ci.")
            set_path(data, path, new)
            return f"`{path}` : `{short(current)}` → `{short(new)}`"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True)

    @config.command(name="reinitialiser", description="Remettre un paramètre (ou tout, avec « tout ») à sa valeur par défaut")
    @app_commands.describe(parametre="Ex. gacha.pity_hero, une section comme boss, ou « tout »",
                           confirmer="Obligatoire pour tout réinitialiser")
    @app_commands.autocomplete(parametre=path_autocomplete)
    @staff_only()
    async def reinitialiser(self, interaction: discord.Interaction, parametre: str, confirmer: bool = False) -> None:
        defaults = load_defaults()
        if parametre.strip().lower() == RESET_ALL:
            if not confirmer:
                raise ConfigError("Cela remet TOUTE la configuration par défaut (salons et rôles compris). "
                                  "Relance avec `confirmer: True`.")

            def mutate_all(data: dict) -> str:
                data.clear()
                data.update(defaults)
                return "toute la configuration remise par défaut"

            summary = await self.edit(interaction, mutate_all)
            await interaction.response.send_message(f"✅ {summary}", ephemeral=True)
            return

        def mutate(data: dict) -> str:
            path = ".".join(split_path(parametre))
            if not has_path(defaults, path):
                raise ConfigError("Ce paramètre n'a pas de valeur par défaut : utilise `/config supprimer`.")
            old = get_path(data, path) if has_path(data, path) else None
            default = get_path(defaults, path)
            set_path(data, path, default)
            return f"`{path}` : `{short(old)}` → `{short(default)}` (défaut)"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True)

    @config.command(name="supprimer", description="Supprimer un élément de liste (ex. shop.items.3) ou une entrée niveau → valeur")
    @app_commands.describe(parametre="Ex. shop.items.3, boss.roster.4, leveling.level_roles.10")
    @app_commands.autocomplete(parametre=path_autocomplete)
    @staff_only()
    async def supprimer(self, interaction: discord.Interaction, parametre: str) -> None:
        def mutate(data: dict) -> str:
            path = ".".join(split_path(parametre))
            removed = delete_path(data, path)
            return f"`{path}` supprimé (était `{short(removed)}`)"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True)

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

    # -- raccourcis pour les salons et les rôles ---------------------------------

    @config.command(name="salon", description="Choisir le salon d'une fonction du bot (vide = désactiver)")
    @app_commands.describe(fonction="À quoi sert ce salon", salon="Le salon (laisser vide pour désactiver)")
    @app_commands.choices(fonction=CHANNEL_FUNCTIONS)
    @staff_only()
    async def salon(self, interaction: discord.Interaction, fonction: app_commands.Choice[str],
                    salon: Optional[discord.TextChannel] = None) -> None:
        def mutate(data: dict) -> str:
            data["channels"][fonction.value] = salon.id if salon else 0
            return f"{fonction.name} → {salon.mention if salon else 'désactivé'}"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True)

    @config.command(name="salons", description="Gérer les listes de salons (commandes autorisées, salons sans XP)")
    @app_commands.describe(liste="La liste à modifier", action="Ajouter, retirer ou vider", salon="Le salon concerné")
    @app_commands.choices(liste=CHANNEL_LISTS, action=LIST_ACTIONS)
    @staff_only()
    async def salons(self, interaction: discord.Interaction, liste: app_commands.Choice[str],
                     action: app_commands.Choice[str], salon: Optional[discord.abc.GuildChannel] = None) -> None:
        def mutate(data: dict) -> str:
            values, what = _list_update(data["channels"][liste.value], salon.id if salon else None, action.value, "salon")
            data["channels"][liste.value] = values
            target = f" {salon.mention}" if salon and action.value != "clear" else ""
            current = " ".join(f"<#{c}>" for c in values) or "vide"
            return f"{liste.name} : {what}{target} (maintenant : {current})"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True)

    @config.command(name="staff", description="Gérer les rôles qui ont accès aux commandes du staff")
    @app_commands.describe(action="Ajouter, retirer ou vider", role="Le rôle concerné")
    @app_commands.choices(action=LIST_ACTIONS)
    @staff_only()
    async def staff(self, interaction: discord.Interaction, action: app_commands.Choice[str],
                    role: Optional[discord.Role] = None) -> None:
        def mutate(data: dict) -> str:
            values, what = _list_update(data["roles"]["staff"], role.id if role else None, action.value, "rôle")
            data["roles"]["staff"] = values
            current = " ".join(f"<@&{r}>" for r in values) or "aucun (seuls les administrateurs)"
            return f"rôles staff : {what} (maintenant : {current})"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @config.command(name="role-boss", description="Rôle mentionné quand le boss apparaît (vide = aucune mention)")
    @staff_only()
    async def role_boss(self, interaction: discord.Interaction, role: Optional[discord.Role] = None) -> None:
        def mutate(data: dict) -> str:
            data["roles"]["boss_ping"] = role.id if role else 0
            return f"rôle du boss → {role.mention if role else 'aucun'}"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @config.command(name="role-niveau", description="Rôle donné automatiquement à un niveau (vide = retirer)")
    @app_commands.describe(niveau="Niveau du compte", role="Le rôle à donner (laisser vide pour retirer ce palier)")
    @staff_only()
    async def role_niveau(self, interaction: discord.Interaction, niveau: app_commands.Range[int, 1, 1000],
                          role: Optional[discord.Role] = None) -> None:
        if role is not None and interaction.guild and interaction.guild.me \
                and role >= interaction.guild.me.top_role:
            raise ConfigError(f"Le rôle {role.mention} est au-dessus du rôle du bot : déplace le rôle du bot plus haut "
                              "dans les paramètres du serveur, sinon il ne pourra pas le donner.")

        def mutate(data: dict) -> str:
            roles = data["leveling"].setdefault("level_roles", {})
            if role is None:
                if str(niveau) not in roles:
                    raise ConfigError(f"Aucun rôle n'est configuré pour le niveau {niveau}.")
                roles.pop(str(niveau))
                return f"rôle du niveau {niveau} retiré"
            roles[str(niveau)] = role.id
            data["leveling"]["level_roles"] = dict(sorted(roles.items(), key=lambda kv: int(kv[0])))
            return f"niveau {niveau} → {role.mention}"

        summary = await self.edit(interaction, mutate)
        await interaction.response.send_message(f"✅ {summary}", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())


async def setup(bot) -> None:
    await bot.add_cog(Settings(bot))
