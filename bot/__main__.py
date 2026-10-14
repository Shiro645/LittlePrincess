"""Point d'entrée : python -m bot"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from . import __version__
from .client import LittlePrincess, setup_logging
from .config import DATA_DIR, ConfigError, as_section, load_config, load_defaults, validate
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

    problems: list[str] = []
    try:
        cfg = load_config()
        problems = validate(cfg)
    except ConfigError as exc:
        problems = [str(exc)]
    if problems:
        log.error("config.json invalide : %s", " | ".join(problems))
        log.error("Le bot démarre avec les valeurs par défaut. Corrige config.json ou utilise /config.")
        cfg = as_section(load_defaults())
    data = GameData(cfg, DATA_DIR)
    data.load()
    db = Database(DATA_DIR / "littleprincess.db")
    await db.connect()

    log.info("LittlePrincess v%s — %d héros chargés (%s)", __version__, len(data.heroes), data.source)
    bot = LittlePrincess(AppContext(cfg, db, data), guild_id)
    bot.config_problems = problems
    async with bot:
        await bot.start(token)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
