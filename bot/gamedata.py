"""Données de jeu : héros (API Guardian Tales TOP) et armes.

Les héros viennent de l'API https://gtales.top (token requis). Les réponses
sont mises en cache dans data/heroes_cache.json. Tant qu'aucun token n'est
configuré, le bot utilise un petit jeu de données de démarrage
(bot/data/seed_heroes.json) aux stats approximatives.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

SEED_FILE = Path(__file__).resolve().parent / "data" / "seed_heroes.json"
SEED_PREFIX = "seed-"

ELEMENTS = ("basic", "fire", "water", "earth", "light", "dark")
ROLES = ("warrior", "tank", "ranged", "support")
STATES = ("downed", "airborne", "injured")
WEAPON_TYPES = ("sword_1h", "sword_2h", "rifle", "bow", "basket", "staff", "gauntlet", "claw")


# ---------------------------------------------------------------------------
#  Normalisation
# ---------------------------------------------------------------------------

def normalize_name(text: str) -> str:
    """'Future Princess' -> 'futureprincess' (sans accents ni ponctuation)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value).replace(" ", ""))
    if not match:
        return default
    return int(float(match.group(0).replace(",", ".")))


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else default


def normalize_element(value: Any) -> str:
    text = normalize_name(str(value or ""))
    if text in ELEMENTS:
        return text
    if text in ("normal", "neutral", "none", ""):
        return "basic"
    for element in ELEMENTS:
        if element in text:
            return element
    return "basic"


def normalize_role(value: Any) -> str:
    text = normalize_name(str(value or ""))
    for role in ROLES:
        if role in text:
            return role
    if "range" in text:
        return "ranged"
    if "heal" in text or "supp" in text:
        return "support"
    return "warrior"


def normalize_state(value: Any) -> Optional[str]:
    text = normalize_name(str(value or ""))
    if not text:
        return None
    if "down" in text:
        return "downed"
    if "air" in text:
        return "airborne"
    if "injur" in text:
        return "injured"
    return None


def parse_chain_states(value: Any) -> tuple[Optional[str], Optional[str]]:
    """'downed-airborne' -> ('downed', 'airborne') ; 'injured' -> ('injured', None)."""
    if not value:
        return None, None
    parts = re.split(r"\s*(?:-|>|→|/|,|to)\s*", str(value).strip().lower())
    states = [normalize_state(p) for p in parts if p]
    states = [s for s in states if s]
    if not states:
        return None, None
    return states[0], (states[1] if len(states) > 1 else None)


def normalize_weapon_type(value: Any) -> Optional[str]:
    text = normalize_name(str(value or ""))
    if not text:
        return None
    if text in WEAPON_TYPES or text.replace("_", "") in [w.replace("_", "") for w in WEAPON_TYPES]:
        for wt in WEAPON_TYPES:
            if wt.replace("_", "") == text.replace("_", ""):
                return wt
    if "basket" in text:
        return "basket"
    if "bow" in text:
        return "bow"
    if "rifle" in text or "gun" in text:
        return "rifle"
    if "staff" in text or "wand" in text:
        return "staff"
    if "gauntlet" in text or "glove" in text or "fist" in text:
        return "gauntlet"
    if "claw" in text:
        return "claw"
    if "two" in text or "2h" in text or "twohand" in text:
        return "sword_2h"
    if "sword" in text or "one" in text or "1h" in text:
        return "sword_1h"
    return None


def parse_percent_multiplier(value: Any) -> Optional[float]:
    """'300%' -> 3.0"""
    if value in (None, ""):
        return None
    pct = to_float(value, default=-1)
    return pct / 100 if pct > 0 else None


def parse_dps_multiplier(description: str) -> Optional[float]:
    """'inflict 500% of DPS as melee damage' -> 5.0"""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%\s*(?:of|de)", description or "", re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", ".")) / 100


