"""Point d'entrée : python -m bot"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from . import __version__
from .client import LittlePrincess, setup_logging
from .config import DATA_DIR, load_config
from .db import Database
from .gamedata import GameData
from .services.context import AppContext

log = logging.getLogger("bot")


async def main() -> int:
    setup_logging()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        log.error("DISCORD_TOKEN manquant : renseigne-le dans le fichier .env")
        return 1
    guild_id = int(os.getenv("GUILD_ID", "0") or 0) or None

    cfg = load_config()
    data = GameData(cfg, DATA_DIR)
    data.load()
    db = Database(DATA_DIR / "littleprincess.db")
    await db.connect()

    log.info("LittlePrincess v%s — %d héros chargés (%s)", __version__, len(data.heroes), data.source)
    bot = LittlePrincess(AppContext(cfg, db, data), guild_id)
    async with bot:
        await bot.start(token)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
