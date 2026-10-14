import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import DEFAULT_CONFIG, make_config
from bot.config import (ConfigError, coerce_value, deep_merge, delete_path, dumps, get_path, help_for, iter_paths,
                        load_config, load_defaults, plain, read_json, set_path, validate, write_json)


class ConfigTest(unittest.TestCase):
    def test_default_config_loads_and_is_valid(self):
        cfg = make_config()
        self.assertEqual(cfg.gacha.cost_single, 300)
        self.assertEqual(cfg.gacha.cost_ten, 2700)
        self.assertAlmostEqual(sum(cfg.gacha.hero_rates.values()), 100.0)
        self.assertTrue(cfg.boss.roster)
        self.assertEqual(len(cfg.daily.rewards), 7)
        self.assertEqual(validate(plain(cfg)), [])

    def test_unique_ids(self):
        cfg = make_config()
        for name, items in (("achievements", cfg.achievements), ("daily", cfg.missions.daily_pool),
                            ("weekly", cfg.missions.weekly_pool), ("shop", cfg.shop["items"]),
                            ("boss", cfg.boss.roster)):
            ids = [i["id"] for i in items]
            self.assertEqual(len(ids), len(set(ids)), name)

    def test_deep_merge(self):
        merged = deep_merge({"a": {"b": 1, "c": 2}, "d": 3}, {"a": {"b": 10}})
        self.assertEqual(merged, {"a": {"b": 10, "c": 2}, "d": 3})

    def test_user_file_created_and_merged(self):
        tmp = Path(tempfile.mkdtemp())
        user = tmp / "config.json"
        cfg = load_config(DEFAULT_CONFIG, user)
        self.assertTrue(user.exists())
        self.assertEqual(read_json(user), load_defaults(DEFAULT_CONFIG))
        self.assertEqual(cfg.gacha.cost_single, 300)
        # Un fichier partiel est complété par les valeurs par défaut (ex. après une mise à jour)
        user.write_text(json.dumps({"gacha": {"cost_single": 100}}), encoding="utf-8")
        cfg = load_config(DEFAULT_CONFIG, user)
        self.assertEqual(cfg.gacha.cost_single, 100)
        self.assertEqual(cfg.gacha.cost_ten, 2700)

    def test_invalid_json_is_reported(self):
        tmp = Path(tempfile.mkdtemp())
        user = tmp / "config.json"
        user.write_text('{"gacha": {', encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config(DEFAULT_CONFIG, user)
        self.assertIn("ligne 1", str(ctx.exception))

    def test_write_keeps_backup_and_is_readable(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "config.json"
        data = load_defaults(DEFAULT_CONFIG)
        write_json(path, data)
        data["gacha"]["cost_single"] = 1
        write_json(path, data)
        self.assertEqual(read_json(path)["gacha"]["cost_single"], 1)
        self.assertEqual(read_json(path.with_suffix(".json.bak"))["gacha"]["cost_single"], 300)
        text = path.read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 400)
        self.assertEqual(json.loads(dumps(data)), data)

    def test_paths(self):
        data = load_defaults(DEFAULT_CONFIG)
        self.assertEqual(get_path(data, "gacha.pity_hero"), 80)
        self.assertEqual(get_path(data, "shop/items/0/price/gold"), 10000)
        set_path(data, "gacha.pity_hero", 60)
        self.assertEqual(data["gacha"]["pity_hero"], 60)
        with self.assertRaises(ConfigError):
            set_path(data, "gacha.pity_heros", 60)          # faute de frappe
        with self.assertRaises(ConfigError):
            get_path(data, "shop.items.99")
        # Les dictionnaires « niveau -> valeur » acceptent de nouvelles clés
        set_path(data, "leveling.level_roles.10", 1234)
        self.assertEqual(data["leveling"]["level_roles"], {"10": 1234})
        self.assertEqual(delete_path(data, "leveling.level_roles.10"), 1234)
        # Ajout en fin de liste avec l'index = longueur
        n = len(data["shop"]["items"])
        set_path(data, f"shop.items.{n}", {"id": "x"})
        self.assertEqual(data["shop"]["items"][-1], {"id": "x"})
        self.assertEqual(delete_path(data, f"shop.items.{n}"), {"id": "x"})
        with self.assertRaises(ConfigError):
            delete_path(data, "gacha.cost_single")          # clé fixe : on réinitialise, on ne supprime pas
        paths = dict(iter_paths(data))
        self.assertIn("boss.roster.0.attacks.0.hp_pct", paths)

    def test_coerce_value(self):
        self.assertEqual(coerce_value("60", 80), 60)
        self.assertEqual(coerce_value("2,5", 1.5), 2.5)
        self.assertEqual(coerce_value("0.25", 0.5), 0.25)
        self.assertIs(coerce_value("oui", False), True)
        self.assertIs(coerce_value("false", True), False)
        self.assertEqual(coerce_value("Europe/Brussels", "Europe/Paris"), "Europe/Brussels")
        self.assertEqual(coerce_value('"#FFFFFF"', "#000000"), "#FFFFFF")
        self.assertEqual(coerce_value("[1, 2]", []), [1, 2])
        self.assertEqual(coerce_value('{"gems": 5}', {"gems": 1}), {"gems": 5})
        for raw, current in (("abc", 80), ("2.5", 80), ("peut-être", True), ("12", []), ("[1]", {})):
            with self.assertRaises(ConfigError, msg=raw):
                coerce_value(raw, current)

    def test_validation_catches_errors(self):
        data = load_defaults(DEFAULT_CONFIG)
        data["gacha"]["hero_rates"]["3"] = 10
        data["general"]["timezone"] = "Mars/Olympus"
        data["combat"]["team_size"] = 7
        data["boss"]["roster"][0]["attacks"][0]["type"] = "laser"
        data["shop"]["items"][1]["id"] = data["shop"]["items"][0]["id"]
        errors = "\n".join(validate(data))
        for needle in ("hero_rates", "timezone", "team_size", "boss.roster.0", "shop.items"):
            self.assertIn(needle, errors)

    def test_help(self):
        self.assertIn("gemmes", help_for("gacha.cost_single"))
        self.assertTrue(help_for("boss"))
        self.assertTrue(help_for("boss.roster.2.attacks"))   # remonte à la description de la liste
        self.assertIsNone(help_for("inexistant.cle"))
        defaults = load_defaults(DEFAULT_CONFIG)
        self.assertEqual([k for k in defaults if not help_for(k)], [], "chaque section doit être décrite")


if __name__ == "__main__":
    unittest.main()