# ---------------------------------------------------------------------------
#  Effets (buffs d'équipe, options d'armes…)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Effect:
    """Un bonus en pourcentage (ou en points pour les chances de critique)."""

    stat: str                 # atk, hp, def, heal, crit_chance, crit_mult, skill_dmg, ranged_dmg, melee_dmg, dmg, dmg_vs
    value: float
    element: Optional[str] = None      # ne s'applique qu'aux alliés de cet élément
    vs_element: Optional[str] = None   # pour dmg_vs : contre les ennemis de cet élément


_STAT_PATTERNS: list[tuple[str, str]] = [
    (r"crit(?:ical)?\s*hit\s*chance|crit(?:ical)?\s*chance|crit\s*rate", "crit_chance"),
    (r"crit(?:ical)?\s*(?:hit\s*)?(?:multiplier|damage|dmg)", "crit_mult"),
    (r"weapon\s*skill\s*regen", "skill_regen"),
    (r"skill\s*(?:damage|dmg)", "skill_dmg"),
    (r"ranged\s*(?:damage|dmg)", "ranged_dmg"),
    (r"melee\s*(?:damage|dmg)", "melee_dmg"),
    (r"\bheal", "heal"),
    (r"\bdef(?:ense|ence)?\b", "def"),
    (r"\b(?:max\s*)?hp\b", "hp"),
    (r"\batk\b|\battack\b", "atk"),
    (r"\bdamage\b|\bdmg\b", "dmg"),
]
_ELEMENT_RE = r"\b(basic|fire|water|earth|light|dark)\b"


def parse_effects(text: str) -> list[Effect]:
    """Analyse un texte du type 'Fire type Atk +30% / Crit Hit Chance 4%'.

    Ce qui n'est pas reconnu est ignoré (le texte reste affiché tel quel).
    """
    effects: list[Effect] = []
    if not text:
        return effects
    for segment in re.split(r"\s*(?:/|,|;|\n|\band\b)\s*", text):
        pct = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*%", segment)
        if not pct:
            continue
        value = float(pct.group(1).replace(",", "."))
        lowered = segment.lower()
        stat = None
        for pattern, name in _STAT_PATTERNS:
            if re.search(pattern, lowered):
                stat = name
                break
        if stat is None or stat == "skill_regen":
            continue
        vs = re.search(r"(?:against|to|vs\.?)\s+" + _ELEMENT_RE, lowered)
        if vs and stat in ("dmg", "atk"):
            effects.append(Effect("dmg_vs", value, vs_element=normalize_element(vs.group(1))))
            continue
        ally = re.search(_ELEMENT_RE + r"\s*(?:type|element|heroes?)?", lowered)
        element = normalize_element(ally.group(1)) if ally else None
        effects.append(Effect(stat, value, element=element))
    return effects


# ---------------------------------------------------------------------------
#  Modèles
# ---------------------------------------------------------------------------

@dataclass
class Hero:
    key: str
    name: str
    atr: str
    role: str
    element: str
    rarity: int
    atk: int
    hp: int
    defense: int
    heal: int
    weapon_type: str
    party_text: str = ""
    party_effects: list[Effect] = field(default_factory=list)
    self_effects: list[Effect] = field(default_factory=list)
    na_name: str = ""
    na_desc: str = ""
    ability_name: str = ""
    ability_desc: str = ""
    skill_name: str = ""
    skill_desc: str = ""
    skill_mult: float = 2.5
    skill_cd_seconds: float = 10
    skill_state: Optional[str] = None
    chain_name: str = ""
    chain_desc: str = ""
    chain_start: Optional[str] = None
    chain_end: Optional[str] = None
    chain_mult: float = 3.0
    exclusive_weapon: Optional[str] = None   # clé de l'arme exclusive
    seed: bool = False
    image_url: Optional[str] = None


@dataclass
class WeaponDef:
    key: str
    name: str
    weapon_type: str
    rarity: int
    atk: int
    exclusive_hero: Optional[str] = None
    effects: list[Effect] = field(default_factory=list)
    effect_text: str = ""
    stats_text: str = ""
    image_url: Optional[str] = None

    @property
    def is_exclusive(self) -> bool:
        return self.exclusive_hero is not None


