import random
import unittest
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.helpers import make_config, make_data
from bot.game import gacha, stats


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()

    def test_scaling(self):
        self.assertEqual(stats.scale(self.cfg, 1000, 100, 5), 1000)
        self.assertEqual(stats.scale(self.cfg, 1000, 1, 5), 200)
        self.assertEqual(stats.scale(self.cfg, 1000, 1, 1), 120)
        self.assertLess(stats.scale(self.cfg, 1000, 50, 3), stats.scale(self.cfg, 1000, 51, 3))

    def test_max_level(self):
        self.assertEqual(stats.max_level(self.cfg, 1), 20)
        self.assertEqual(stats.max_level(self.cfg, 5), 100)
        self.assertEqual(stats.max_level(self.cfg, 9), 100)

    def test_affordable_levels(self):
        cost = stats.cost_to_level(self.cfg, 1, 11)
        levels, spent = stats.affordable_levels(self.cfg, 1, 20, cost, 50)
        self.assertEqual((levels, spent), (10, cost))
        levels, _ = stats.affordable_levels(self.cfg, 1, 5, 10 ** 9, 50)
        self.assertEqual(levels, 4)
        self.assertEqual(stats.affordable_levels(self.cfg, 1, 20, 0, 5), (0, 0))

    def test_xp(self):
        need = stats.xp_to_next(self.cfg, 1)
        level, xp, reached = stats.apply_xp(self.cfg, 1, 0, need + 5)
        self.assertEqual((level, xp, reached), (2, 5, [2]))
        level, _, reached = stats.apply_xp(self.cfg, 1, 0, 100000)
        self.assertGreater(level, 5)
        self.assertEqual(reached, list(range(2, level + 1)))


class GachaTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.data = make_data(self.cfg)
        self.now = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Europe/Paris"))
        self.banners = gacha.banners(self.cfg, self.data, self.now)

    def test_rates_roughly_match(self):
        pulls, _ = gacha.roll(self.cfg, self.data, self.banners["standard"], 20000, 0, random.Random(1))
        counts = Counter(p.rarity for p in pulls)
        self.assertAlmostEqual(counts[1] / 20000, 0.7825, delta=0.03)
        self.assertAlmostEqual(counts[2] / 20000, 0.19, delta=0.02)
        self.assertGreater(counts[3], 0)

    def test_pity_guarantees_top_rarity(self):
        cfg = self.cfg
        pulls, pity = gacha.roll(cfg, self.data, self.banners["standard"], 1, cfg.gacha.pity_hero - 1, random.Random(0))
        self.assertEqual(pulls[0].rarity, 3)
        self.assertEqual(pity, 0)
        # Sans 3★, le compteur avance
        _, pity = gacha.roll(cfg, self.data, self.banners["standard"], 1, 5, _NoTopRng())
        self.assertEqual(pity, 6)

    def test_ten_pull_minimum(self):
        pulls, _ = gacha.roll(self.cfg, self.data, self.banners["standard"], 10, 0, _NoTopRng())
        self.assertTrue(any(p.rarity >= 2 for p in pulls))
        pulls, _ = gacha.roll(self.cfg, self.data, self.banners["weapon"], 10, 0, _NoTopRng())
        self.assertTrue(any(p.rarity >= 3 for p in pulls))

    def test_featured_banner(self):
        featured = self.banners["featured"].featured_hero
        self.assertIsNotNone(featured)
        self.assertEqual(self.data.hero(featured).rarity, 3)
        pulls, _ = gacha.roll(self.cfg, self.data, self.banners["featured"], 3000, 0, random.Random(2))
        three = [p for p in pulls if p.rarity == 3]
        share = sum(1 for p in three if p.key == featured) / len(three)
        self.assertGreater(share, 0.4)
        # Même semaine -> même héros vedette
        later = gacha.banners(self.cfg, self.data, self.now.replace(day=16))
        self.assertEqual(later["featured"].featured_hero, featured)

    def test_rotation_from_config(self):
        self.cfg.gacha["featured_rotation"] = ["119"]
        self.assertEqual(gacha.featured_hero_key(self.cfg, self.data, self.now), "119")

    def test_weapon_banner_pool(self):
        pulls, _ = gacha.roll(self.cfg, self.data, self.banners["weapon"], 500, 0, random.Random(3))
        for p in pulls:
            weapon = self.data.weapon(p.key)
            self.assertEqual(weapon.rarity, p.rarity)
            self.assertEqual(weapon.is_exclusive, p.rarity == 5)


class _NoTopRng(random.Random):
    """Ne tire jamais la rareté maximale."""

    def uniform(self, a, b):
        return b * 0.999


if __name__ == "__main__":
    unittest.main()
