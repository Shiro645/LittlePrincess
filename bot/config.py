"""Configuration du bot : un seul fichier config.json, modifiable depuis Discord (/config).

Le fichier par défaut (livré avec le bot) est fusionné avec celui de l'utilisateur :
les clés absentes chez l'utilisateur prennent la valeur par défaut, ce qui permet
d'ajouter de nouvelles options lors d'une mise à jour sans casser une config existante.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterator, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path(os.getenv("LP_DEFAULT_CONFIG", ROOT / "config" / "config.json"))
USER_CONFIG = Path(os.getenv("LP_CONFIG", ROOT / "config" / "config.json"))
DATA_DIR = Path(os.getenv("LP_DATA_DIR", ROOT / "data"))
HELP_FILE = Path(__file__).resolve().parent / "data" / "config_help.json"


class ConfigError(ValueError):
    """Valeur ou fichier de configuration invalide (message affichable)."""


class Section(dict):
    """Dictionnaire accessible en attributs : cfg.gacha.cost_single."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(f"Clé de config manquante : {name}") from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def _wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return Section({k: _wrap(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def as_section(data: dict) -> Section:
    return _wrap(data)


def plain(value: Any) -> Any:
    """Copie profonde en dict/list ordinaires (pour modifier puis sauvegarder)."""
    return json.loads(json.dumps(value))


def deep_merge(base: dict, override: dict) -> dict:
    """Fusionne override dans base (récursif sur les dictionnaires, les listes sont remplacées)."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
#  Lecture / écriture
# ---------------------------------------------------------------------------

def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} n'est pas un JSON valide (ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}")
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} doit contenir un objet JSON {{ … }}")
    return data


def _inline(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def dumps(value: Any, indent: int = 0, width: int = 110, in_list: bool = False) -> str:
    """JSON lisible : sections dépliées, petites valeurs sur une ligne, un élément de liste par ligne."""
    if not isinstance(value, (dict, list)):
        return _inline(value)
    pad, inner = "  " * indent, "  " * (indent + 1)
    compact = _inline(value)
    limit = 220 if in_list else width
    if (indent >= 2 or in_list) and _depth(value) <= 2 and len(pad) + len(compact) <= limit:
        return compact
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {dumps(v, indent + 1, width)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if not value:
        return "[]"
    items = [f"{inner}{dumps(v, indent + 1, width, in_list=True)}" for v in value]
    return "[\n" + ",\n".join(items) + "\n" + pad + "]"


def _depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def write_json(path: Path, data: dict, backup: bool = True) -> None:
    """Écriture atomique ; l'ancienne version est gardée dans config.json.bak."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dumps(data) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_defaults(default_path: Path = DEFAULT_CONFIG) -> dict:
    return read_json(default_path)


def _stringify_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _stringify_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_keys(v) for v in value]
    return value


def migrate_yaml(user_path: Path, defaults: dict) -> bool:
    """v1.0.0 utilisait config.yaml : on le convertit une fois en config.json (l'ancien fichier est renommé)."""
    legacy = user_path.with_name("config.yaml")
    if user_path.exists() or not legacy.exists():
        return False
    try:
        import yaml
    except ImportError:
        return False
    try:
        data = yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Impossible de convertir l'ancien config.yaml : {exc}") from exc
    write_json(user_path, deep_merge(defaults, _stringify_keys(data)), backup=False)
    legacy.rename(legacy.with_name("config.yaml.migre"))
    return True


def load_config(default_path: Path = DEFAULT_CONFIG, user_path: Path = USER_CONFIG) -> Section:
    """Charge la config. Crée le fichier utilisateur (copie complète des valeurs par défaut) s'il n'existe pas."""
    defaults = read_json(default_path)
    if default_path != user_path and not user_path.exists():
        if not migrate_yaml(user_path, defaults):
            write_json(user_path, defaults, backup=False)
    return _wrap(deep_merge(defaults, read_json(user_path)))


def int_keys(mapping: dict) -> dict:
    """Les clés JSON sont des textes ('3') : on les convertit en entiers."""
    return {int(k): v for k, v in (mapping or {}).items()}


# ---------------------------------------------------------------------------
#  Chemins : "gacha.pity_hero", "shop.items.0.price.gold"
# ---------------------------------------------------------------------------

