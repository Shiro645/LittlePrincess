"""Moteur de combat au tour par tour.

Utilisé par le colisée (simulation automatique), le dummy et le boss (les
joueurs choisissent une action par tour). Les règles s'inspirent de Guardian
Tales : avantage élémentaire, compétence d'arme avec recharge, compétences de
chaîne déclenchées par les états À terre / Projeté / Blessé, buffs d'équipe,
tanks qui attirent les coups et soutiens qui soignent.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

STATE_LABELS = {"downed": "À terre", "airborne": "Projeté", "injured": "Blessé"}
STATE_EMOJIS = {"downed": "💫", "airborne": "🌀", "injured": "🩸"}
CC_STATES = ("downed", "airborne")

ACTION_AUTO = "auto"
ACTION_ATTACK = "attack"
ACTION_SKILL = "skill"
ACTION_GUARD = "guard"


@dataclass
class FighterSpec:
    """Stats finales d'un combattant (après niveau, étoiles, arme et buffs)."""

    name: str
    element: Optional[str]
    role: str
    atk: int
    hp: int
    defense: int
    heal: int = 0
    crit_chance: float = 5.0          # %
    crit_mult: float = 1.5
    skill_name: str = "Compétence"
    skill_mult: float = 2.5
    skill_cd: int = 3                 # tours
    skill_state: Optional[str] = None
    chain_name: str = "Chaîne"
    chain_start: Optional[str] = None
    chain_end: Optional[str] = None
    chain_mult: float = 3.0
    dmg_pct: float = 0.0              # bonus de dégâts général
    skill_dmg_pct: float = 0.0
    role_dmg_pct: float = 0.0         # dégâts à distance ou de mêlée selon le rôle
    dmg_vs: dict[str, float] = field(default_factory=dict)
    owner_id: int = 0
    hero_key: str = ""
    image_url: Optional[str] = None


@dataclass
class Fighter:
    spec: FighterSpec
    side: int
    max_hp: int
    hp: int
    skill_cd_left: int = 0
    state: Optional[str] = None
    state_until: int = 0
    chained_round: int = -1
    guarding: bool = False
    is_boss: bool = False
    is_dummy: bool = False
    damage: dict[str, int] = field(default_factory=lambda: {"na": 0, "skill": 0, "chain": 0})
    healed: int = 0
    damage_taken: int = 0

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def total_damage(self) -> int:
        return sum(self.damage.values())

    @property
    def hp_ratio(self) -> float:
        return self.hp / self.max_hp if self.max_hp else 0.0


@dataclass
class BattleResult:
    winner: Optional[int]                  # 0, 1 ou None (égalité)
    rounds: int
    round_logs: list[list[str]]
    fighters: list[Fighter]
    timeout: bool = False

    def side(self, side: int) -> list[Fighter]:
        return [f for f in self.fighters if f.side == side]


def fmt(n: float) -> str:
    """12345 -> '12 345'"""
    return f"{int(round(n)):,}".replace(",", " ")


def make_fighter(spec: FighterSpec, side: int, hp_mult: float = 1.0) -> Fighter:
    hp = max(1, int(spec.hp * hp_mult))
    return Fighter(spec=spec, side=side, max_hp=hp, hp=hp)


