import json
import random
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_config, make_data
from bot.db import Database
from bot.game.gacha import HERO, WEAPON, Pull
from bot.services.collection import Collection, GameError, add_pull_sync
from bot.services.context import AppContext
from bot.services.leveling import Leveling, add_xp_sync
from bot.services.players import Players, debit_sync, ensure_sync, format_rewards, merge_rewards
from bot.services.progress import Progress


class ServicesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_config()
        self.data = make_data(self.cfg, self.tmp)
        self.db = Database(self.tmp / "test.db")
        await self.db.connect()
        self.app = AppContext(self.cfg, self.db, self.data)
        self.players = Players(self.app)
        self.collection = Collection(self.app)
        self.progress = Progress(self.app)
        self.leveling = Leveling(self.app)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_new_player_and_currencies(self):
        player = await self.players.get(1)
        self.assertEqual(player["gems"], self.cfg.economy.start_gems)
        self.assertEqual(await self.players.titles(1), ["Novice"])
        self.assertFalse(await self.players.debit(1, {"gems": 10 ** 9}))
        self.assertTrue(await self.players.debit(1, {"gems": 300}))
        await self.players.credit(1, {"gold": 50, "hero_tickets": 2})
        player = await self.players.find(1)
        self.assertEqual(player["gems"], self.cfg.economy.start_gems - 300)
        self.assertEqual(player["hero_tickets"], 2)

    async def test_transaction_rollback(self):
        await self.players.get(1)

        def _tx(conn):
            debit_sync(conn, 1, {"gems": 100})
            raise GameError("stop")

        with self.assertRaises(GameError):
            await self.db.transaction(_tx)
        self.assertEqual((await self.players.find(1))["gems"], self.cfg.economy.start_gems)

    async def test_pulls_duplicates_and_conversion(self):
        pull = Pull(HERO, "seed-plitvice", 3)

        def _tx(conn):
            ensure_sync(conn, self.cfg, 1)
            return [add_pull_sync(conn, self.cfg, 1, pull, 3) for _ in range(4)]

        outcomes = await self.db.transaction(_tx)
        self.assertEqual([o.status for o in outcomes], ["new", "star_up", "star_up", "converted"])
        self.assertEqual(outcomes[2].stars, 5)
        self.assertEqual(outcomes[3].converted, dict(self.cfg.gacha.duplicate_conversion.hero["3"]))
        player = await self.players.find(1)
        self.assertEqual(player["gems"], self.cfg.economy.start_gems + self.cfg.gacha.duplicate_conversion.hero["3"]["gems"])

        wpull = Pull(WEAPON, "g-bow-1", 1)
        outcome = await self.db.transaction(lambda conn: add_pull_sync(conn, self.cfg, 1, wpull, 1))
        self.assertEqual((outcome.status, outcome.stars), ("new", 1))

    async def _give(self, user_id, key, stars=3, level=1):
        await self.players.get(user_id)
        await self.db.execute("INSERT INTO heroes (user_id, hero_key, stars, level) VALUES (?, ?, ?, ?)",
                              (user_id, key, stars, level))

    async def test_level_up(self):
        await self._give(1, "119", stars=1)
        await self.players.credit(1, {"gold": 10 ** 7})
        result = await self.collection.level_up(1, "hero", "119", 100)
        self.assertEqual((result.old_level, result.new_level, result.cap), (1, 20, 20))
        with self.assertRaises(GameError):
            await self.collection.level_up(1, "hero", "119", 1)
        with self.assertRaises(GameError):
            await self.collection.level_up(1, "hero", "seed-lahn", 1)

    async def test_level_up_limited_by_gold(self):
        await self._give(1, "119", stars=5)
        await self.db.execute("UPDATE players SET gold = 0 WHERE user_id = 1")
        with self.assertRaises(GameError):
            await self.collection.level_up(1, "hero", "119", 1)

    async def test_team_and_equip(self):
        await self._give(1, "119")
        await self._give(1, "seed-marina")
        with self.assertRaises(GameError):
            await self.collection.set_team(1, ["119", "seed-lahn"])
        with self.assertRaises(GameError):
            await self.collection.set_team(1, ["119", "119"])
        await self.collection.set_team(1, ["seed-marina", "119"])
        self.assertEqual(await self.collection.team_keys(1), ["seed-marina", "119"])

        await self.db.execute("INSERT INTO weapons (user_id, weapon_key, stars) VALUES (1, 'ex-119', 5)")
        await self.db.execute("INSERT INTO weapons (user_id, weapon_key, stars) VALUES (1, 'g-bow-1', 1)")
        with self.assertRaises(GameError):   # Eunha porte un panier, pas un arc
            await self.collection.equip(1, "119", "g-bow-1")
        await self.collection.equip(1, "119", "ex-119")
        units = await self.collection.team_units(1)
        self.assertEqual(units[1].weapon.key, "ex-119")

    async def test_missions_and_achievements(self):
        await self.players.get(1)
        daily = self.progress.daily_missions()
        self.assertEqual(len(daily), self.cfg.missions.daily_count)
        mission = daily[0]
        await self.progress.record(1, mission["type"], int(mission["target"]))
        total, claimed = await self.progress.claim_missions(1)
        self.assertIn(mission, claimed)
        total2, claimed2 = await self.progress.claim_missions(1)
        self.assertEqual(claimed2, [])

        unlocked = await self.progress.record(1, "summon", 1)
        self.assertIn("first_summon", [a["id"] for a in unlocked])
        self.assertEqual(await self.progress.record(1, "summon", 1), [])
        unlocked = await self.progress.set_max(1, "max_hero_level", 100)
        self.assertIn("hero_lv100", [a["id"] for a in unlocked])
        self.assertIn("Maître d'armes", await self.players.titles(1))

    async def test_leveling(self):
        result = await self.leveling.on_message(1, random.Random(1))
        self.assertIsNotNone(result)
        self.assertIsNone(await self.leveling.on_message(1))   # cooldown
        result = await self.db.transaction(lambda conn: add_xp_sync(conn, self.cfg, 1, 50000))
        self.assertTrue(result.leveled_up)
        self.assertIn("Aventurier", result.titles)
        self.assertGreater(result.rewards["gems"], 0)
        self.assertIn("level_10", [a["id"] for a in result.achievements])
        # Réactions plafonnées par jour
        total = 0
        for _ in range(100):
            await self.db.execute("UPDATE players SET last_react_xp = 0 WHERE user_id = 2")
            r = await self.leveling.on_reaction(2)
            total += r.gained if r else 0
        self.assertEqual(total, self.cfg.leveling.reaction_daily_cap)

    async def test_seed_migration(self):
        await self._give(1, "seed-plitvice", stars=4, level=30)
        await self._give(1, "seed-lahn", stars=3)
        await self.collection.set_team(1, ["seed-plitvice", "seed-lahn"])
        await self.db.execute("INSERT INTO weapons (user_id, weapon_key, stars) VALUES (1, 'ex-seed-plitvice', 5)")
        await self.db.execute("UPDATE heroes SET weapon_key = 'ex-seed-plitvice' WHERE hero_key = 'seed-plitvice'")
        # Simule les données de l'API : Plitvice existe sous la clé "50", Lahn n'existe pas
        api_heroes = json.loads((Path(__file__).resolve().parent.parent / "bot" / "data" / "seed_heroes.json")
                                .read_text(encoding="utf-8"))
        api = []
        for raw in api_heroes:
            if raw["key"] == "seed-plitvice":
                raw = dict(raw, key="50")
                raw.pop("_seed", None)
                api.append(raw)
            elif not raw["key"].startswith("seed-"):
                api.append(raw)
        (self.tmp / "heroes_cache.json").write_text(json.dumps({"fetched_at": "2026-10-14T00:00:00+00:00",
                                                                "heroes": api}), encoding="utf-8")
        self.data.load()
        self.assertEqual(self.data.source, "api")
        moved = await self.collection.migrate_seed_heroes()
        self.assertEqual(moved, 1)
        row = await self.collection.hero(1, "50")
        self.assertEqual((row["stars"], row["level"], row["weapon_key"]), (4, 30, "ex-50"))
        self.assertEqual(await self.collection.team_keys(1), ["50"])
        self.assertIsNotNone(await self.collection.weapon(1, "ex-50"))

    def test_format_rewards(self):
        self.assertEqual(format_rewards({"gems": 1500, "gold": 0}), "💎 1 500")
        self.assertEqual(merge_rewards({"gems": 1}, None, {"gems": 2, "gold": 3}), {"gems": 3, "gold": 3})


if __name__ == "__main__":
    unittest.main()
