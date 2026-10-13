"""Versions, changelog et mise à jour via Watchtower."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
_VERSION_RE = re.compile(r'__version__\s*=\s*["\'](\d+(?:\.\d+)*)["\']')
_SECTION_RE = re.compile(r"^##\s*\[?(\d+(?:\.\d+)*)\]?(.*)$", re.MULTILINE)


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text or "0"))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def extract_version(source: str) -> Optional[str]:
    match = _VERSION_RE.search(source or "")
    return match.group(1) if match else None


def changelog_sections(text: str) -> list[tuple[str, str, str]]:
    """Retourne [(version, titre, contenu)] dans l'ordre du fichier."""
    matches = list(_SECTION_RE.finditer(text))
    sections = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = match.group(2).strip(" -—")
        sections.append((match.group(1), title, text[match.end():end].strip()))
    return sections


def changes_since(text: str, since: Optional[str], current: str) -> list[tuple[str, str, str]]:
    """Sections du changelog entre `since` (exclu) et `current` (inclus)."""
    result = []
    for version, title, body in changelog_sections(text):
        if is_newer(version, current):
            continue
        if since is None:
            if version == current:
                result.append((version, title, body))
        elif is_newer(version, since):
            result.append((version, title, body))
    return result


def read_changelog() -> str:
    try:
        return CHANGELOG.read_text(encoding="utf-8")
    except OSError:
        return ""


async def fetch_remote_version(repository: str, branch: str, user_agent: str) -> Optional[str]:
    import aiohttp

    url = f"https://raw.githubusercontent.com/{repository}/{branch}/bot/__init__.py"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": user_agent}, trust_env=True) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                log.warning("Vérification de version : HTTP %s sur %s", resp.status, url)
                return None
            return extract_version(await resp.text())


async def trigger_watchtower(url: str, timeout_s: float = 600) -> tuple[bool, str]:
    """Demande à Watchtower de mettre à jour le bot. Le bot sera probablement arrêté pendant l'appel."""
    import aiohttp

    token = os.getenv("WATCHTOWER_TOKEN", "").strip()
    if not token:
        return False, "WATCHTOWER_TOKEN n'est pas défini dans .env"
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{url.rstrip('/')}/v1/update",
                                    headers={"Authorization": f"Bearer {token}"}) as resp:
                body = (await resp.text())[:300]
                if resp.status == 200:
                    return True, body
                return False, f"HTTP {resp.status} : {body}"
    except aiohttp.ClientError as exc:
        return False, f"Watchtower injoignable ({exc}). Vérifie que le container watchtower tourne."