class Battle:
    def __init__(self, cfg, fighters: list[Fighter], rng: Optional[random.Random] = None):
        self.cfg = cfg.combat
        self.fighters: list[Fighter] = []
        self.rng = rng or random.Random()
        self.round = 0
        self.round_logs: list[list[str]] = []
        self._log: list[str] = []
        self.chart = {k: set(v or []) for k, v in self.cfg.element_chart.items()}
        self.add(fighters)

    # -- utilitaires -------------------------------------------------------

    def add(self, fighters: list[Fighter]) -> None:
        """Ajoute des combattants et fixe la recharge initiale de leur compétence."""
        initial = self.cfg.get("initial_skill_cooldown", 0)
        for f in fighters:
            if str(initial) == "random":
                f.skill_cd_left = self.rng.randint(0, max(0, f.spec.skill_cd - 1))
            else:
                f.skill_cd_left = min(int(initial), f.spec.skill_cd)
        self.fighters.extend(fighters)

    def log(self, line: str) -> None:
        self._log.append(line)

    def alive(self, side: int) -> list[Fighter]:
        return [f for f in self.fighters if f.side == side and f.alive]

    def enemies(self, f: Fighter) -> list[Fighter]:
        return [e for e in self.fighters if e.side != f.side and e.alive]

    def teammates(self, f: Fighter) -> list[Fighter]:
        """Alliés de la même équipe (même joueur) : pour les chaînes et les soins."""
        return [a for a in self.fighters if a.side == f.side and a.alive and a.spec.owner_id == f.spec.owner_id]

    def element_multiplier(self, attacker: Optional[str], defender: Optional[str]) -> float:
        """Feu > Terre > Eau > Feu ; Lumière et Ténèbres se battent mutuellement."""
        if not attacker or not defender:
            return 1.0
        if defender in self.chart.get(attacker, ()):
            return float(self.cfg.element_advantage)
        if attacker in self.chart.get(defender, ()):
            return float(self.cfg.element_disadvantage)
        return 1.0

    def pick_target(self, attacker: Fighter) -> Optional[Fighter]:
        enemies = self.enemies(attacker)
        if not enemies:
            return None
        tanks = [e for e in enemies if e.spec.role == "tank"]
        if tanks and self.rng.random() < float(self.cfg.tank_taunt_chance):
            return self.rng.choice(tanks)
        return self.rng.choice(enemies)

    # -- dégâts --------------------------------------------------------------

    def compute_damage(self, attacker: Fighter, target: Fighter, mult: float, kind: str) -> tuple[int, bool]:
        spec = attacker.spec
        bonus = spec.dmg_pct + spec.role_dmg_pct
        if kind == "skill":
            bonus += spec.skill_dmg_pct
        if target.spec.element:
            bonus += spec.dmg_vs.get(target.spec.element, 0.0)
        dmg = spec.atk * mult * max(0.0, 1 + bonus / 100)
        dmg *= self.element_multiplier(spec.element, target.spec.element)
        defense = max(0, target.spec.defense)
        dmg *= 1 - defense / (defense + float(self.cfg.def_constant))
        crit = self.rng.random() < spec.crit_chance / 100
        if crit:
            dmg *= spec.crit_mult
        variance = float(self.cfg.damage_variance)
        dmg *= self.rng.uniform(1 - variance, 1 + variance)
        if target.guarding:
            dmg *= 1 - float(self.cfg.guard_reduction)
        return max(1, int(dmg)), crit

    def hit(self, attacker: Fighter, target: Fighter, kind: str, mult: float, state: Optional[str] = None,
            combo: int = 0, label: str = "") -> int:
        dmg, crit = self.compute_damage(attacker, target, mult, kind)
        dealt = min(dmg, target.hp) if not target.is_dummy else dmg
        target.hp = max(0, target.hp - dmg) if not target.is_dummy else target.hp
        target.damage_taken += dealt
        attacker.damage[kind] += dealt
        icon = {"na": "⚔️", "skill": "✨", "chain": "🔗"}[kind]
        what = f" *{label}*" if label else ""
        crit_txt = " **CRITIQUE**" if crit else ""
        combo_txt = f" (combo ×{combo})" if combo > 1 else ""
        line = f"{icon} **{attacker.name}**{what} → {target.name} : {fmt(dealt)}{crit_txt}{combo_txt}"
        if state and target.alive:
            target.state = state
            target.state_until = self.round + 1
            line += f" {STATE_EMOJIS[state]} {STATE_LABELS[state]}"
        self.log(line)
        if not target.alive:
            self.log(f"💀 **{target.name}** est K.O. !")
        elif state:
            self.trigger_chain(attacker, target, state, combo)
        return dealt

    def trigger_chain(self, source: Fighter, target: Fighter, state: str, combo: int) -> None:
        """Un allié dont la chaîne commence par `state` enchaîne immédiatement."""
        for ally in self.teammates(source):
            if ally is source:
                continue
            if ally.chained_round == self.round or ally.spec.chain_start != state:
                continue
            ally.chained_round = self.round
            mult = ally.spec.chain_mult * (1 + float(self.cfg.chain_combo_bonus) * combo)
            self.hit(ally, target, "chain", mult, state=ally.spec.chain_end, combo=combo + 1,
                     label=ally.spec.chain_name)
            return

    # -- actions ---------------------------------------------------------------

    def heal(self, healer: Fighter) -> bool:
        allies = [a for a in self.teammates(healer) if a.hp < a.max_hp]
        if not allies or healer.spec.heal <= 0:
            return False
        target = min(allies, key=lambda a: a.hp_ratio)
        amount = int(healer.spec.heal * float(self.cfg.heal_multiplier) * self.rng.uniform(0.95, 1.05))
        amount = min(amount, target.max_hp - target.hp)
        target.hp += amount
        healer.healed += amount
        who = "se soigne" if target is healer else f"soigne {target.name}"
        self.log(f"💚 **{healer.name}** {who} : +{fmt(amount)} PV")
        return True

    def needs_heal(self, healer: Fighter) -> bool:
        threshold = float(self.cfg.support_heal_threshold)
        return healer.spec.role == "support" and healer.spec.heal > 0 and any(
            a.hp_ratio < threshold for a in self.teammates(healer)
        )

    def act(self, f: Fighter, action: str = ACTION_AUTO) -> None:
        if not f.alive or f.is_dummy:
            return
        f.guarding = False
        if f.state in CC_STATES and not f.is_boss and f.state_until >= self.round:
            state = f.state
            f.state = None
            if self.rng.random() < float(self.cfg.cc_skip_chance):
                self.log(f"{STATE_EMOJIS[state]} **{f.name}** est {STATE_LABELS[state]} et perd son tour")
                return
        if action == ACTION_GUARD:
            f.guarding = True
            if not (f.spec.role == "support" and self.heal(f)):
                self.log(f"🛡️ **{f.name}** se met en garde")
            return
        if action == ACTION_AUTO and self.needs_heal(f):
            self.heal(f)
            return
        target = self.pick_target(f)
        if target is None:
            return
        use_skill = action in (ACTION_AUTO, ACTION_SKILL) and f.skill_cd_left <= 0
        if use_skill:
            f.skill_cd_left = f.spec.skill_cd
            self.hit(f, target, "skill", f.spec.skill_mult, state=f.spec.skill_state, label=f.spec.skill_name)
        else:
            mult = float(self.cfg.normal_attack_multiplier)
            if action == ACTION_ATTACK:
                # Attaque concentrée (boss) : plus forte, mais sans compétence
                mult *= float(self.cfg.get("focus_attack_multiplier", 1.0))
            self.hit(f, target, "na", mult)

    # -- tours ---------------------------------------------------------------

    def start_round(self) -> None:
        self.round += 1
        self._log = []

    def end_round(self) -> list[str]:
        for f in self.fighters:
            if f.skill_cd_left > 0:
                f.skill_cd_left -= 1
            # Un état dure jusqu'à la fin du tour suivant son application
            if f.state and f.state_until <= self.round:
                f.state = None
        logs = self._log
        self.round_logs.append(logs)
        self._log = []
        return logs

    def turn_order(self, first_side: int) -> list[Fighter]:
        a = [f for f in self.fighters if f.side == first_side]
        b = [f for f in self.fighters if f.side != first_side]
        order: list[Fighter] = []
        for i in range(max(len(a), len(b))):
            if i < len(a):
                order.append(a[i])
            if i < len(b):
                order.append(b[i])
        return order

    def finished(self) -> bool:
        return not self.alive(0) or not self.alive(1)

    def run_auto(self, max_rounds: int) -> BattleResult:
        first = self.rng.randint(0, 1)
        while self.round < max_rounds and not self.finished():
            self.start_round()
            for f in self.turn_order(first):
                if self.finished():
                    break
                self.act(f, ACTION_AUTO)
            self.end_round()
        return self.result(max_rounds)

    def result(self, max_rounds: int) -> BattleResult:
        alive0, alive1 = self.alive(0), self.alive(1)
        timeout = False
        if alive0 and not alive1:
            winner: Optional[int] = 0
        elif alive1 and not alive0:
            winner = 1
        else:
            timeout = self.round >= max_rounds
            r0 = sum(f.hp for f in self.fighters if f.side == 0) / max(1, sum(f.max_hp for f in self.fighters if f.side == 0))
            r1 = sum(f.hp for f in self.fighters if f.side == 1) / max(1, sum(f.max_hp for f in self.fighters if f.side == 1))
            winner = 0 if r0 > r1 else 1 if r1 > r0 else None
        return BattleResult(winner, self.round, self.round_logs, self.fighters, timeout)