def split_path(path: str) -> list[str]:
    parts = [p for p in re.split(r"[./]", (path or "").strip()) if p]
    if not parts:
        raise ConfigError("Chemin vide.")
    return parts


def _step(node: Any, part: str, path: str) -> Any:
    if isinstance(node, dict):
        if part not in node:
            raise ConfigError(f"Clé inconnue : `{path}`")
        return node[part]
    if isinstance(node, list):
        if not part.isdigit() or int(part) >= len(node):
            raise ConfigError(f"Index invalide dans `{path}` (la liste a {len(node)} élément(s), de 0 à {len(node) - 1})")
        return node[int(part)]
    raise ConfigError(f"`{path}` : impossible d'aller plus loin qu'une valeur simple")


def get_path(data: Any, path: str) -> Any:
    node = data
    for part in split_path(path):
        node = _step(node, part, path)
    return node


def has_path(data: Any, path: str) -> bool:
    try:
        get_path(data, path)
        return True
    except ConfigError:
        return False


def _is_free_map(node: dict) -> bool:
    """Dictionnaire dont on peut ajouter des clés (ex. niveau -> rôle)."""
    return not node or all(re.fullmatch(r"-?\d+", str(k)) for k in node)


def set_path(data: dict, path: str, value: Any) -> None:
    parts = split_path(path)
    parent = data
    for i, part in enumerate(parts[:-1]):
        parent = _step(parent, part, ".".join(parts[: i + 1]))
    last = parts[-1]
    if isinstance(parent, dict):
        if last not in parent and not _is_free_map(parent):
            raise ConfigError(f"Clé inconnue : `{path}` (vérifie l'orthographe avec l'autocomplétion)")
        parent[last] = value
    elif isinstance(parent, list):
        if not last.isdigit() or int(last) > len(parent):
            raise ConfigError(f"Index invalide : `{path}` (0 à {len(parent)} pour ajouter à la fin)")
        if int(last) == len(parent):
            parent.append(value)
        else:
            parent[int(last)] = value
    else:
        raise ConfigError(f"`{'.'.join(parts[:-1])}` est une valeur simple")


def delete_path(data: dict, path: str) -> Any:
    parts = split_path(path)
    parent = get_path(data, ".".join(parts[:-1])) if len(parts) > 1 else data
    last = parts[-1]
    if isinstance(parent, list):
        _step(parent, last, path)
        return parent.pop(int(last))
    if isinstance(parent, dict):
        if last not in parent:
            raise ConfigError(f"Clé inconnue : `{path}`")
        if not _is_free_map(parent):
            raise ConfigError("Seuls les éléments de liste et les entrées du type « niveau → valeur » peuvent être "
                              "supprimés. Utilise `/config reinitialiser` pour revenir à la valeur par défaut.")
        return parent.pop(last)
    raise ConfigError(f"`{path}` ne peut pas être supprimé")


