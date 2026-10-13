import unittest

from tests.helpers import make_data
from bot.game import wordle
from bot import updates

CHANGELOG = """# Changelog

## [1.2.0] - 2026-11-01
- Nouveau boss

## [1.1.0] - 2026-10-20
- Boutique

## [1.0.0] - 2026-10-14
- Première version
"""


class WordleTest(unittest.TestCase):
    def test_compare(self):
        data = make_data()
        eunha, marina = data.hero("119"), data.hero("seed-marina")
        clues = {c.attribute: c.status for c in wordle.compare(eunha, eunha)}
        self.assertTrue(all(s == wordle.OK for s in clues.values()))
        clues = {c.attribute: c.status for c in wordle.compare(data.hero("1"), marina)}
        self.assertEqual(clues["rarity"], wordle.UP)
        self.assertEqual(clues["element"], wordle.NO)

    def test_answer_is_stable(self):
        data = make_data()
        a = wordle.pick_answer(data, "2026-10-14", "salt")
        self.assertEqual(a, wordle.pick_answer(data, "2026-10-14", "salt"))
        answers = {wordle.pick_answer(data, f"2026-10-{d:02d}", "salt") for d in range(1, 29)}
        self.assertGreater(len(answers), 5)


class UpdatesTest(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(updates.is_newer("1.10.0", "1.9.3"))
        self.assertFalse(updates.is_newer("1.0.0", "1.0.0"))
        self.assertEqual(updates.extract_version('__version__ = "2.3.4"\n'), "2.3.4")

    def test_changes_since(self):
        self.assertEqual([v for v, _, _ in updates.changes_since(CHANGELOG, "1.0.0", "1.2.0")], ["1.2.0", "1.1.0"])
        self.assertEqual([v for v, _, _ in updates.changes_since(CHANGELOG, None, "1.1.0")], ["1.1.0"])
        self.assertEqual(updates.changes_since(CHANGELOG, "1.2.0", "1.2.0"), [])
        version, title, body = updates.changelog_sections(CHANGELOG)[0]
        self.assertEqual((version, title, body), ("1.2.0", "2026-11-01", "- Nouveau boss"))

    def test_project_changelog_has_current_version(self):
        from bot import __version__
        versions = [v for v, _, _ in updates.changelog_sections(updates.read_changelog())]
        self.assertIn(__version__, versions)


if __name__ == "__main__":
    unittest.main()
