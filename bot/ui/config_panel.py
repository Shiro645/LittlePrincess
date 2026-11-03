"""Interactive configuration panel (staff only).

The panel is a persistent message posted by `/config panneau`. Picking a section
opens an ephemeral browser where staff members navigate the config tree and edit
values through the most fitting control:

* booleans          -> toggled with one click
* channel / role IDs -> Discord channel or role pickers
* other values      -> a modal pre-filled with the current value (JSON for lists/objects)

Every change goes through `Settings.edit()`: copy, mutate, validate, save, reload.
"""

from __future__ import annotations

import copy
import io
import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

import discord

from ..config import (ConfigError, coerce_value, delete_path, dumps, get_path, has_path, help_for, load_defaults,
                      plain, set_path, split_path)
from . import format as ui

if TYPE_CHECKING:
    from ..cogs.settings import Settings

PANEL_SELECT_ID = "lp:config:section"
PANEL_EXPORT_ID = "lp:config:export"
PANEL_RELOAD_ID = "lp:config:reload"
PAGE_SIZE = 25
LEVEL_ROLES = "leveling.level_roles"
TEXT_CHANNELS = [discord.ChannelType.text, discord.ChannelType.news]


def short(value: Any, limit: int = 60) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


def parent_of(path: str) -> str:
    return ".".join(split_path(path)[:-1]) if path else ""


def node_kind(path: str, value: Any) -> str:
    """How a value is edited in the panel."""
    if path.startswith(LEVEL_ROLES + "."):
        return "level_role"
    if path.startswith("channels."):
        if isinstance(value, list):
            return "channels"
        if isinstance(value, int) and not isinstance(value, bool):
            return "channel"
    if path == "roles.staff":
        return "roles"
    if path == "roles.boss_ping":
        return "role"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (dict, list)):
        return "node"
    return "scalar"


def display_value(path: str, value: Any) -> str:
    kind = node_kind(path, value)
    if kind == "channel":
        return f"<#{value}>" if value else "*désactivé*"
    if kind == "channels":
        return " ".join(f"<#{c}>" for c in value) or "*aucun*"
    if kind in ("role", "level_role"):
        return f"<@&{value}>" if value else "*aucun*"
    if kind == "roles":
        return " ".join(f"<@&{r}>" for r in value) or "*aucun*"
    if kind == "bool":
        return "✅ oui" if value else "❌ non"
    if isinstance(value, dict):
        return f"{{…}} {len(value)} réglage(s)"
    if isinstance(value, list):
        if value and all(isinstance(v, dict) for v in value):
            return f"[{len(value)} élément(s)]"
        return f"`{short(value, 80)}`"
    return f"`{short(value, 80)}`"


def item_label(key: Any, value: Any) -> str:
    """Readable name for a child entry (list items show their name or id)."""
    if isinstance(value, dict):
        name = value.get("name") or value.get("id")
        if name and str(key).isdigit():
            return f"{key} — {name}"
    return str(key)


def panel_embed(bot) -> discord.Embed:
    cfg = bot.cfg
    e = ui.embed(cfg, "⚙️ Panneau de configuration",
                 "Choisis une section dans le menu ci-dessous pour voir et modifier ses réglages. "
                 "Les changements sont vérifiés puis appliqués immédiatement, sans redémarrer le bot.")
    lines = [f"**{key}** — {help_for(key) or ''}"[:120] for key in plain(cfg)]
    e.add_field(name="Sections", value="\n".join(lines)[:1024], inline=False)
    e.set_footer(text=f"Réservé au staff · v{bot.version} · chaque modification est tracée dans le salon staff")
    return e


async def _deny(interaction: discord.Interaction) -> bool:
    from ..client import is_staff  # local import: client imports the cogs indirectly

    if is_staff(interaction.client.cfg, interaction.user):
        return True
    await interaction.response.send_message("⛔ Ce panneau est réservé au staff.", ephemeral=True)
    return False


# ---------------------------------------------------------------------------
#  Persistent panel message
# ---------------------------------------------------------------------------

