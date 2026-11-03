"""Config panel: every node of the default config renders within Discord's limits.

Requires discord.py (installed in CI through requirements.txt).
"""

import importlib.util
import unittest
from types import SimpleNamespace

from tests.helpers import make_config
from bot.config import iter_paths, plain

HAS_DISCORD = importlib.util.find_spec("discord") is not None


@unittest.skipUnless(HAS_DISCORD, "discord.py is not installed")
class ConfigPanelTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = make_config()
        self.bot = SimpleNamespace(cfg=self.cfg, version="test")
        self.cog = SimpleNamespace(bot=self.bot)

    def check_view(self, view):
        components = view.to_components()
        self.assertLessEqual(len(components), 5)
        for row in components:
            self.assertLessEqual(len(row["components"]), 5)
            for item in row["components"]:
                self.assertLessEqual(len(item.get("label") or ""), 80)
                for option in item.get("options", []):
                    self.assertLessEqual(len(option["label"]), 100)
                    self.assertLessEqual(len(option.get("description") or ""), 100)
                self.assertLessEqual(len(item.get("options", [])), 25)

    async def test_panel(self):
        from bot.ui.config_panel import ConfigPanelView, panel_embed

        view = ConfigPanelView(self.bot)
        self.check_view(view)
        self.assertTrue(all(item.custom_id for item in view.children), "persistent view needs custom_ids")
        embed = panel_embed(self.bot)
        self.assertLessEqual(len(embed), 6000)

    async def test_every_node_renders(self):
        from bot.ui.config_panel import NodeView, node_kind

        count = 0
        for path, value in iter_paths(plain(self.cfg)):
            if node_kind(path, value) != "node":
                continue
            embed, view = NodeView.build(self.cog, path)
            self.check_view(view)
            self.assertLessEqual(len(embed.description or ""), 4096)
            embed, view = NodeView.build(self.cog, path, confirm_reset=True, status="✅ ok")
            self.check_view(view)
            count += 1
        self.assertGreater(count, 30)

    async def test_kinds_and_pickers(self):
        from bot.ui.config_panel import NodeView, PickerView, ValueModal, display_value, item_label, node_kind

        self.assertEqual(node_kind("channels.boss", 0), "channel")
        self.assertEqual(node_kind("channels.allowed", []), "channels")
        self.assertEqual(node_kind("roles.staff", []), "roles")
        self.assertEqual(node_kind("roles.boss_ping", 0), "role")
        self.assertEqual(node_kind("leveling.level_roles.10", 123), "level_role")
        self.assertEqual(node_kind("gifts.enabled", True), "bool")
        self.assertEqual(node_kind("gacha.pity_hero", 80), "scalar")
        self.assertEqual(node_kind("gacha", {}), "node")
        self.assertEqual(display_value("channels.boss", 0), "*désactivé*")
        self.assertEqual(display_value("channels.boss", 42), "<#42>")
        self.assertEqual(item_label(0, {"id": "x", "name": "Boss"}), "0 — Boss")

        browser = NodeView(self.cog, "channels")
        for path, kind, value in (("channels.boss", "channel", 0), ("channels.allowed", "channels", []),
                                  ("roles.staff", "roles", []), ("roles.boss_ping", "role", 0),
                                  ("leveling.level_roles.10", "level_role", 0)):
            embed, view = PickerView.build(browser, path, kind, value)
            self.check_view(view)
        modal = ValueModal(browser, "gacha.pity_hero", 80)
        self.assertEqual(modal.field.default, "80")
        modal = ValueModal(browser, "shop.items.0", plain(self.cfg)["shop"]["items"][0], json_mode=True)
        self.assertIn('"id"', modal.field.default)
        self.assertLessEqual(len(modal.title), 45)


if __name__ == "__main__":
    unittest.main()