def _pick_variant(raw: dict, variant: str) -> dict:
    variants = raw.get("variants") or []
    if not variants:
        return {}
    for v in variants:
        if str(v.get("version", "")).lower() == variant.lower():
            return v
    return variants[0]


def parse_hero(raw: dict, cfg, assets_base: str) -> Optional[Hero]:
    """Transforme une réponse /api/heroes?hero=KEY en Hero. None si inutilisable."""
    key = str(raw.get("key") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not key or not name:
        return None
    v = _pick_variant(raw, cfg.gamedata.get("variant", "Base"))
    stats = v.get("stats") or raw.get("stats") or {}
    atk, hp = to_int(stats.get("atk")), to_int(stats.get("hp"))
    if atk <= 0 or hp <= 0:
        return None
    role = normalize_role(raw.get("role"))
    element = normalize_element(raw.get("element"))
    rarity = max(1, min(3, to_int(raw.get("rarity"), 1)))
    atr = str(raw.get("atr") or "").strip()

    weapon = v.get("weapon") or {}
    skill = v.get("skill") or {}
    chain = v.get("chain") or {}
    na = v.get("na") or {}
    ability = v.get("ability") or {}

    weapon_type = normalize_weapon_type(weapon.get("type"))
    if weapon_type is None:
        weapon_type = cfg.gamedata.default_weapon_by_role.get(role, "sword_1h")

    chain_start, chain_end = parse_chain_states(chain.get("stun"))
    party_text = str(v.get("party") or raw.get("party") or "")

    cd = to_float(skill.get("cd"), default=10.0) or 10.0
    hero = Hero(
        key=key,
        name=name,
        atr=atr,
        role=role,
        element=element,
        rarity=rarity,
        atk=atk,
        hp=hp,
        defense=to_int(stats.get("def")),
        heal=to_int(stats.get("heal")),
        weapon_type=weapon_type,
        party_text=party_text,
        party_effects=parse_effects(party_text),
        self_effects=parse_effects(str(raw.get("otherStats") or "")),
        na_name=str(na.get("title") or ""),
        na_desc=str(na.get("description") or ""),
        ability_name=str(ability.get("title") or ""),
        ability_desc=str(ability.get("description") or ""),
        skill_name=str(skill.get("title") or ""),
        skill_desc=str(skill.get("description") or ""),
        skill_mult=parse_percent_multiplier(skill.get("dmg")) or cfg.combat.default_skill_multiplier,
        skill_cd_seconds=cd,
        skill_state=normalize_state(skill.get("stun")),
        chain_name=str(chain.get("title") or ""),
        chain_desc=str(chain.get("description") or ""),
        chain_start=chain_start,
        chain_end=chain_end,
        chain_mult=parse_dps_multiplier(str(chain.get("description") or "")) or cfg.combat.default_chain_multiplier,
        seed=bool(raw.get("_seed")),
        image_url=f"{assets_base}/assets/heroes/{atr}.webp" if atr else None,
    )
    if weapon.get("name"):
        hero.exclusive_weapon = f"ex-{key}"
    return hero


def parse_exclusive_weapon(raw: dict, hero: Hero, cfg, assets_base: str) -> Optional[WeaponDef]:
    v = _pick_variant(raw, cfg.gamedata.get("variant", "Base"))
    weapon = v.get("weapon") or {}
    if not weapon.get("name"):
        return None
    stats_text = str(weapon.get("stats") or "")
    options_text = str(weapon.get("options") or "")
    effects = [e for e in parse_effects(stats_text) + parse_effects(options_text) if e.stat != "heal"]
    return WeaponDef(
        key=f"ex-{hero.key}",
        name=str(weapon["name"]),
        weapon_type=hero.weapon_type,
        rarity=5,
        atk=to_int(weapon.get("atk"), default=1400) or 1400,
        exclusive_hero=hero.key,
        effects=effects,
        effect_text=str(weapon.get("effect") or ""),
        stats_text=" / ".join(t for t in (stats_text, options_text) if t),
        image_url=f"{assets_base}/assets/weapons/{hero.atr}.webp" if hero.atr else None,
    )


def build_generic_weapons(cfg) -> dict[str, WeaponDef]:
    weapons: dict[str, WeaponDef] = {}
    atk_by_rarity = {int(k): int(v) for k, v in cfg.weapons.generic_atk.items()}
    for wtype, names in cfg.weapons.generic_names.items():
        for index, name in enumerate(names[:4]):
            rarity = index + 1
            key = f"g-{wtype}-{rarity}"
            weapons[key] = WeaponDef(
                key=key,
                name=name,
                weapon_type=wtype,
                rarity=rarity,
                atk=atk_by_rarity.get(rarity, 500),
            )
    return weapons


# ---------------------------------------------------------------------------
#  Conteneur principal
# ---------------------------------------------------------------------------

class GameData:
    def __init__(self, cfg, data_dir: Path):
        self.cfg = cfg
        self.data_dir = Path(data_dir)
        self.cache_file = self.data_dir / "heroes_cache.json"
        self.heroes: dict[str, Hero] = {}
        self.weapons: dict[str, WeaponDef] = {}
        self.source = "seed"
        self.fetched_at: Optional[datetime] = None
        self.seed_names: dict[str, str] = {}
        self._by_name: dict[str, str] = {}

    # -- chargement ---------------------------------------------------------

    @property
    def assets_base(self) -> str:
        return str(self.cfg.gamedata.api_base_url).rstrip("/")

    def load(self) -> None:
        seed_raw = self._read_seed()
        self.seed_names = {
            str(h["key"]): normalize_name(h["name"]) for h in seed_raw if str(h["key"]).startswith(SEED_PREFIX)
        }
        raw_heroes: list[dict] = seed_raw
        self.source = "seed"
        self.fetched_at = None
        if self.cache_file.exists():
            try:
                cache = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if cache.get("heroes"):
                    raw_heroes = cache["heroes"]
                    self.source = "api"
                    self.fetched_at = datetime.fromisoformat(cache["fetched_at"])
            except (ValueError, KeyError, OSError) as exc:
                log.warning("Cache des héros illisible (%s), utilisation des données de démarrage", exc)
        self._build(raw_heroes)

    def _read_seed(self) -> list[dict]:
        try:
            return json.loads(SEED_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("Impossible de lire %s : %s", SEED_FILE, exc)
            return []

    def _build(self, raw_heroes: list[dict]) -> None:
        excluded = {str(k) for k in self.cfg.gamedata.get("exclude_heroes", [])}
        include_only = {str(k) for k in self.cfg.gamedata.get("include_only", [])}
        heroes: dict[str, Hero] = {}
        weapons = build_generic_weapons(self.cfg)
        for raw in raw_heroes:
            try:
                hero = parse_hero(raw, self.cfg, self.assets_base)
            except Exception:  # une fiche mal formée ne doit pas tout casser
                log.exception("Fiche héros invalide : %s", raw.get("key"))
                continue
            if hero is None or hero.key in excluded:
                continue
            if include_only and hero.key not in include_only:
                continue
            heroes[hero.key] = hero
            ex = parse_exclusive_weapon(raw, hero, self.cfg, self.assets_base)
            if ex:
                weapons[ex.key] = ex
            else:
                hero.exclusive_weapon = None
        self.heroes = heroes
        self.weapons = weapons
        self._by_name = {normalize_name(h.name): h.key for h in heroes.values()}
        log.info("%d héros et %d armes chargés (source : %s)", len(heroes), len(weapons), self.source)

    # -- synchronisation avec l'API -----------------------------------------

    def needs_sync(self) -> bool:
        if self.source != "api" or self.fetched_at is None:
            return True
        age = datetime.now(timezone.utc) - self.fetched_at
        return age.days >= int(self.cfg.gamedata.sync_interval_days)

    async def sync_from_api(self, token: str, user_agent: str) -> int:
        """Télécharge tous les héros depuis l'API. Retourne le nombre de héros."""
        import aiohttp  # fourni par discord.py

        if not token:
            raise RuntimeError("Aucun token API (GTALES_API_TOKEN) configuré dans .env")
        headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent, "Accept": "application/json"}
        base = self.assets_base
        delay = float(self.cfg.gamedata.request_delay_seconds)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout, trust_env=True) as session:
            async with session.get(f"{base}/api/heroes") as resp:
                if resp.status == 401:
                    raise RuntimeError("Token API refusé (401). Vérifie GTALES_API_TOKEN.")
                resp.raise_for_status()
                index = await resp.json(content_type=None)
            if not isinstance(index, list):
                raise RuntimeError("Réponse inattendue de /api/heroes")
            details: list[dict] = []
            for entry in index:
                key = str(entry.get("key") or "")
                if not key:
                    continue
                for attempt in range(3):
                    try:
                        async with session.get(f"{base}/api/heroes", params={"hero": key}) as resp:
                            if resp.status == 429:
                                await asyncio.sleep(5 * (attempt + 1))
                                continue
                            resp.raise_for_status()
                            detail = await resp.json(content_type=None)
                        if isinstance(detail, dict):
                            detail.setdefault("atr", entry.get("atr"))
                            details.append(detail)
                        break
                    except aiohttp.ClientError as exc:
                        log.warning("Héros %s : %s (essai %d)", key, exc, attempt + 1)
                        await asyncio.sleep(2)
                await asyncio.sleep(delay)
        if not details:
            raise RuntimeError("Aucun héros récupéré depuis l'API")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": datetime.now(timezone.utc).isoformat(), "heroes": details}
        tmp = self.cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cache_file)
        self.load()
        return len(self.heroes)

    # -- recherche ------------------------------------------------------------

    def hero(self, key: str) -> Optional[Hero]:
        return self.heroes.get(str(key))

    def weapon(self, key: str) -> Optional[WeaponDef]:
        return self.weapons.get(str(key))

    def find_hero(self, query: str) -> Optional[Hero]:
        """Trouve un héros par clé ou par nom (insensible aux accents/majuscules)."""
        if not query:
            return None
        if query in self.heroes:
            return self.heroes[query]
        norm = normalize_name(query)
        if norm in self._by_name:
            return self.heroes[self._by_name[norm]]
        matches = [k for n, k in self._by_name.items() if norm and norm in n]
        return self.heroes[matches[0]] if len(matches) == 1 else None

    def search_heroes(self, query: str, keys: Optional[list[str]] = None, limit: int = 25) -> list[Hero]:
        norm = normalize_name(query)
        pool = [self.heroes[k] for k in keys if k in self.heroes] if keys is not None else list(self.heroes.values())
        if norm:
            starts = [h for h in pool if normalize_name(h.name).startswith(norm)]
            contains = [h for h in pool if norm in normalize_name(h.name) and h not in starts]
            pool = starts + contains
        else:
            pool = sorted(pool, key=lambda h: (-h.rarity, h.name))
        return pool[:limit]

    def heroes_by_rarity(self, rarity: int) -> list[Hero]:
        return sorted((h for h in self.heroes.values() if h.rarity == rarity), key=lambda h: h.key)

    def weapons_by_rarity(self, rarity: int) -> list[WeaponDef]:
        return sorted((w for w in self.weapons.values() if w.rarity == rarity), key=lambda w: w.key)

    def resolve_seed_key(self, seed_key: str) -> Optional[str]:
        """Retrouve, dans les données de l'API, le héros correspondant à un héros de démarrage."""
        name = self.seed_names.get(seed_key)
        return self._by_name.get(name) if name else None
