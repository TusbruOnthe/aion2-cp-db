"""Combien de CP vaut une amélioration ?

La formule officielle du CP n'est pas publique. Deux sources, toujours citées à l'écran :

1. Barèmes publiés (ordre de grandeur, pas la formule du jeu) :
   - lignes de stats : barème coréen relevé par Vortex Gaming (22/03/2026) ;
   - niveaux de skills et de stigmas : relevés Inven KR (18/03/2026).
2. Mesure sur la base : régression du CP des joueurs de TA classe (même niveau) sur
   leur niveau d'objet et leurs niveaux de skills. Donne « CP par niveau d'objet »,
   qui chiffre enchant, amplification, Daevanion, meilleur stuff, Arcana.

Garde-fou : un gain hors des bornes plausibles (ex. +1 600 CP pour un niveau de pet)
n'est jamais affiché ; on écrit « effet non mesurable » à la place.
"""
from __future__ import annotations

import numpy as np

from .build import norm

SRC_STATS = "barème coréen Vortex Gaming (22/03/2026), non vérifié sur Global"
SRC_SKILLS = "relevés Inven KR (18/03/2026)"
SRC_MODEL = "mesuré sur les joueurs de ta classe"
SRC_STIGMA_COST = "coûts en éclats : daevaguides.com (08/10/2026) et aion2builds.org (09/10/2026)"

PCT_CP = 100.0  # +1 % (dégâts, vitesse, crit dmg…) ≈ 100 CP
FLAT_CP = {"attack": 1.25, "might": 1.25, "dexterity": 1.25, "intelligence": 1.25, "constitution": 1.25,
           "precision": 1.25, "willpower": 1.25, "defense": 1.0, "accuracy": 0.25, "critical hit": 0.25,
           "penetration": 1.25, "front attack": 0.5, "back attack": 0.5, "hp": 0.05, "mp": 0.0,
           "block": 0.25, "evasion": 0.25}
# Lignes de stats qui ne valent rien en PvE / dans le CP selon le barème
LOW_VALUE = ("resist", "pvp", "regen", "mp")

# Ordres de grandeur maximum plausibles par unité, pour bloquer les chiffres absurdes
PLAUSIBLE_MAX = {"ilvl": 120, "active_lvl": 1500, "passive_lvl": 700, "stigma_lvl": 1500,
                 "pet_lvl": 150, "wing_ench": 600, "titles": 150, "line": 8000}

_WEIGHTS = None


def stat_weights() -> dict:
    """Valeurs mesurées sur les builds détaillés (base partagée, recalculées chaque nuit).
    Fichier téléchargé > fichier livré avec l'appli."""
    global _WEIGHTS
    if _WEIGHTS is None:
        from .paths import read_json, res, usr
        d = read_json(usr("stat_weights.json")) or read_json(res("data", "stat_weights.json")) or {}
        _WEIGHTS = d
    return _WEIGHTS


def reload_weights():
    global _WEIGHTS
    _WEIGHTS = None


def stat_weight(name: str, unit: str):
    """(CP par point, bas, haut, mesuré ?) pour une stat."""
    w = (stat_weights().get("weights") or {}).get(f"{norm(name)}|{unit or ''}")
    if w:
        return w["w"], w["lo"], w["hi"], True
    v = stat_cp_prior(name, 1.0, unit) or 0.0
    return v, v, v, False


def stat_cp(name: str, value, unit: str) -> float | None:
    if value is None:
        return None
    return value * stat_weight(name, unit)[0]


def stat_cp_range(name: str, value, unit: str):
    if value is None:
        return None
    w, lo, hi, measured = stat_weight(name, unit)
    return value * lo, value * hi, measured


def src_stats() -> str:
    d = stat_weights()
    if d.get("weights"):
        import datetime
        day = datetime.datetime.fromtimestamp(d.get("t", 0)).strftime("%d/%m/%Y")
        return (f"mesuré sur {d.get('n')} builds détaillés de la base partagée ({day}), "
                "en partant du barème coréen Vortex Gaming")
    return SRC_STATS


def stat_cp_prior(name: str, value, unit: str) -> float | None:
    """Barème coréen (ordre de grandeur), utilisé seulement pour une stat jamais mesurée."""
    if value is None:
        return None
    n = norm(name)
    if any(k in n for k in ("pvp",)) or (unit != "%" and n in ("mp", "natural mp regen", "natural hp regen")):
        return 0.0
    if unit == "%":
        if "resist" in n:
            return value * PCT_CP * 0.2
        return value * PCT_CP
    w = FLAT_CP.get(n)
    if w is None:
        w = 0.1 if "resist" in n else 0.25
    return value * w