class ConfigPanelView(discord.ui.View):
    """Persistent view: works after a restart thanks to fixed custom_ids."""

    def __init__(self, bot):
        super().__init__(timeout=None)
        options = []
        for key in list(plain(bot.cfg))[:PAGE_SIZE]:
            desc = help_for(key) or ""
            options.append(discord.SelectOption(label=key[:100], value=key[:100], description=desc[:100] or None))
        select = discord.ui.Select(custom_id=PANEL_SELECT_ID, placeholder="Choisir une section…", options=options)
        select.callback = self._on_section
        self.add_item(select)
        export = discord.ui.Button(custom_id=PANEL_EXPORT_ID, label="Exporter", emoji="📄",
                                   style=discord.ButtonStyle.secondary)
        export.callback = self._on_export
        self.add_item(export)
        reload_ = discord.ui.Button(custom_id=PANEL_RELOAD_ID, label="Recharger le fichier", emoji="🔄",
                                    style=discord.ButtonStyle.secondary)
        reload_.callback = self._on_reload
        self.add_item(reload_)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _deny(interaction)

    async def _on_section(self, interaction: discord.Interaction) -> None:
        path = interaction.data["values"][0]
        cog = interaction.client.get_cog("Settings")
        embed, view = NodeView.build(cog, path)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _on_export(self, interaction: discord.Interaction) -> None:
        content = dumps(plain(interaction.client.cfg)) + "\n"
        await interaction.response.send_message(
            "📄 Configuration actuelle. Tu peux la modifier puis la renvoyer avec `/config importer`.",
            file=discord.File(io.BytesIO(content.encode("utf-8")), filename="config.json"), ephemeral=True)

    async def _on_reload(self, interaction: discord.Interaction) -> None:
        try:
            interaction.client.reload_config()
        except ConfigError as exc:
            await interaction.response.send_message(f"❌ {exc}"[:2000], ephemeral=True)
            return
        await interaction.response.send_message("✅ config.json relu et appliqué.", ephemeral=True)


# ---------------------------------------------------------------------------
#  Ephemeral config browser
# ---------------------------------------------------------------------------

