"""Emplacements des fichiers : ressources livrées avec l'appli et données de l'utilisateur."""
import json
import os
import sys


def resource_dir() -> str:
    """Dossier des fichiers livrés (web/, data/). Marche aussi dans l'exe PyInstaller."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_dir() -> str:
    """Données propres au joueur : cache, base de classe, réglages."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "AION2-Conseiller-CP")
    os.makedirs(d, exist_ok=True)
    return d


def res(*parts) -> str:
    return os.path.join(resource_dir(), *parts)


def usr(*parts) -> str:
    p = os.path.join(user_dir(), *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str, data, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)