def skill_step(category: str, level: int) -> float:
    """CP gagné en passant au niveau `level` (depuis level-1)."""
    if category == "stigma":
        return 225 if level <= 5 else 400 if level <= 10 else 700 if level <= 15 else 1200
    if category == "passive":
        return 200
    return {12: 500, 16: 900, 20: 1000}.get(level, 100)


def skill_gain(category: str, cur: int, to: int) -> float:
    return float(sum(skill_step(category, lv) for lv in range(cur + 1, to + 1)))


def stigma_shards(level: int) -> int:
    """Éclats pour passer au niveau `level`."""
    return 1 if level <= 5 else 2 if level <= 10 else 4 if level <= 15 else 8


def stigma_cost(cur: int, to: int) -> int:
    return sum(stigma_shards(lv) for lv in range(cur + 1, to + 1))


class CPModel:
    """Régression positive CP ~ niveau d'objet + niveaux de skills, sur les joueurs de la base."""

    FEATS = ("ilvl", "active_lvl", "passive_lvl", "stigma_lvl")

    def __init__(self):
        self.ok = False
        self.coef, self.lo, self.hi = {}, {}, {}
        self.n, self.r2, self.mae, self.intercept = 0, 0.0, 0.0, 0.0

    @staticmethod
    def _nnls(X, y):
        """Moindres carrés avec coefficients >= 0 (on retire les négatifs un par un)."""
        active = list(range(X.shape[1]))
        coef = np.zeros(X.shape[1])
        for _ in range(X.shape[1]):
            A = np.c_[X[:, active], np.ones(len(y))]
            w = np.linalg.lstsq(A, y, rcond=None)[0]
            if (w[:-1] >= 0).all():
                coef[:] = 0
                coef[active] = w[:-1]
                return coef, w[-1]
            active = [a for a, wi in zip(active, w[:-1]) if wi >= 0]
            if not active:
                break
        return coef, float(y.mean())

    def fit(self, samples: list[dict]) -> "CPModel":
        rows = [s for s in complete_samples(samples) if s.get("cp") and s.get("f", {}).get("ilvl")]
        if len(rows) < 30:
            return self
        X = np.array([[s["f"].get(k, 0) for k in self.FEATS] for s in rows], float)
        y = np.array([s["cp"] for s in rows], float)
        coef, b0 = self._nnls(X, y)
        self.intercept = float(b0)
        pred = X @ coef + b0
        self.r2 = float(1 - ((y - pred) ** 2).sum() / max(((y - y.mean()) ** 2).sum(), 1e-9))
        self.mae = float(np.abs(y - pred).mean())
        rng = np.random.default_rng(1)
        boots = np.array([self._nnls(X[i], y[i])[0] for i in (rng.integers(0, len(y), len(y)) for _ in range(120))])
        for j, k in enumerate(self.FEATS):
            self.coef[k] = float(coef[j])
            self.lo[k] = float(np.percentile(boots[:, j], 10))
            self.hi[k] = float(np.percentile(boots[:, j], 90))
        self.n = len(rows)
        self.ok = self.r2 > 0.5 and self.coef.get("ilvl", 0) > 0
        return self

    def per_ilvl(self) -> float | None:
        c = self.coef.get("ilvl")
        if not self.ok or not c or c > PLAUSIBLE_MAX["ilvl"]:
            return None
        return c

    def to_dict(self):
        return {"ok": self.ok, "coef": self.coef, "lo": self.lo, "hi": self.hi, "n": self.n,
                "r2": self.r2, "mae": self.mae}


def complete_samples(samples: list[dict]) -> list[dict]:
    """Écarte les joueurs dont le site renvoie des skills incomplets (0 stigma, ou moins de 60 %
    des niveaux de skills habituels) : sinon ils paraissent « très efficaces » à tort."""
    acts = sorted(s["f"].get("active_lvl", 0) for s in samples if s.get("f"))
    med = acts[len(acts) // 2] if acts else 0
    return [s for s in samples if is_complete(s.get("f") or {}, med)]


def is_complete(f: dict, med_active: float) -> bool:
    return f.get("stigma_lvl", 0) > 0 and f.get("active_lvl", 0) >= 0.6 * med_active


def guard(kind: str, per_unit: float | None) -> float | None:
    """None si la valeur par unité n'est pas plausible : on n'affiche pas de faux chiffre."""
    if per_unit is None or per_unit <= 0:
        return None
    cap = PLAUSIBLE_MAX.get(kind)
    if cap and per_unit > cap:
        return None
    return per_unit


def fmt_cp(v) -> str:
    if v is None:
        return "non mesurable"
    return f"{round(v):,}".replace(",", " ")
