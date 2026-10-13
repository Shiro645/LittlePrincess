"""Gestion du temps : fuseau de la guilde, jour de jeu, semaine."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def tz(cfg) -> ZoneInfo:
    return ZoneInfo(str(cfg.general.timezone))


def now(cfg) -> datetime:
    return datetime.now(tz(cfg))


def game_day(cfg, when: datetime | None = None) -> date:
    """Jour de jeu (le reset quotidien se fait à daily.reset_hour)."""
    when = when or now(cfg)
    return (when - timedelta(hours=int(cfg.daily.reset_hour))).date()


def day_key(cfg, when: datetime | None = None) -> str:
    return game_day(cfg, when).isoformat()


def week_key(cfg, when: datetime | None = None) -> str:
    year, week, _ = game_day(cfg, when).isocalendar()
    return f"{year}-W{week:02d}"


def next_reset(cfg, when: datetime | None = None) -> datetime:
    when = when or now(cfg)
    day = game_day(cfg, when) + timedelta(days=1)
    return datetime(day.year, day.month, day.day, int(cfg.daily.reset_hour), tzinfo=tz(cfg))


def next_weekly(cfg, weekday: int, hour: int, minute: int, when: datetime | None = None) -> datetime:
    """Prochaine occurrence (ou l'occurrence en cours si on est pile dessus) d'un créneau hebdo."""
    when = when or now(cfg)
    candidate = when.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate += timedelta(days=(weekday - when.weekday()) % 7)
    if candidate < when - timedelta(seconds=59):
        candidate += timedelta(days=7)
    return candidate


def last_weekly(cfg, weekday: int, hour: int, minute: int, when: datetime | None = None) -> datetime:
    """Dernière occurrence passée d'un créneau hebdo."""
    when = when or now(cfg)
    candidate = when.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate -= timedelta(days=(when.weekday() - weekday) % 7)
    if candidate > when:
        candidate -= timedelta(days=7)
    return candidate


def discord_ts(when: datetime, style: str = "R") -> str:
    return f"<t:{int(when.timestamp())}:{style}>"
