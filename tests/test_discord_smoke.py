"""Test de fumée : tous les cogs se chargent et les commandes sont valides pour Discord.

Nécessite discord.py (installé en CI via requirements.txt).
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_config, make_data

HAS_DISCORD = importlib.util.find_spec("discord") is not None

EXPECTED = {"aide", "profil", "classement", "invoquer", "bannieres", "heros", "arme", "team", "daily", "missions",
            "wordle", "colisee", "boss", "dummy", "boutique", "donner", "succes", "titre", "bot", "admin", "config"}


@unittest.skipUnless(HAS_DISCORD, "discord.py non installé")
class DiscordSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_cogs_load_and_commands_serialize(self):
        from discord import app_commands

        from bot.client import EXTENSIONS, LittlePrincess
        from bot.db import Database
        from bot.services.context import AppContext

        tmp = Path(tempfile.mkdtemp())
        cfg = make_config()
        db = Database(tmp / "smoke.db")
        await db.connect()
        bot = LittlePrincess(AppContext(cfg, db, make_data(cfg, tmp)), guild_id=None)
        for ext in EXTENSIONS:
            await bot.load_extension(ext)

        commands = bot.tree.get_commands()
        self.assertTrue(EXPECTED <= {c.name for c in commands}, EXPECTED - {c.name for c in commands})
        self.assertLessEqual(len(commands), 100)

        def walk(cmd):
            yield cmd
            if isinstance(cmd, app_commands.Group):
                for sub in cmd.commands:
                    yield from walk(sub)

        count = 0
        for top in commands:
            try:
                payload = top.to_dict(bot.tree)
            except TypeError:
                payload = top.to_dict()
            self.assertTrue(payload["name"])
            for cmd in walk(top):
                count += 1
                self.assertLessEqual(len(cmd.description), 100, cmd.qualified_name)
                if isinstance(cmd, app_commands.Command):
                    self.assertLessEqual(len(cmd.parameters), 25)
                    for param in cmd.parameters:
                        self.assertLessEqual(len(param.description or ""), 100, f"{cmd.qualified_name}.{param.name}")
        self.assertGreater(count, 40)
        await bot.close()


@unittest.skipUnless(HAS_DISCORD, "discord.py non installé")
class CommandSyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from bot.client import EXTENSIONS, LittlePrincess
        from bot.db import Database
        from bot.services.context import AppContext

        tmp = Path(tempfile.mkdtemp())
        cfg = make_config()
        self.db = Database(tmp / "sync.db")
        await self.db.connect()
        self.bot = LittlePrincess(AppContext(cfg, self.db, make_data(cfg, tmp)), guild_id=123)
        for ext in EXTENSIONS:
            await self.bot.load_extension(ext)

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_guild_sync_removes_stale_global_commands(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        tree = self.bot.tree
        tree.sync = AsyncMock(side_effect=lambda guild=None: list(tree.get_commands(guild=guild)))
        tree.fetch_commands = AsyncMock(return_value=[SimpleNamespace(name="ancienne"), SimpleNamespace(name="vieux")])
        count, removed = await self.bot.sync_commands()
        self.assertGreaterEqual(count, 20)
        self.assertEqual(removed, ["ancienne", "vieux"])
        self.assertEqual(tree.sync.await_count, 2)               # serveur puis global (vidé)
        self.assertEqual(tree.get_commands(), [])                 # plus aucune commande globale
        guild_names = {c.name for c in tree.get_commands(guild=SimpleNamespace(id=123))}
        self.assertIn("invoquer", guild_names)                   # nos commandes restent sur le serveur

        # Deuxième synchro (/bot commandes) : rien à nettoyer, les commandes du serveur sont conservées
        tree.sync.reset_mock()
        tree.fetch_commands = AsyncMock(return_value=[])
        count2, removed2 = await self.bot.sync_commands()
        self.assertEqual((count2, removed2), (count, []))
        self.assertEqual(tree.sync.await_count, 1)


if __name__ == "__main__":
    unittest.main()
