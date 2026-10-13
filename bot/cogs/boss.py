"""Boss du samedi : raid en direct au tour par tour, puis rattrapage en solo."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import timeutil
from ..game.combat import (ACTION_ATTACK, ACTION_AUTO, ACTION_GUARD, ACTION_SKILL, Battle, BossAttack, Fighter,
                           FighterSpec, boss_turn, make_fighter)
from ..game.simulate import estimate_damage
from ..game.teams import build_specs
from ..services.collection import GameError
from ..services.leveling import add_xp_sync
from ..services.players import credit_sync, ensure_sync, format_rewards, merge_rewards
from ..ui import format as ui
from ..ui.battle import LogView
from ..ui.format import num
from ._common import bot_of

log = logging.getLogger(__name__)

LIVE_GRACE = timedelta(minutes=30)   # si le bot démarre en retard, le raid live peut encore commencer
ACTION_LABELS = {ACTION_ATTACK: "⚔️ Attaque", ACTION_SKILL: "✨ Compétence", ACTION_GUARD: "🛡️ Garde",
                 ACTION_AUTO: "🤖 Auto"}
ACTION_HELP = ("⚔️ **Attaque** : attaque normale renforcée, sans compétence · ✨ **Compétence** : les compétences "
               "prêtes sont lancées (et déclenchent les chaînes), les autres héros attaquent · 🛡️ **Garde** : "
               "dégâts subis réduits, les soutiens soignent")


def boss_def(cfg, boss_id: str) -> dict:
    for boss in cfg.boss.roster:
        if boss["id"] == boss_id:
            return boss
    return cfg.boss.roster[0]


def boss_attacks(boss: dict) -> list[BossAttack]:
    return [BossAttack(a["name"], a.get("type", "single"), float(a.get("hp_pct", 20)), int(a.get("weight", 1)),
                       a.get("state")) for a in boss.get("attacks", [])]


def boss_fighter(boss: dict, hp: int, hp_max: int) -> Fighter:
    spec = FighterSpec(name=boss["name"], element=boss.get("element"), role="tank", atk=0,
                       hp=max(1, hp_max), defense=int(boss.get("defense", 500)))
    return Fighter(spec=spec, side=1, max_hp=max(1, hp_max), hp=hp, is_boss=True)


def boss_embed(cfg, boss: dict, hp: int, hp_max: int, title: str = "", description: str = "") -> discord.Embed:
    e = ui.embed(cfg, title or f"👹 {boss['name']}", description or boss.get("description", ""), colour=0xC62828)
    element = boss.get("element")
    e.add_field(name="Élément", value=f"{ui.ELEMENT_EMOJI.get(element, '')} {ui.ELEMENT_FR.get(element, '—')}")
    e.add_field(name="DEF", value=num(boss.get("defense", 0)))
    if hp_max:
        e.add_field(name="PV", value=f"{ui.progress_bar(hp, hp_max, 16)}\n{num(hp)} / {num(hp_max)}", inline=False)
    if boss.get("image"):
        e.set_thumbnail(url=boss["image"])
    return e


class Raid:
    """État en mémoire d'un raid en direct."""

    def __init__(self, cog: "Boss", event_id: int, boss: dict, hp: int, hp_max: int):
        self.cog = cog
        self.event_id = event_id
        self.boss = boss
        self.attacks = boss_attacks(boss)
        self.fighter = boss_fighter(boss, hp, hp_max)
        self.fighter.max_hp = hp_max
        self.battle = Battle(cog.bot.cfg, [self.fighter], random.Random())
        self.participants: dict[int, list[Fighter]] = {}
        self.names: dict[int, str] = {}
        self.actions: dict[int, str] = {}
        self.damage: dict[int, int] = {}
        self.turn = 0
        self.all_chose = asyncio.Event()
        self.thread: Optional[discord.abc.Messageable] = None
        self.lock = asyncio.Lock()

    def alive_participants(self) -> list[int]:
        return [uid for uid, team in self.participants.items() if any(f.alive for f in team)]

    def team_status(self, user_id: int) -> str:
        parts = []
        for f in self.participants.get(user_id, []):
            if not f.alive:
                parts.append(f"💀 {f.name}")
                continue
            skill = "✨ prête" if f.skill_cd_left <= 0 else f"⏳ {f.skill_cd_left} t."
            parts.append(f"{f.name} ❤️ {int(f.hp_ratio * 100)} % {skill}")
        return " · ".join(parts)

    async def join(self, member: discord.Member) -> str:
        bot = self.cog.bot
        cfg = bot.cfg
        async with self.lock:
            if member.id in self.participants:
                return "Tu participes déjà au raid !"
            units = await bot.collection.team_units(member.id)
            if not units:
                raise GameError("Tu n'as pas d'équipe : `/team definir`.")
            specs = build_specs(cfg, units, member.id)
            added = await self.cog.hp_for(specs, self.boss)
            fighters = [make_fighter(s, 0, float(cfg.combat.hp_multiplier.boss)) for s in specs]
            self.battle.add(fighters)
            self.participants[member.id] = fighters
            self.names[member.id] = member.display_name
            self.damage.setdefault(member.id, 0)
            self.fighter.max_hp += added
            self.fighter.hp += added
            self.fighter.spec.hp = self.fighter.max_hp

            def _tx(conn):
                ensure_sync(conn, cfg, member.id)
                conn.execute("UPDATE boss_events SET hp_max = hp_max + ?, hp = hp + ? WHERE id = ?",
                             (added, added, self.event_id))
                conn.execute("INSERT OR IGNORE INTO boss_damage (event_id, user_id, damage, mode) VALUES (?, ?, 0, 'live')",
                             (self.event_id, member.id))
                unlocked = bot.progress.record_sync(conn, member.id, "boss_participation")
                xp = add_xp_sync(conn, cfg, member.id, int(cfg.leveling.minigame_xp.boss_participation))
                return unlocked, xp

            unlocked, xp = await bot.db.transaction(_tx)
        xp.achievements += unlocked
        await bot.handle_xp_result(member, xp, self.thread)
        return f"✅ Inscrit ! Ton équipe rejoint le combat (+{num(added)} PV pour le boss)."

    def resolve_turn(self) -> tuple[list[str], list[str], dict[int, int]]:
        """Résout un tour : actions des joueurs puis riposte du boss."""
        cfg = self.cog.bot.cfg
        battle = self.battle
        battle.start_round()
        summary: list[str] = []
        dealt: dict[int, int] = {}
        for uid in list(self.participants):
            team = self.participants[uid]
            if not any(f.alive for f in team) or not self.fighter.alive:
                continue
            action = self.actions.get(uid, str(cfg.boss.afk_action))
            before = sum(f.total_damage for f in team)
            start = len(battle._log)
            for f in team:
                if f.alive and self.fighter.alive:
                    battle.act(f, action)
            gained = sum(f.total_damage for f in team) - before
            chains = sum(1 for line in battle._log[start:] if line.startswith("🔗"))
            dealt[uid] = gained
            self.damage[uid] = self.damage.get(uid, 0) + gained
            afk = "" if uid in self.actions else " *(auto)*"
            line = f"{ACTION_LABELS.get(action, action)}{afk} **{self.names[uid]}** — {num(gained)} dégâts"
            if chains:
                line += f" · 🔗×{chains}"
            summary.append(line)
        start = len(battle._log)
        if self.fighter.alive:
            boss_turn(battle, self.fighter, self.attacks)
        boss_lines = battle._log[start:]
        battle.end_round()
        self.actions.clear()
        self.all_chose.clear()
        return summary, boss_lines, dealt


