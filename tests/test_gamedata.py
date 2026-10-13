import unittest

from tests.helpers import make_config, make_data
from bot.gamedata import (Effect, normalize_element, normalize_name, normalize_weapon_type, parse_chain_states,
                          parse_dps_multiplier, parse_effects, parse_hero)


class GameDataTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.data = make_data(self.cfg)

    def test_seed_loaded(self):
        self.assertEqual(self.data.source, "seed")
        self.assertGreaterEqual(len(self.data.heroes), 20)
        for rarity in (1, 2, 3):
            self.assertTrue(self.data.heroes_by_rarity(rarity), rarity)
        for rarity in (1, 2, 3, 4, 5):
            self.assertTrue(self.data.weapons_by_rarity(rarity), rarity)

    def test_eunha_from_api_example(self):
        eunha = self.data.hero("119")
        self.assertEqual(eunha.name, "Eunha")
        self.assertEqual((eunha.role, eunha.element, eunha.rarity), ("ranged", "basic", 3))
        self.assertEqual((eunha.atk, eunha.hp, eunha.defense, eunha.heal), (2328, 94000, 451, 1250))
        self.assertEqual(eunha.weapon_type, "basket")
        self.assertEqual((eunha.chain_start, eunha.chain_end), ("downed", "airborne"))
        self.assertAlmostEqual(eunha.chain_mult, 5.0)
        self.assertAlmostEqual(eunha.skill_mult, 3.0)
        self.assertEqual(eunha.skill_state, "downed")
        self.assertEqual(eunha.party_effects, [Effect("ranged_dmg", 50.0)])
        self.assertEqual(eunha.image_url, "https://gtales.top/assets/heroes/eun.webp")
        weapon = self.data.weapon("ex-119")
        self.assertEqual(weapon.name, "Infinite Change")
        self.assertEqual(weapon.rarity, 5)
        self.assertEqual(weapon.atk, 1407)
        self.assertIn(Effect("crit_chance", 3.0), weapon.effects)

    def test_parse_effects(self):
        effects = parse_effects("Fire type Atk +30% / Damage against Dark type +20% / Crit Hit Multiplier +50% / "
                                "Normal Atk +10% / +5% Shield increase / Weapon Skill Regen Speed +18%")
        self.assertEqual(effects, [
            Effect("atk", 30.0, element="fire"),
            Effect("dmg_vs", 20.0, vs_element="dark"),
            Effect("crit_mult", 50.0),
            Effect("atk", 10.0),
        ])

    def test_normalizers(self):
        self.assertEqual(normalize_name("Future Princess"), "futureprincess")
        self.assertEqual(normalize_name("Élise"), "elise")
        self.assertEqual(normalize_element("Normal"), "basic")
        self.assertEqual(normalize_element("DARK"), "dark")
        self.assertEqual(parse_chain_states("downed-airborne"), ("downed", "airborne"))
        self.assertEqual(parse_chain_states("Injured"), ("injured", None))
        self.assertEqual(parse_chain_states(""), (None, None))
        self.assertEqual(normalize_weapon_type("Two-Handed Sword"), "sword_2h")
        self.assertEqual(normalize_weapon_type("One-Handed Sword"), "sword_1h")
        self.assertEqual(normalize_weapon_type("basket"), "basket")
        self.assertEqual(normalize_weapon_type("Gauntlet"), "gauntlet")
        self.assertIsNone(normalize_weapon_type(""))
        self.assertAlmostEqual(parse_dps_multiplier("inflict 500% of DPS as melee damage"), 5.0)

    def test_invalid_hero_is_skipped(self):
        self.assertIsNone(parse_hero({"key": "x", "name": "Vide"}, self.cfg, "https://gtales.top"))
        self.assertIsNone(parse_hero({"name": "Sans clé", "variants": []}, self.cfg, "https://gtales.top"))

    def test_missing_weapon_type_uses_role_default(self):
        raw = {"key": "9", "name": "Test", "role": "support", "element": "fire", "rarity": "2",
               "variants": [{"version": "Base", "stats": {"atk": "100", "hp": "1000"}}]}
        hero = parse_hero(raw, self.cfg, "https://gtales.top")
        self.assertEqual(hero.weapon_type, "staff")
        self.assertIsNone(hero.chain_start)

    def test_find_and_search(self):
        self.assertEqual(self.data.find_hero("eunha").key, "119")
        self.assertEqual(self.data.find_hero("FUTURE princess").name, "Future Princess")
        self.assertIsNone(self.data.find_hero("zzz"))
        names = [h.name for h in self.data.search_heroes("ma")]
        self.assertIn("Marina", names)

    def test_resolve_seed_key(self):
        self.assertEqual(self.data.resolve_seed_key("seed-plitvice"), "seed-plitvice")
        self.assertIsNone(self.data.resolve_seed_key("seed-inconnu"))


if __name__ == "__main__":
    unittest.main()
