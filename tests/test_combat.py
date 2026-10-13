import random
import unittest

from tests.helpers import make_config, make_data
from bot.game.combat import (ACTION_GUARD, Battle, BossAttack, Fighter, FighterSpec, boss_turn, make_fighter,
                             skill_cd_turns)
from bot.game.simulate import estimate_damage, run_dummy
from bot.game.teams import OwnedUnit, build_specs, party_effects, unit_stats


def spec(name, element="basic", role="warrior", atk=1000, hp=10000, defense=0, owner=1, **kw):
    return FighterSpec(name=name, element=element, role=role, atk=atk, hp=hp, defense=defense, owner_id=owner, **kw)


class CombatTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.data = make_data(self.cfg)

    def team(self, keys, level=100, stars=5, owner=1):
        units = []
        for key in keys:
            hero = self.data.hero(key)
            weapon = self.data.weapon(hero.exclusive_weapon) if hero.exclusive_weapon else None
            units.append(OwnedUnit(hero, level, stars, weapon, level, 5))
        return build_specs(self.cfg, units, owner)

    def test_element_chart(self):
        b = Battle(self.cfg, [])
        self.assertEqual(b.element_multiplier("fire", "earth"), 1.3)
        self.assertEqual(b.element_multiplier("earth", "fire"), 0.7)
        self.assertEqual(b.element_multiplier("light", "dark"), 1.3)
        self.assertEqual(b.element_multiplier("dark", "light"), 1.3)
        self.assertEqual(b.element_multiplier("basic", "fire"), 1.0)
        self.assertEqual(b.element_multiplier(None, "fire"), 1.0)

    def test_skill_cooldown_turns(self):
        self.assertEqual(skill_cd_turns(self.cfg, 11), 3)
        self.assertEqual(skill_cd_turns(self.cfg, 1), 2)

    def test_duel_ends_with_winner(self):
        a = self.team(["seed-marina", "seed-plitvice", "119", "seed-nari"], owner=1)
        b = self.team(["seed-oghma", "seed-lupina", "seed-bari", "seed-gabriel"], owner=2)
        wins = [0, 0]
        for seed in range(40):
            hp = self.cfg.combat.hp_multiplier.duel
            battle = Battle(self.cfg, [make_fighter(s, 0, hp) for s in a] + [make_fighter(s, 1, hp) for s in b],
                            random.Random(seed))
            result = battle.run_auto(self.cfg.combat.max_rounds_duel)
            self.assertIn(result.winner, (0, 1))
            self.assertEqual(len(result.round_logs), result.rounds)
            wins[result.winner] += 1
        self.assertTrue(all(wins), "équipes de même niveau : chacune doit gagner parfois")

    def test_stronger_team_wins_mostly(self):
        strong = self.team(["seed-marina", "seed-plitvice", "119", "seed-nari"], 100, 5, owner=1)
        weak = self.team(["seed-marina", "seed-plitvice", "119", "seed-nari"], 10, 1, owner=2)
        wins = 0
        for seed in range(20):
            battle = Battle(self.cfg, [make_fighter(s, 0) for s in strong] + [make_fighter(s, 1) for s in weak],
                            random.Random(seed))
            wins += battle.run_auto(30).winner == 0
        self.assertGreaterEqual(wins, 19)

    def test_chain_triggers_on_state(self):
        cfg = self.cfg
        cfg.combat["cc_skip_chance"] = 0
        attacker = make_fighter(spec("A", skill_state="downed", skill_mult=2.0, crit_chance=0), 0)
        chainer = make_fighter(spec("B", chain_start="downed", chain_end="airborne", chain_mult=3.0, crit_chance=0), 0)
        chainer2 = make_fighter(spec("C", chain_start="airborne", chain_mult=3.0, crit_chance=0), 0)
        target = make_fighter(spec("T", hp=10 ** 9, owner=2), 1)
        battle = Battle(cfg, [attacker, chainer, chainer2, target], random.Random(1))
        attacker.skill_cd_left = 0
        battle.start_round()
        battle.act(attacker)
        self.assertGreater(chainer.damage["chain"], 0)
        self.assertGreater(chainer2.damage["chain"], 0)
        self.assertEqual(attacker.damage["chain"], 0)
        # Une seule chaîne par héros et par tour
        before = chainer.damage["chain"]
        battle.hit(attacker, target, "na", 1.0, state="downed")
        self.assertEqual(chainer.damage["chain"], before)

    def test_support_heals(self):
        healer = make_fighter(spec("H", role="support", heal=1000), 0)
        ally = make_fighter(spec("A"), 0)
        ally.hp = 1000
        enemy = make_fighter(spec("E", owner=2), 1)
        battle = Battle(self.cfg, [healer, ally, enemy], random.Random(1))
        battle.start_round()
        battle.act(healer)
        self.assertGreater(ally.hp, 1000)
        self.assertGreater(healer.healed, 0)

    def test_guard_and_boss_attack(self):
        heroes = [make_fighter(spec(f"H{i}", hp=100000), 0) for i in range(2)]
        boss = Fighter(spec("Boss", element="dark", owner=0, hp=10 ** 6), side=1, max_hp=10 ** 6, hp=10 ** 6, is_boss=True)
        battle = Battle(self.cfg, heroes + [boss], random.Random(1))
        battle.start_round()
        heroes[0].guarding = True
        boss_turn(battle, boss, [BossAttack("AoE", "aoe", 10.0)])
        self.assertLess(heroes[0].damage_taken, heroes[1].damage_taken)
        self.assertAlmostEqual(heroes[1].damage_taken, 10000, delta=600)

    def test_guard_action(self):
        hero = make_fighter(spec("H"), 0)
        enemy = make_fighter(spec("E", owner=2), 1)
        battle = Battle(self.cfg, [hero, enemy], random.Random(1))
        battle.start_round()
        battle.act(hero, ACTION_GUARD)
        self.assertTrue(hero.guarding)
        self.assertEqual(enemy.damage_taken, 0)

    def test_party_buffs_and_leader(self):
        eunha = self.data.hero("119")
        mayreel = self.data.hero("seed-mayreel")
        units = [OwnedUnit(eunha, 100, 5), OwnedUnit(mayreel, 100, 5)]
        effects = party_effects(self.cfg, units)
        self.assertEqual(effects[0][1], self.cfg.combat.leader_party_buff_multiplier)
        stats = unit_stats(self.cfg, units[1], effects)
        self.assertEqual(stats.bonuses["ranged_dmg"], 50 * 1.5)
        self.assertEqual(stats.bonuses["atk"], 30)   # Earth type Atk +30% (son propre buff, s'applique à elle)

    def test_exclusive_weapon_bonus(self):
        eunha = self.data.hero("119")
        weapon = self.data.weapon("ex-119")
        bare = unit_stats(self.cfg, OwnedUnit(eunha, 100, 5))
        armed = unit_stats(self.cfg, OwnedUnit(eunha, 100, 5, weapon, 100, 5))
        self.assertGreater(armed.atk, bare.atk + weapon.atk)
        self.assertGreater(armed.hp, bare.hp)

    def test_dummy_and_estimate(self):
        specs = self.team(["seed-marina", "seed-plitvice", "119", "seed-nari"])
        run = run_dummy(self.cfg, specs, 10, rng=random.Random(1))
        self.assertEqual(len(run.per_round), 10)
        self.assertEqual(run.total, sum(run.per_round))
        self.assertGreater(run.total, 0)
        weak = estimate_damage(self.cfg, self.team(["seed-marina"], 1, 1), 15, "dark", 600)
        strong = estimate_damage(self.cfg, specs, 15, "dark", 600)
        self.assertGreater(strong, weak * 10)


if __name__ == "__main__":
    unittest.main()