def iter_paths(data: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Tous les chemins (sections et valeurs) de la config."""
    items = data.items() if isinstance(data, dict) else enumerate(data) if isinstance(data, list) else ()
    for key, value in items:
        path = f"{prefix}.{key}" if prefix else str(key)
        yield path, value
        if isinstance(value, (dict, list)):
            yield from iter_paths(value, path)


# ---------------------------------------------------------------------------
#  Conversion des valeurs saisies sur Discord
# ---------------------------------------------------------------------------

_TRUE = {"true", "vrai", "oui", "yes", "on", "1"}
_FALSE = {"false", "faux", "non", "no", "off", "0"}


def type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "booléen (true/false)"
    if isinstance(value, int):
        return "nombre entier"
    if isinstance(value, float):
        return "nombre"
    if isinstance(value, str):
        return "texte"
    if isinstance(value, list):
        return "liste JSON [ … ]"
    if isinstance(value, dict):
        return "objet JSON { … }"
    return "valeur"


def coerce_value(raw: str, current: Any) -> Any:
    """Transforme le texte saisi en valeur du même type que la valeur actuelle."""
    text = (raw or "").strip()
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        parsed = text
    if current is None:
        return parsed
    if isinstance(current, bool):
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ConfigError("Valeur attendue : true ou false (oui / non).")
    if isinstance(current, (int, float)):
        number = parsed
        if isinstance(parsed, str):
            try:
                number = float(parsed.replace(",", ".").replace(" ", ""))
            except ValueError:
                raise ConfigError(f"Valeur attendue : {type_name(current)}.") from None
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ConfigError(f"Valeur attendue : {type_name(current)}.")
        if isinstance(current, int) and not float(number).is_integer():
            raise ConfigError("Valeur attendue : nombre entier (sans virgule).")
        return int(number) if isinstance(current, int) else float(number)
    if isinstance(current, str):
        return parsed if isinstance(parsed, str) else text
    if isinstance(current, list) and isinstance(parsed, list):
        return parsed
    if isinstance(current, dict) and isinstance(parsed, dict):
        return parsed
    raise ConfigError(f"Valeur attendue : {type_name(current)}. Exemple : "
                      + ("`[123, 456]`" if isinstance(current, list) else '`{"gems": 100, "gold": 1000}`'))


# ---------------------------------------------------------------------------
#  Validation
# ---------------------------------------------------------------------------

def validate(cfg: dict) -> list[str]:
    """Vérifie la cohérence de la config. Retourne la liste des problèmes (vide = OK)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    def get(path: str, default: Any = None) -> Any:
        try:
            return get_path(cfg, path)
        except ConfigError:
            errors.append(f"`{path}` est manquant")
            return default

    def number(path: str, lo: float, hi: float) -> None:
        value = get(path, lo)
        check(isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi,
              f"`{path}` doit être un nombre entre {lo} et {hi}")

    try:
        ZoneInfo(str(get("general.timezone", "Europe/Paris")))
    except (ZoneInfoNotFoundError, ValueError):
        errors.append("`general.timezone` : fuseau horaire inconnu (ex. Europe/Paris)")
    check(bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", str(get("general.embed_color", "#000000")))),
          "`general.embed_color` doit être une couleur hexadécimale (ex. #F5A9D0)")

    for path in ("channels.announcements", "channels.admin", "channels.level_up", "channels.achievements",
                 "channels.boss", "channels.wordle", "roles.boss_ping"):
        value = get(path, 0)
        check(isinstance(value, int) and not isinstance(value, bool) and value >= 0, f"`{path}` doit être un ID (nombre)")
    for path in ("channels.allowed", "channels.xp_excluded", "roles.staff"):
        value = get(path, [])
        check(isinstance(value, list) and all(isinstance(v, int) for v in value), f"`{path}` doit être une liste d'IDs")

    for path in ("gacha.hero_rates", "gacha.weapon_rates"):
        rates = get(path, {})
        if isinstance(rates, dict) and rates:
            try:
                total = sum(float(v) for v in rates.values())
                check(abs(total - 100) < 0.01, f"`{path}` : la somme des taux doit faire 100 % (actuellement {total:g} %)")
                check(all(str(k).isdigit() for k in rates), f"`{path}` : les clés doivent être des raretés (1, 2, 3…)")
            except (TypeError, ValueError):
                errors.append(f"`{path}` : les taux doivent être des nombres")
        else:
            errors.append(f"`{path}` doit contenir les taux par rareté")
    number("gacha.cost_single", 0, 10 ** 9)
    number("gacha.cost_ten", 0, 10 ** 9)
    number("gacha.featured_share", 0, 1)
    number("gacha.pity_hero", 0, 10 ** 6)
    number("gacha.pity_weapon", 0, 10 ** 6)
    number("gacha.featured_change_weekday", 0, 6)
    number("gacha.featured_change_hour", 0, 23)

    for path in ("progression.max_level_by_stars", "progression.star_factor"):
        table = get(path, {})
        check(isinstance(table, dict) and {str(s) for s in range(1, 6)} <= {str(k) for k in table},
              f"`{path}` doit définir les étoiles 1 à 5")
    number("progression.level_factor_min", 0, 1)
    number("progression.level_reference", 1, 10 ** 4)
    number("combat.team_size", 1, 4)
    number("combat.def_constant", 1, 10 ** 9)
    number("combat.tank_taunt_chance", 0, 1)
    number("combat.cc_skip_chance", 0, 1)
    number("combat.seconds_per_turn", 0.1, 3600)

    for path in ("daily.rewards", "wordle.rewards"):
        value = get(path, [])
        check(isinstance(value, list) and value and all(isinstance(r, dict) for r in value),
              f"`{path}` doit être une liste non vide de récompenses")
    number("daily.reset_hour", 0, 23)
    number("wordle.max_attempts", 1, 50)
    number("colosseum.min_bet", 0, 10 ** 9)
    number("colosseum.max_bet", 0, 10 ** 9)
    number("dummy.turns", 1, 100)

    number("boss.weekday", 0, 6)
    number("boss.hour", 0, 23)
    number("boss.minute", 0, 59)
    number("boss.solo_end_weekday", 0, 6)
    number("boss.solo_end_hour", 0, 23)
    number("boss.solo_end_minute", 0, 59)
    number("boss.max_turns", 1, 100)
    number("boss.turn_seconds", 10, 900)
    check(get("boss.hp_mode", "adaptive") in ("adaptive", "fixed"), "`boss.hp_mode` : adaptive ou fixed")
    check(get("boss.afk_action", "auto") in ("auto", "attack", "skill", "guard"),
          "`boss.afk_action` : auto, attack, skill ou guard")
    roster = get("boss.roster", [])
    if not isinstance(roster, list) or not roster:
        errors.append("`boss.roster` doit contenir au moins un boss")
    else:
        for i, boss in enumerate(roster):
            where = f"`boss.roster.{i}`"
            if not isinstance(boss, dict) or not boss.get("id") or not boss.get("name"):
                errors.append(f"{where} : chaque boss a besoin d'un id et d'un name")
                continue
            attacks = boss.get("attacks")
            if not isinstance(attacks, list) or not attacks:
                errors.append(f"{where} : au moins une attaque est nécessaire")
                continue
            for j, attack in enumerate(attacks):
                ok = isinstance(attack, dict) and attack.get("type") in ("single", "aoe") \
                    and isinstance(attack.get("hp_pct"), (int, float))
                check(ok, f"{where}.attacks.{j} : type single/aoe et hp_pct (nombre) obligatoires")

    for i, item in enumerate(get("shop.items", []) or []):
        ok = isinstance(item, dict) and item.get("id") and item.get("name") and isinstance(item.get("price"), dict) \
            and item.get("type") in ("gems", "hero_ticket", "weapon_ticket", "star_up")
        check(ok, f"`shop.items.{i}` : id, name, price {{…}} et type (gems, hero_ticket, weapon_ticket, star_up) obligatoires")
    for i, ach in enumerate(get("achievements", []) or []):
        ok = isinstance(ach, dict) and all(ach.get(k) for k in ("id", "name", "stat")) \
            and isinstance(ach.get("target"), (int, float))
        check(ok, f"`achievements.{i}` : id, name, stat et target obligatoires")
    for pool in ("missions.daily_pool", "missions.weekly_pool"):
        for i, mission in enumerate(get(pool, []) or []):
            ok = isinstance(mission, dict) and all(mission.get(k) for k in ("id", "type", "name")) \
                and isinstance(mission.get("target"), (int, float))
            check(ok, f"`{pool}.{i}` : id, type, name et target obligatoires")

    for section, key in (("achievements", "id"), ("shop.items", "id"), ("boss.roster", "id"),
                         ("missions.daily_pool", "id"), ("missions.weekly_pool", "id")):
        items = get(section, []) or []
        ids = [item.get(key) for item in items if isinstance(item, dict)]
        check(len(ids) == len(set(ids)), f"`{section}` : deux éléments ont le même id")
    return errors


# ---------------------------------------------------------------------------
#  Aide (descriptions des paramètres)
# ---------------------------------------------------------------------------

_help_cache: Optional[dict] = None


def help_for(path: str) -> Optional[str]:
    """Description d'un paramètre (cherche aussi dans les sections parentes pour les éléments de liste)."""
    global _help_cache
    if _help_cache is None:
        try:
            _help_cache = json.loads(HELP_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _help_cache = {}
    parts = split_path(path)
    exact = ".".join(parts)
    if exact in _help_cache:
        return _help_cache[exact]
    generic = ".".join("*" if p.isdigit() else p for p in parts)
    if generic in _help_cache:
        return _help_cache[generic]
    while parts:
        parts = parts[:-1]
        key = ".".join(p for p in parts if not p.isdigit())
        if key and key in _help_cache and any(p.isdigit() for p in split_path(path)):
            return _help_cache[key]
    return None
