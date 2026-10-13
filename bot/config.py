"""Chargement de la configuration YAML.

Le fichier par défaut (livré avec le bot) est fusionné avec le fichier de
l'utilisateur : les clés absentes chez l'utilisateur prennent la valeur par
défaut, ce qui permet d'ajouter de nouvelles options lors d'une mise à jour
sans casser une config existante.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path(os.getenv("LP_DEFAULT_CONFIG", ROOT / "config" / "config.yaml"))
USER_CONFIG = Path(os.getenv("LP_CONFIG", ROOT / "config" / "config.yaml"))
DATA_DIR = Path(os.getenv("LP_DATA_DIR", ROOT / "data"))


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


def deep_merge(base: dict, override: dict) -> dict:
    """Fusionne override dans base (récursif sur les dictionnaires)."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(default_path: Path = DEFAULT_CONFIG, user_path: Path = USER_CONFIG) -> Section:
    """Charge la config. Crée le fichier utilisateur s'il n'existe pas."""
    if default_path != user_path and not user_path.exists() and default_path.exists():
        user_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(default_path, user_path)
    merged = deep_merge(_read(default_path), _read(user_path))
    return _wrap(merged)


def int_keys(mapping: dict) -> dict:
    """YAML peut renvoyer des clés '3' ou 3 : on normalise en int."""
    return {int(k): v for k, v in (mapping or {}).items()}
