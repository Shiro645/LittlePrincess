import tempfile
import unittest
from pathlib import Path

import yaml

from tests.helpers import DEFAULT_CONFIG, make_config
from bot.config import deep_merge, load_config


class ConfigTest(unittest.TestCase):
    def test_default_config_loads(self):
        cfg = make_config()
        self.assertEqual(cfg.gacha.cost_single, 300)
        self.assertEqual(cfg.gacha.cost_ten, 2700)
        self.assertAlmostEqual(sum(cfg.gacha.hero_rates.values()), 100.0)
        self.assertAlmostEqual(sum(cfg.gacha.weapon_rates.values()), 100.0)
        self.assertTrue(cfg.boss.roster)
        self.assertEqual(len(cfg.daily.rewards), 7)

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
        user = tmp / "config.yaml"
        cfg = load_config(DEFAULT_CONFIG, user)
        self.assertTrue(user.exists())
        self.assertEqual(cfg.gacha.cost_single, 300)
        # L'utilisateur ne garde qu'une partie : le reste vient des valeurs par défaut
        user.write_text(yaml.safe_dump({"gacha": {"cost_single": 100}}), encoding="utf-8")
        cfg = load_config(DEFAULT_CONFIG, user)
        self.assertEqual(cfg.gacha.cost_single, 100)
        self.assertEqual(cfg.gacha.cost_ten, 2700)


if __name__ == "__main__":
    unittest.main()