class TurnView(discord.ui.View):
    def __init__(self, raid: Raid, joining_only: bool = False):
        super().__init__(timeout=None)
        self.raid = raid
        if joining_only:
            for item in (self.attack, self.skill, self.guard):
                self.remove_item(item)

    async def _choose(self, interaction: discord.Interaction, action: str) -> None:
        raid = self.raid
        if interaction.user.id not in raid.participants:
            await interaction.response.send_message("Rejoins d'abord le raid avec ➕ !", ephemeral=True)
            return
        if not any(f.alive for f in raid.participants[interaction.user.id]):
            await interaction.response.send_message("Toute ton équipe est K.O. 💀", ephemeral=True)
            return
        raid.actions[interaction.user.id] = action
        await interaction.response.send_message(
            f"Action choisie : {ACTION_LABELS[action]}\n{raid.team_status(interaction.user.id)}", ephemeral=True)
        if all(uid in raid.actions for uid in raid.alive_participants()):
            raid.all_chose.set()

    @discord.ui.button(label="Attaque", emoji="⚔️", style=discord.ButtonStyle.primary)
    async def attack(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._choose(interaction, ACTION_ATTACK)

    @discord.ui.button(label="Compétence", emoji="✨", style=discord.ButtonStyle.success)
    async def skill(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._choose(interaction, ACTION_SKILL)

    @discord.ui.button(label="Garde", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def guard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._choose(interaction, ACTION_GUARD)

    @discord.ui.button(label="Rejoindre", emoji="➕", style=discord.ButtonStyle.secondary)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            return
        try:
            message = await self.raid.join(interaction.user)
        except GameError as exc:
            message = f"❌ {exc}"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class Boss(commands.Cog):
    boss = app_commands.Group(name="boss", description="Le boss du samedi")

    def __init__(self, bot):
        self.bot = bot
        self.raid: Optional[Raid] = None
        self.raid_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()
        if self.raid_task:
            self.raid_task.cancel()

    @property
    def cfg(self):
        return self.bot.cfg

    # -- PV adaptatifs ------------------------------------------------------

    async def hp_for(self, specs, boss: dict) -> int:
        cfg = self.cfg.boss
        if str(cfg.hp_mode) == "fixed":
            return int(cfg.hp_per_participant)
        loop = asyncio.get_running_loop()
        estimate = await loop.run_in_executor(
            None, estimate_damage, self.cfg, specs, int(cfg.max_turns), boss.get("element"), int(boss.get("defense", 500)))
        return max(1, int(estimate * float(cfg.difficulty)))

    # -- planification --------------------------------------------------------

    def boss_for(self, start: datetime) -> dict:
        roster = self.cfg.boss.roster
        return roster[(start.toordinal() // 7) % len(roster)]

    def solo_end_after(self, start: datetime) -> datetime:
        cfg = self.cfg.boss
        end = timeutil.next_weekly(self.cfg, int(cfg.solo_end_weekday), int(cfg.solo_end_hour),
                                   int(cfg.solo_end_minute), start)
        return end if end > start else end + timedelta(days=7)

    async def current_event(self) -> Optional[dict]:
        row = await self.bot.db.fetchone("SELECT * FROM boss_events WHERE status != 'done' ORDER BY id DESC LIMIT 1")
        return dict(row) if row else None

    @tasks.loop(seconds=30)
    async def tick(self) -> None:
        try:
            await self._tick()
        except Exception:
            log.exception("Erreur dans la boucle du boss")

    @tick.before_loop
    async def before_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def _tick(self) -> None:
        cfg = self.cfg.boss
        now = timeutil.now(self.cfg)
        event = await self.current_event()
        if event:
            solo_end = datetime.fromisoformat(event["solo_end"])
            if event["status"] == "live" and self.raid is None:
                await self.bot.db.execute("UPDATE boss_events SET status = 'solo' WHERE id = ?", (event["id"],))
                await self.bot.safe_send(self.bot.channel("boss"),
                                         "⚠️ Le raid a été interrompu (redémarrage du bot). Le rattrapage `/boss solo` "
                                         f"est ouvert jusqu'à {timeutil.discord_ts(solo_end, 'F')}.")
            elif event["status"] == "solo" and now >= solo_end:
                await self.finalize(event["id"])
            return
        if not cfg.enabled:
            return
        start = timeutil.last_weekly(self.cfg, int(cfg.weekday), int(cfg.hour), int(cfg.minute), now)
        week = start.date().isoformat()
        if await self.bot.db.fetchone("SELECT 1 FROM boss_events WHERE week = ?", (week,)):
            return
        solo_end = self.solo_end_after(start)
        if now >= solo_end:
            return
        live = now - start <= LIVE_GRACE
        await self.open_event(week, self.boss_for(start), solo_end, live)

    async def open_event(self, week: str, boss: dict, solo_end: datetime, live: bool) -> int:
        now = timeutil.now(self.cfg)
        channel = self.bot.channel("boss")
        event_id = await self.bot.db.insert(
            "INSERT INTO boss_events (week, boss_id, hp_max, hp, status, started_at, solo_end, channel_id) "
            "VALUES (?, ?, 0, 0, ?, ?, ?, ?)",
            (week, boss["id"], "live" if live else "solo", now.isoformat(), solo_end.isoformat(),
             channel.id if channel else None))
        if live:
            self.raid = Raid(self, event_id, boss, 0, 0)
            self.raid_task = asyncio.create_task(self.run_raid(self.raid, solo_end))
        else:
            await self.bot.safe_send(channel, embed=boss_embed(
                self.cfg, boss, 0, 0, f"👹 {boss['name']} est apparu !",
                f"Le raid en direct est passé, mais tu peux l'affronter en solo avec `/boss solo` jusqu'à "
                f"{timeutil.discord_ts(solo_end, 'F')}."))
        log.info("Boss %s ouvert (semaine %s, live=%s)", boss["id"], week, live)
        return event_id

    # -- raid en direct -------------------------------------------------------

    async def run_raid(self, raid: Raid, solo_end: datetime) -> None:
        try:
            await self._run_raid(raid, solo_end)
        except Exception:
            log.exception("Erreur pendant le raid")
            await self.bot.db.execute("UPDATE boss_events SET status = 'solo' WHERE id = ? AND status = 'live'",
                                      (raid.event_id,))
        finally:
            self.raid = None

    async def _run_raid(self, raid: Raid, solo_end: datetime) -> None:
        cfg = self.cfg.boss
        bot = self.bot
        channel = bot.channel("boss")
        if channel is None:
            log.warning("channels.boss non configuré : le raid live est annulé, rattrapage solo ouvert")
            await bot.db.execute("UPDATE boss_events SET status = 'solo' WHERE id = ?", (raid.event_id,))
            return
        join_end = timeutil.now(self.cfg) + timedelta(seconds=int(cfg.join_seconds))
        ping = f"<@&{int(self.cfg.roles.boss_ping)}> " if int(self.cfg.roles.boss_ping or 0) else ""
        intro = boss_embed(self.cfg, raid.boss, 0, 0, f"👹 {raid.boss['name']} attaque Kanterbury !",
                           f"{raid.boss.get('description', '')}\n\nCliquez sur **➕ Rejoindre** avec votre équipe ! "
                           f"Premier tour {timeutil.discord_ts(join_end)} · {cfg.max_turns} tours max · "
                           f"{cfg.turn_seconds} s par tour.")
        intro_view = TurnView(raid, joining_only=True)
        message = await channel.send(ping + "Le boss du samedi est là !", embed=intro, view=intro_view,
                                     allowed_mentions=discord.AllowedMentions(roles=True))
        raid.thread = channel
        if cfg.use_thread and isinstance(channel, discord.TextChannel):
            try:
                raid.thread = await message.create_thread(name=f"Raid — {raid.boss['name']}"[:100],
                                                          auto_archive_duration=1440)
            except discord.HTTPException as exc:
                log.warning("Impossible de créer le fil du raid : %s", exc)

        await asyncio.sleep(int(cfg.join_seconds))
        if not raid.participants:
            intro_view.stop()
            await message.edit(view=None)
            await bot.db.execute("UPDATE boss_events SET status = 'solo' WHERE id = ?", (raid.event_id,))
            await bot.safe_send(channel, f"😴 Personne n'a rejoint le raid… Le rattrapage `/boss solo` reste ouvert "
                                         f"jusqu'à {timeutil.discord_ts(solo_end, 'F')}.")
            return

        target = raid.thread or channel
        for turn in range(1, int(cfg.max_turns) + 1):
            raid.turn = turn
            if not raid.fighter.alive or not raid.alive_participants():
                break
            embed = boss_embed(self.cfg, raid.boss, raid.fighter.hp, raid.fighter.max_hp,
                               f"Tour {turn}/{cfg.max_turns} — {raid.boss['name']}",
                               f"Choisissez votre action ! Fin du tour {timeutil.discord_ts(timeutil.now(self.cfg) + timedelta(seconds=int(cfg.turn_seconds)))}\n"
                               f"Participants en vie : {len(raid.alive_participants())}\n\n{ACTION_HELP}")
            view = TurnView(raid)
            turn_msg = await target.send(embed=embed, view=view)
            try:
                await asyncio.wait_for(raid.all_chose.wait(), timeout=int(cfg.turn_seconds))
            except asyncio.TimeoutError:
                pass
            summary, boss_lines, dealt = raid.resolve_turn()
            await self.save_live_damage(raid, dealt)
            result = ui.embed(self.cfg, f"Tour {turn} — résultat",
                              "\n".join(summary + [""] + boss_lines)[:4000] or "…", colour=0xC62828)
            result.add_field(name="PV du boss",
                             value=f"{ui.progress_bar(raid.fighter.hp, raid.fighter.max_hp, 16)} "
                                   f"{num(raid.fighter.hp)} / {num(raid.fighter.max_hp)}")
            view.stop()
            try:
                await turn_msg.edit(view=None)
            except discord.HTTPException:
                pass
            await target.send(embed=result)

        killed = not raid.fighter.alive
        await bot.db.execute("UPDATE boss_events SET status = 'solo', killed = ? WHERE id = ?",
                             (int(killed), raid.event_id))
        ranking = sorted(raid.damage.items(), key=lambda kv: -kv[1])
        lines = [f"`{i + 1}.` **{raid.names[uid]}** — {num(dmg)}" for i, (uid, dmg) in enumerate(ranking)]
        title = f"🏆 {raid.boss['name']} est vaincu !" if killed else f"⏱️ Fin du raid — {raid.boss['name']} a survécu"
        e = ui.embed(self.cfg, title, "\n".join(lines)[:4000], colour=0x2E7D32 if killed else 0xC62828)
        e.add_field(name="Rattrapage", value=f"Les absents peuvent encore combattre avec `/boss solo` jusqu'à "
                                             f"{timeutil.discord_ts(solo_end, 'F')}. Récompenses distribuées ensuite.")
        intro_view.stop()
        await message.edit(view=None)
        await bot.safe_send(channel, embed=e)
        if target is not channel:
            await bot.safe_send(target, embed=e)

    async def save_live_damage(self, raid: Raid, dealt: dict[int, int]) -> None:
        def _tx(conn):
            for uid, dmg in dealt.items():
                conn.execute("UPDATE boss_damage SET damage = damage + ? WHERE event_id = ? AND user_id = ?",
                             (dmg, raid.event_id, uid))
            conn.execute("UPDATE boss_events SET hp = ?, killed = ? WHERE id = ?",
                         (raid.fighter.hp, int(not raid.fighter.alive), raid.event_id))
        await self.bot.db.transaction(_tx)

    # -- fin et récompenses -------------------------------------------------

    async def finalize(self, event_id: int) -> None:
        bot = self.bot
        cfg = self.cfg
        rewards_cfg = cfg.boss.rewards
        event = await bot.db.fetchone("SELECT * FROM boss_events WHERE id = ?", (event_id,))
        if event is None or event["rewarded"]:
            return
        rows = await bot.db.fetchall("SELECT user_id, damage FROM boss_damage WHERE event_id = ? ORDER BY damage DESC",
                                     (event_id,))
        killed = bool(event["killed"])
        ranks = list(rewards_cfg.rank_bonus or [])

        def _tx(conn):
            cur = conn.execute("UPDATE boss_events SET status = 'done', rewarded = 1 WHERE id = ? AND rewarded = 0",
                               (event_id,))
            if cur.rowcount != 1:
                return {}, {}
            given, unlocked = {}, {}
            for i, row in enumerate(rows):
                reward = merge_rewards(rewards_cfg.participation, ranks[i] if i < len(ranks) else None,
                                       rewards_cfg.kill_bonus if killed else None)
                credit_sync(conn, row["user_id"], reward)
                given[row["user_id"]] = reward
                if killed:
                    unlocked[row["user_id"]] = bot.progress.record_sync(conn, row["user_id"], "boss_kill")
            return given, unlocked

        given, unlocked = await bot.db.transaction(_tx)
        if not given and rows:
            return
        boss = boss_def(cfg, event["boss_id"])
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'`{i + 1}.`'} **{await bot.display_name(r['user_id'])}** — "
                 f"{num(r['damage'])} · {format_rewards(given.get(r['user_id'], {}))}" for i, r in enumerate(rows)]
        title = f"🎁 Récompenses — {boss['name']} " + ("(vaincu ✅)" if killed else "(survivant ❌)")
        await bot.safe_send(bot.channel("boss"), embed=ui.embed(cfg, title, "\n".join(lines)[:4000] or "Aucun participant."))
        for uid, achs in unlocked.items():
            user = bot.get_user(uid)
            if user and achs:
                await bot.notify_achievements(user, achs, bot.channel("boss"))

    # -- commandes ----------------------------------------------------------

    @boss.command(name="info", description="Le boss en cours ou le prochain")
    async def info(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg
        event = await self.current_event()
        if event:
            boss = boss_def(cfg, event["boss_id"])
            participants = await bot.db.fetchval("SELECT COUNT(*) FROM boss_damage WHERE event_id = ?", (event["id"],), 0)
            status = {"live": "⚔️ Raid en direct en cours !", "solo": "🧍 Rattrapage solo ouvert (`/boss solo`)"}
            desc = (f"{status.get(event['status'], '')}\nFin : {timeutil.discord_ts(datetime.fromisoformat(event['solo_end']), 'F')}"
                    f"\nParticipants : {participants}" + ("\n✅ **Vaincu !**" if event["killed"] else ""))
            await interaction.response.send_message(embed=boss_embed(cfg, boss, event["hp"], event["hp_max"],
                                                                     description=desc))
            return
        bc = cfg.boss
        start = timeutil.next_weekly(cfg, int(bc.weekday), int(bc.hour), int(bc.minute))
        boss = self.boss_for(start)
        e = boss_embed(cfg, boss, 0, 0, f"Prochain boss : {boss['name']}",
                       f"{boss.get('description', '')}\n\nApparition {timeutil.discord_ts(start, 'F')} "
                       f"({timeutil.discord_ts(start)})")
        await interaction.response.send_message(embed=e)

    @boss.command(name="solo", description="Rattrapage : affronte le boss seul si tu as manqué le raid")
    async def solo(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        cfg = bot.cfg
        user_id = interaction.user.id
        event = await self.current_event()
        if event is None:
            raise GameError("Aucun boss en ce moment. `/boss info` pour le prochain.")
        if event["status"] == "live":
            raise GameError("Le raid en direct est en cours : rejoins-le dans le salon du boss !")
        if await bot.db.fetchone("SELECT 1 FROM boss_damage WHERE event_id = ? AND user_id = ?", (event["id"], user_id)):
            raise GameError("Tu as déjà affronté ce boss cette semaine.")
        units = await bot.collection.team_units(user_id)
        if not units:
            raise GameError("Tu n'as pas d'équipe : `/team definir`.")
        await interaction.response.defer(thinking=True)
        boss = boss_def(cfg, event["boss_id"])
        specs = build_specs(cfg, units, user_id)
        # Boss déjà vaincu : le combat compte pour la participation, sans ajouter de PV
        added = 0 if event["killed"] else await self.hp_for(specs, boss)
        hp_max, hp = event["hp_max"] + added, event["hp"] + added
        fighter = boss_fighter(boss, hp if hp > 0 else hp_max or 1, hp_max or 1)
        dead_already = event["killed"] or hp <= 0
        team = [make_fighter(s, 0, float(cfg.combat.hp_multiplier.boss)) for s in specs]
        battle = Battle(cfg, team + [fighter], random.Random())
        attacks = boss_attacks(boss)
        for _ in range(int(cfg.boss.solo_turns)):
            if not fighter.alive or not any(f.alive for f in team):
                break
            battle.start_round()
            for f in team:
                if fighter.alive:
                    battle.act(f)
            boss_turn(battle, fighter, attacks)
            battle.end_round()
        damage = sum(f.total_damage for f in team)
        result = battle.result(int(cfg.boss.solo_turns))
        xp_amount = int(cfg.leveling.minigame_xp.boss_participation)

        def _tx(conn):
            ensure_sync(conn, cfg, user_id)
            cur = conn.execute("INSERT OR IGNORE INTO boss_damage (event_id, user_id, damage, mode) VALUES (?, ?, ?, 'solo')",
                               (event["id"], user_id, damage))
            if cur.rowcount != 1:
                raise GameError("Tu as déjà affronté ce boss cette semaine.")
            conn.execute("UPDATE boss_events SET hp_max = hp_max + ?, hp = MAX(0, hp + ? - ?) WHERE id = ?",
                         (added, added, damage if not dead_already else 0, event["id"]))
            row = conn.execute("SELECT hp, killed FROM boss_events WHERE id = ?", (event["id"],)).fetchone()
            killed_now = row["hp"] <= 0 and not row["killed"] and not dead_already
            if killed_now:
                conn.execute("UPDATE boss_events SET killed = 1 WHERE id = ?", (event["id"],))
            unlocked = bot.progress.record_sync(conn, user_id, "boss_participation")
            xp = add_xp_sync(conn, cfg, user_id, xp_amount)
            return killed_now, row["hp"], unlocked, xp

        killed_now, hp_left, unlocked, xp = await bot.db.transaction(_tx)
        title = f"👹 {boss['name']} — {num(damage)} dégâts"
        if killed_now:
            title = f"🏆 Coup de grâce ! {boss['name']} est vaincu !"
        fallen = " Ton équipe est tombée…" if not any(f.alive for f in team) else ""
        e = boss_embed(cfg, boss, hp_left, hp_max, title, f"Combat solo en {result.rounds} tours.{fallen}")
        e.add_field(name="Ton équipe", value="\n".join(
            f"{'💀' if not f.alive else '❤️'} **{f.name}** — {num(f.total_damage)}" for f in team), inline=False)
        await interaction.followup.send(embed=e, view=LogView(cfg, result, f"Solo contre {boss['name']}"))
        xp.achievements += unlocked
        await bot.handle_xp_result(interaction.user, xp, interaction.channel)

    @boss.command(name="classement", description="Dégâts sur le boss de la semaine")
    async def classement(self, interaction: discord.Interaction) -> None:
        bot = bot_of(interaction)
        event = await bot.db.fetchone("SELECT * FROM boss_events ORDER BY id DESC LIMIT 1")
        if event is None:
            raise GameError("Aucun boss n'a encore eu lieu.")
        boss = boss_def(bot.cfg, event["boss_id"])
        rows = await bot.db.fetchall("SELECT user_id, damage, mode FROM boss_damage WHERE event_id = ? ORDER BY damage DESC",
                                     (event["id"],))
        lines = [f"`{i + 1}.` **{await bot.display_name(r['user_id'])}** — {num(r['damage'])}"
                 f"{' (solo)' if r['mode'] == 'solo' else ''}" for i, r in enumerate(rows)]
        await interaction.response.send_message(embed=ui.embed(bot.cfg, f"👹 Classement — {boss['name']}",
                                                               "\n".join(lines) or "Aucun participant."))

    # -- utilisé par /admin ------------------------------------------------------

    async def admin_start(self) -> str:
        if await self.current_event():
            raise GameError("Un boss est déjà en cours. Termine-le d'abord (`/admin boss-terminer`).")
        now = timeutil.now(self.cfg)
        solo_end = self.solo_end_after(now)
        if solo_end - now < timedelta(hours=1):
            solo_end = now + timedelta(hours=24)
        boss = self.boss_for(now)
        await self.open_event(f"manual-{now:%Y%m%d%H%M%S}", boss, solo_end, live=True)
        return f"Boss **{boss['name']}** lancé ! Rattrapage jusqu'à {timeutil.discord_ts(solo_end, 'F')}."

    async def admin_finish(self) -> str:
        event = await self.current_event()
        if event is None:
            raise GameError("Aucun boss en cours.")
        if event["status"] == "live" and self.raid_task:
            self.raid_task.cancel()
            self.raid = None
        await self.finalize(event["id"])
        return "Boss terminé et récompenses distribuées."


async def setup(bot) -> None:
    await bot.add_cog(Boss(bot))
