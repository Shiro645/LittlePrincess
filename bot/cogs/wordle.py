"""Wordle de héros : /wordle deviner, /wordle classement."""

from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from .. import timeutil
from ..game import wordle
from ..services.collection import GameError
from ..services.leveling import add_xp_sync
from ..services.players import credit_sync, ensure_sync, format_rewards
from ..ui import format as ui
from ._common import bot_of, hero_autocomplete, resolve_hero

STATUS_EMOJI = {wordle.OK: "🟩", wordle.NO: "🟥", wordle.UP: "🔼", wordle.DOWN: "🔽"}


def describe_value(cfg, attribute: str, value) -> str:
    if attribute == "element":
        return f"{ui.ELEMENT_EMOJI.get(value, '')} {ui.ELEMENT_FR.get(value, value)}"
    if attribute == "role":
        return ui.ROLE_FR.get(value, value)
    if attribute == "rarity":
        return ui.stars(value)
    if attribute == "weapon_type":
        return cfg.weapons.type_names.get(value, value)
    return ui.STATE_FR.get(value, "—")


class Wordle(commands.Cog):
    wordle = app_commands.Group(name="wordle", description="Devine le héros du jour")

    def __init__(self, bot):
        self.bot = bot

    async def answer_for(self, day: str) -> str:
        key = await self.bot.db.get_meta(f"wordle:{day}")
        if key and key in self.bot.data.heroes:
            return key
        key = wordle.pick_answer(self.bot.data, day, str(self.bot.cfg.wordle.salt))
        await self.bot.db.set_meta(f"wordle:{day}", key)
        return key

    def board(self, guesses: list[str], answer_key: str, solved: bool, max_attempts: int) -> discord.Embed:
        cfg, data = self.bot.cfg, self.bot.data
        answer = data.hero(answer_key)
        header = " · ".join(label for _, label in wordle.ATTRIBUTES)
        e = ui.embed(cfg, "🧩 Wordle des héros", f"Essais : {len(guesses)}/{max_attempts}\n*{header}*")
        for key in guesses:
            hero = data.hero(key)
            if hero is None:
                continue
            clues = wordle.compare(hero, answer)
            line = "  ".join(f"{STATUS_EMOJI[c.status]} {describe_value(cfg, c.attribute, c.value)}" for c in clues)
            e.add_field(name=("✅ " if key == answer_key else "") + hero.name, value=line, inline=False)
        if not solved and len(guesses) >= int(cfg.wordle.hint_after) and answer.party_text:
            e.add_field(name="💡 Indice — buff d'équipe", value=answer.party_text, inline=False)
        e.set_footer(text="🟩 correct · 🟥 incorrect · 🔼 rareté plus haute · 🔽 plus basse")
        return e

    @wordle.command(name="deviner", description="Propose un héros pour le wordle du jour")
    @app_commands.describe(heros="Ta proposition")
    @app_commands.autocomplete(heros=hero_autocomplete)
    async def deviner(self, interaction: discord.Interaction, heros: str) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg
        hero = resolve_hero(bot, heros)
        day = timeutil.day_key(cfg)
        answer_key = await self.answer_for(day)
        max_attempts = int(cfg.wordle.max_attempts)
        user_id = interaction.user.id

        def _tx(conn):
            ensure_sync(conn, cfg, user_id)
            row = conn.execute("SELECT guesses, solved FROM wordle WHERE user_id = ? AND day = ?", (user_id, day)).fetchone()
            guesses = json.loads(row["guesses"]) if row else []
            if row and row["solved"]:
                raise GameError("Tu as déjà trouvé le héros du jour ! Reviens demain.")
            if len(guesses) >= max_attempts:
                raise GameError("Plus d'essais pour aujourd'hui. Reviens demain !")
            if hero.key in guesses:
                raise GameError(f"Tu as déjà proposé **{hero.name}**.")
            guesses.append(hero.key)
            solved = hero.key == answer_key
            conn.execute(
                "INSERT INTO wordle (user_id, day, guesses, solved) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, day) DO UPDATE SET guesses = excluded.guesses, solved = excluded.solved",
                (user_id, day, json.dumps(guesses), int(solved)),
            )
            unlocked = []
            reward = {}
            xp_result = None
            if len(guesses) == 1:
                unlocked += bot.progress.record_sync(conn, user_id, "wordle_play")
            if solved:
                rewards = cfg.wordle.rewards
                reward = dict(rewards[min(len(guesses), len(rewards)) - 1])
                credit_sync(conn, user_id, reward)
                unlocked += bot.progress.record_sync(conn, user_id, "wordle_win")
                xp_result = add_xp_sync(conn, cfg, user_id, int(cfg.leveling.minigame_xp.wordle_win))
            return guesses, solved, reward, unlocked, xp_result

        guesses, solved, reward, unlocked, xp_result = await bot.db.transaction(_tx)
        e = self.board(guesses, answer_key, solved, max_attempts)
        answer = bot.data.hero(answer_key)
        if solved:
            e.description = (f"🎉 Bravo, c'était **{answer.name}** ! Trouvé en {len(guesses)} essai(s).\n"
                             f"Récompense : {format_rewards(reward)}")
            if answer.image_url:
                e.set_thumbnail(url=answer.image_url)
        elif len(guesses) >= max_attempts:
            e.description = f"😢 Perdu ! C'était **{answer.name}**. Nouveau héros {timeutil.discord_ts(timeutil.next_reset(cfg))}."
        await interaction.response.send_message(embed=e, ephemeral=True)
        if solved:
            channel = bot.channel("wordle") or interaction.channel
            await bot.safe_send(channel, f"🧩 {interaction.user.mention} a trouvé le héros du jour en "
                                         f"**{len(guesses)}** essai(s) ! (`/wordle deviner`)",
                                allowed_mentions=discord.AllowedMentions(users=[interaction.user]))
            if xp_result:
                xp_result.achievements += unlocked
                await bot.handle_xp_result(interaction.user, xp_result, interaction.channel)
                return
        await bot.notify_achievements(interaction.user, unlocked, interaction.channel)

    @wordle.command(name="classement", description="Qui a trouvé le héros du jour ?")
    async def classement(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        day = timeutil.day_key(bot.cfg)
        rows = await bot.db.fetchall("SELECT user_id, guesses FROM wordle WHERE day = ? AND solved = 1", (day,))
        ranked = sorted(rows, key=lambda r: len(json.loads(r["guesses"])))
        lines = [f"`{i + 1}.` **{await bot.display_name(r['user_id'])}** — {len(json.loads(r['guesses']))} essai(s)"
                 for i, r in enumerate(ranked)]
        await interaction.response.send_message(
            embed=ui.embed(bot.cfg, f"🧩 Wordle du {day}", "\n".join(lines) or "Personne n'a encore trouvé !"))


async def setup(bot) -> None:
    await bot.add_cog(Wordle(bot))
