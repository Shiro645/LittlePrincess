"""Outils communs aux tests."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import load_config  # noqa: E402
from bot.gamedata import GameData  # noqa: E402

DEFAULT_CONFIG = ROOT / "config" / "config.json"


def make_config():
    return load_config(DEFAULT_CONFIG, DEFAULT_CONFIG)


def make_data(cfg=None, data_dir=None) -> GameData:
    cfg = cfg or make_config()
    data = GameData(cfg, Path(data_dir or tempfile.mkdtemp()))
    data.load()
    return data