# -- Boss ----------------------------------------------------------------------

@dataclass
class BossAttack:
    name: str
    kind: str          # single | aoe
    hp_pct: float      # dégâts en % des PV max de la cible
    weight: int = 1
    state: Optional[str] = None


def boss_turn(battle: Battle, boss: Fighter, attacks: list[BossAttack]) -> None:
    """Le boss attaque les héros du camp 0."""
    if not boss.alive or not attacks:
        return
    targets_pool = battle.alive(0)
    if not targets_pool:
        return
    attack = battle.rng.choices(attacks, weights=[max(1, a.weight) for a in attacks])[0]
    if attack.kind == "aoe":
        targets = targets_pool
        battle.log(f"👹 **{boss.name}** lance *{attack.name}* sur tous les héros !")
    else:
        target = battle.pick_target(boss)
        targets = [target] if target else []
        if target:
            battle.log(f"👹 **{boss.name}** lance *{attack.name}* sur {target.name} !")
    variance = float(battle.cfg.damage_variance)
    for target in targets:
        dmg = target.max_hp * attack.hp_pct / 100
        dmg *= battle.element_multiplier(boss.spec.element, target.spec.element)
        dmg *= battle.rng.uniform(1 - variance, 1 + variance)
        if target.guarding:
            dmg *= 1 - float(battle.cfg.guard_reduction)
        dealt = min(max(1, int(dmg)), target.hp)
        target.hp -= dealt
        target.damage_taken += dealt
        if attack.state and target.alive:
            target.state = attack.state
            target.state_until = battle.round + 1
        if not target.alive:
            battle.log(f"   💀 {target.name} est K.O. ({fmt(dealt)})")
        elif attack.kind != "aoe":
            battle.log(f"   → {fmt(dealt)} dégâts")
    if attack.kind == "aoe":
        battle.log(f"   → {len(targets)} héros touchés")


def skill_cd_turns(cfg, seconds: float) -> int:
    per_turn = float(cfg.combat.seconds_per_turn) or 4.0
    return max(int(cfg.combat.min_skill_cooldown_turns), int(math.ceil(seconds / per_turn)))