class NodeView(discord.ui.View):
    """Shows one node of the config tree (a section, an object or a list) and its children."""

    def __init__(self, cog: "Settings", path: str, page: int = 0, confirm_reset: bool = False):
        super().__init__(timeout=900)
        self.cog, self.path, self.page, self.confirm_reset = cog, path, page, confirm_reset
        data = plain(cog.bot.cfg)
        self.value = get_path(data, path)
        children = list(self.value.items()) if isinstance(self.value, dict) else list(enumerate(self.value))
        self.pages = max(1, (len(children) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(page, self.pages - 1)
        shown = children[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]

        if shown:
            options = []
            for key, value in shown:
                child = join(path, key)
                kind = node_kind(child, value)
                emoji = {"node": "📁", "bool": "🔘", "channel": "#️⃣", "channels": "#️⃣", "role": "👥",
                         "roles": "👥", "level_role": "👥"}.get(kind, "✏️")
                desc = help_for(child) if kind == "node" else short(value, 90)
                options.append(discord.SelectOption(label=item_label(key, value)[:100], value=str(key)[:100],
                                                    description=(desc or "")[:100] or None, emoji=emoji))
            select = discord.ui.Select(placeholder="Choisir un réglage à modifier…", options=options, row=0)
            select.callback = self._on_pick
            self.add_item(select)

        if self.pages > 1:
            self._button("◀", self._prev, disabled=self.page == 0, row=1)
            self._button(f"{self.page + 1}/{self.pages}", None, disabled=True, row=1)
            self._button("▶", self._next, disabled=self.page >= self.pages - 1, row=1)
        if parent_of(path):
            self._button("Retour", self._back, emoji="⬅️", row=2)
        self._button("Modifier en JSON", self._edit_json, emoji="📝", row=2)
        if isinstance(self.value, list):
            self._button("Ajouter un élément", self._add_item, emoji="➕", row=2)
        if path == LEVEL_ROLES:
            self._button("Ajouter un palier", self._add_level_role, emoji="➕", row=2)
        if split_path(path)[-1].isdigit():
            self._button("Supprimer cet élément", self._delete_self, emoji="🗑️", style=discord.ButtonStyle.danger,
                         row=3)
        if has_path(load_defaults(), path):
            label = "Confirmer la réinitialisation" if confirm_reset else "Réinitialiser"
            style = discord.ButtonStyle.danger if confirm_reset else discord.ButtonStyle.secondary
            self._button(label, self._reset, emoji="↩️", style=style, row=3)

    # -- construction ---------------------------------------------------------

    @classmethod
    def build(cls, cog: "Settings", path: str, page: int = 0, status: str = "",
              confirm_reset: bool = False) -> tuple[discord.Embed, "NodeView"]:
        view = cls(cog, path, page, confirm_reset)
        return view.embed(status), view

    def embed(self, status: str = "") -> discord.Embed:
        cfg = self.cog.bot.cfg
        desc = [status, ""] if status else []
        if help_for(self.path):
            desc.append(f"*{help_for(self.path)}*")
        children = list(self.value.items()) if isinstance(self.value, dict) else list(enumerate(self.value))
        lines = []
        for key, value in children[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]:
            lines.append(f"**{item_label(key, value)}** : {display_value(join(self.path, key), value)}")
        desc.append("\n".join(lines) or "*vide*")
        e = ui.embed(cfg, f"⚙️ {self.path}", "\n".join(desc)[:4000])
        e.set_footer(text="Choisis un réglage dans le menu pour le modifier")
        return e

    def _button(self, label: str, callback: Optional[Callable[[discord.Interaction], Awaitable[None]]], *,
                emoji: Optional[str] = None, style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                disabled: bool = False, row: int = 1) -> None:
        button = discord.ui.Button(label=label, emoji=emoji, style=style, disabled=disabled, row=row)
        if callback is not None:
            button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _deny(interaction)

    async def show(self, interaction: discord.Interaction, path: Optional[str] = None, page: int = 0,
                   status: str = "", confirm_reset: bool = False) -> None:
        embed, view = NodeView.build(self.cog, path if path is not None else self.path, page, status, confirm_reset)
        await interaction.response.edit_message(embed=embed, view=view)

    async def apply(self, interaction: discord.Interaction, mutate: Callable[[dict], str],
                    back_to: Optional[str] = None) -> None:
        """Apply a change and refresh the browser (errors are shown without leaving the screen)."""
        try:
            summary = await self.cog.edit(interaction, mutate)
        except ConfigError as exc:
            await interaction.response.send_message(f"❌ {exc}"[:2000], ephemeral=True)
            return
        target = back_to if back_to is not None else self.path
        embed, view = NodeView.build(self.cog, target, 0 if target != self.path else self.page, f"✅ {summary}")
        await interaction.response.edit_message(embed=embed, view=view)

    # -- navigation -------------------------------------------------------------

    async def _prev(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, page=self.page - 1)

    async def _next(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, page=self.page + 1)

    async def _back(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, path=parent_of(self.path))

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        key = interaction.data["values"][0]
        child = join(self.path, key)
        value = get_path(plain(self.cog.bot.cfg), child)
        kind = node_kind(child, value)
        if kind == "node":
            await self.show(interaction, path=child)
        elif kind == "bool":
            await self.apply(interaction, _setter(child, not value))
        elif kind in ("channel", "channels", "role", "roles", "level_role"):
            embed, view = PickerView.build(self, child, kind, value)
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.send_modal(ValueModal(self, child, value))

    # -- editing ------------------------------------------------------------------

    async def _edit_json(self, interaction: discord.Interaction) -> None:
        text = dumps(self.value)
        if len(text) > 4000:
            await interaction.response.send_message(
                "Cette section est trop longue pour un formulaire Discord : ouvre un sous-élément, "
                "ou utilise le bouton **Exporter** puis `/config importer`.", ephemeral=True)
            return
        await interaction.response.send_modal(ValueModal(self, self.path, self.value, json_mode=True))

    async def _add_item(self, interaction: discord.Interaction) -> None:
        template = copy.deepcopy(self.value[0]) if self.value else {}
        if isinstance(template, dict) and "id" in template:
            template["id"] = f"{template['id']}_copie"
        modal = ValueModal(self, join(self.path, len(self.value)), template, json_mode=True,
                           title="Nouvel élément")
        await interaction.response.send_modal(modal)

    async def _add_level_role(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(LevelModal(self))

    async def _delete_self(self, interaction: discord.Interaction) -> None:
        path = self.path

        def mutate(data: dict) -> str:
            removed = delete_path(data, path)
            return f"`{path}` supprimé (était `{short(removed)}`)"

        await self.apply(interaction, mutate, back_to=parent_of(path))

    async def _reset(self, interaction: discord.Interaction) -> None:
        if not self.confirm_reset:
            await self.show(interaction, page=self.page, status="⚠️ Clique à nouveau pour confirmer la réinitialisation.",
                            confirm_reset=True)
            return
        path, default = self.path, get_path(load_defaults(), self.path)

        def mutate(data: dict) -> str:
            set_path(data, path, copy.deepcopy(default))
            return f"`{path}` remis par défaut"

        await self.apply(interaction, mutate)


def _setter(path: str, value: Any) -> Callable[[dict], str]:
    def mutate(data: dict) -> str:
        old = get_path(data, path) if has_path(data, path) else None
        if old == value:
            raise ConfigError("La valeur est déjà celle-ci.")
        set_path(data, path, value)
        return f"`{path}` : `{short(old)}` → `{short(value)}`"

    return mutate


class ValueModal(discord.ui.Modal):
    """Form pre-filled with the current value."""

    def __init__(self, browser: NodeView, path: str, current: Any, json_mode: bool = False,
                 title: Optional[str] = None):
        super().__init__(title=(title or f"Modifier {split_path(path)[-1]}")[:45], timeout=900)
        self.browser, self.path, self.current = browser, path, current
        text = dumps(current) if json_mode or isinstance(current, (dict, list)) else str(current)
        long_text = json_mode or len(text) > 80 or "\n" in text
        self.field = discord.ui.TextInput(
            label=path[-45:], default=text[:4000], max_length=4000, required=False,
            style=discord.TextStyle.paragraph if long_text else discord.TextStyle.short,
            placeholder=(help_for(path) or "Nouvelle valeur")[:100])
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        path, raw = self.path, self.field.value
        is_new = not has_path(plain(self.browser.cog.bot.cfg), path)

        def mutate(data: dict) -> str:
            current = None if is_new else get_path(data, path)
            new = coerce_value(raw, current if not is_new else self.current)
            if not is_new and new == current:
                raise ConfigError("La valeur est déjà celle-ci.")
            set_path(data, path, new)
            if is_new:
                return f"`{path}` ajouté"
            return f"`{path}` : `{short(current)}` → `{short(new)}`"

        await self.browser.apply(interaction, mutate, back_to=self.browser.path)


class LevelModal(discord.ui.Modal, title="Nouveau palier de rôle"):
    level = discord.ui.TextInput(label="Niveau du compte", placeholder="Ex. 10", max_length=4)

    def __init__(self, browser: NodeView):
        super().__init__(timeout=900)
        self.browser = browser

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.level.value.strip().isdigit() or int(self.level.value) < 1:
            await interaction.response.send_message("❌ Indique un niveau (nombre entier positif).", ephemeral=True)
            return
        path = join(LEVEL_ROLES, int(self.level.value))
        embed, view = PickerView.build(self.browser, path, "level_role", 0)
        await interaction.response.edit_message(embed=embed, view=view)


class PickerView(discord.ui.View):
    """Discord channel / role pickers for ID settings."""

    def __init__(self, browser: NodeView, path: str, kind: str):
        super().__init__(timeout=900)
        self.browser, self.path, self.kind = browser, path, kind
        multi = kind in ("channels", "roles")
        if kind in ("channel", "channels"):
            select = discord.ui.ChannelSelect(channel_types=TEXT_CHANNELS, min_values=0 if multi else 1,
                                              max_values=25 if multi else 1, placeholder="Choisir le(s) salon(s)…")
        else:
            select = discord.ui.RoleSelect(min_values=0 if multi else 1, max_values=25 if multi else 1,
                                           placeholder="Choisir le(s) rôle(s)…")
        select.callback = self._on_select
        self.add_item(select)
        clear_label = {"channel": "Désactiver", "role": "Aucun rôle", "level_role": "Supprimer ce palier"}.get(
            kind, "Vider la liste")
        clear = discord.ui.Button(label=clear_label, emoji="🚫", style=discord.ButtonStyle.danger)
        clear.callback = self._on_clear
        self.add_item(clear)
        back = discord.ui.Button(label="Retour", emoji="⬅️", style=discord.ButtonStyle.secondary)
        back.callback = self._on_back
        self.add_item(back)

    @classmethod
    def build(cls, browser: NodeView, path: str, kind: str, value: Any) -> tuple[discord.Embed, "PickerView"]:
        e = ui.embed(browser.cog.bot.cfg, f"⚙️ {path}", f"*{help_for(path) or ''}*\n\n"
                                                         f"Valeur actuelle : {display_value(path, value)}")
        if kind in ("channels", "roles"):
            e.set_footer(text="La sélection remplace la liste actuelle")
        return e, cls(browser, path, kind)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _deny(interaction)

    def _parent(self) -> str:
        return parent_of(self.path)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select = self.children[0]
        if self.kind == "level_role":
            role = select.values[0]
            me = interaction.guild.me if interaction.guild else None
            if me and role >= me.top_role:
                await interaction.response.send_message(
                    f"❌ {role.mention} est au-dessus du rôle du bot : déplace le rôle du bot plus haut dans les "
                    "paramètres du serveur, sinon il ne pourra pas le donner.", ephemeral=True)
                return
        ids = [item.id for item in select.values]
        value: Any = ids if self.kind in ("channels", "roles") else (ids[0] if ids else 0)
        if self.kind == "level_role":
            path = self.path

            def mutate(data: dict) -> str:
                roles = data["leveling"].setdefault("level_roles", {})
                roles[split_path(path)[-1]] = value
                data["leveling"]["level_roles"] = dict(sorted(roles.items(), key=lambda kv: int(kv[0])))
                return f"niveau {split_path(path)[-1]} → <@&{value}>"

            await self._apply(interaction, mutate)
        else:
            await self._apply(interaction, _setter(self.path, value))

    async def _on_clear(self, interaction: discord.Interaction) -> None:
        path = self.path
        if self.kind == "level_role":
            if not has_path(plain(self.browser.cog.bot.cfg), path):
                await self._on_back(interaction)
                return

            def mutate(data: dict) -> str:
                delete_path(data, path)
                return f"palier du niveau {split_path(path)[-1]} supprimé"

            await self._apply(interaction, mutate)
        else:
            await self._apply(interaction, _setter(path, [] if self.kind in ("channels", "roles") else 0))

    async def _on_back(self, interaction: discord.Interaction) -> None:
        embed, view = NodeView.build(self.browser.cog, self._parent())
        await interaction.response.edit_message(embed=embed, view=view)

    async def _apply(self, interaction: discord.Interaction, mutate: Callable[[dict], str]) -> None:
        try:
            summary = await self.browser.cog.edit(interaction, mutate)
        except ConfigError as exc:
            await interaction.response.send_message(f"❌ {exc}"[:2000], ephemeral=True)
            return
        embed, view = NodeView.build(self.browser.cog, self._parent(), status=f"✅ {summary}")
        await interaction.response.edit_message(embed=embed, view=view,
                                                allowed_mentions=discord.AllowedMentions.none())
