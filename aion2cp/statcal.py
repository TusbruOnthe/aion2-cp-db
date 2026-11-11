"""Valeur en CP de chaque stat de ligne / pierre de mana, MESURÉE sur les builds détaillés.

Méthode : pour chaque build détaillé (250 par classe), on additionne chaque stat sur toutes ses
lignes et pierres, puis on explique le CP par une régression :
    CP = a·niveau d'objet + b·niveaux de stigmas + c·skills + Σ w_stat · total_stat
avec un effet fixe par classe. Le barème coréen (Vortex Gaming) sert de point de départ : les
données ne l'écartent que là où elles sont assez nettes (pénalité choisie par validation croisée).
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict

import numpy as np

BASE = ["ilvl", "stigma_lvl", "active_lvl", "passive_lvl"]
FLAT = {"attack": 1.25, "might": 1.25, "dexterity": 1.25, "intelligence": 1.25, "constitution": 1.25,
        "precision": 1.25, "willpower": 1.25, "defense": 1.0, "accuracy": 0.25, "critical hit": 0.25, "hp": 0.05,
        "block": 0.25, "evasion": 0.25, "front attack": 0.5, "back attack": 0.5}


def key(name, unit) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip() + "|" + (unit or "")


def prior(k: str) -> float:
    """Barème publié (ordre de grandeur) utilisé comme point de départ."""
    if k in ("ilvl",):
        return 44
    if k == "stigma_lvl":
        return 400
    if k == "active_lvl":
        return 100
    if k == "passive_lvl":
        return 200
    n, u = k.split("|")
    if "pvp" in n or n in ("mp", "natural mp regen", "natural hp regen", "flight power"):
        return 0
    if u == "%":
        return 20 if any(x in n for x in ("resist", "tolerance", "defense")) else 100
    if "resist" in n or "defense" in n:
        return 0.1
    return FLAT.get(n, 0.25)


PRIMARY = {"might", "dexterity", "intelligence", "precision"}  # stats principales offensives
DEF_PRIMARY = {"constitution", "willpower"}                    # PV / PM : défensives
OFF_PCT = ("combat speed", "damage boost", "weapon damage boost", "critical damage boost", "multi hit chance",
           "double chance", "attack increase", "move speed", "perfect chance", "cooldown", "skill speed")
OFF_FLAT = ("attack", "critical hit", "accuracy", "front attack", "back attack", "damage boost", "weapon damage boost",
            "critical damage boost", "critical attack")
DEFENSIVE = ("resist", "tolerance", "defense", "block", "evasion", "hp", "mp", "regen", "flight", "heal", "endurance")
FAMILIES = ["off_pct", "off_flat", "primary", "def_pct", "def_flat"]
FAMILY_PRIOR = {"off_pct": 100, "off_flat": 0.75, "primary": 1.25, "def_pct": 20, "def_flat": 0.2}
SHRINK_N = 800  # une stat ne s'écarte de sa famille que si elle est vue sur beaucoup de builds


def family(k: str) -> str:
    """Famille d'une stat. Les stats d'une même famille ont des valeurs du même ordre."""
    n, u = k.split("|")
    if "pvp" in n:
        return "util"
    if "penetration" in n:
        return "off_pct" if u == "%" else "off_flat"
    if n in DEF_PRIMARY or n.startswith("status effect"):  # PV/PM, chance d'étourdir… : utilitaires
        return "def_pct" if u == "%" else "def_flat"
    if n in PRIMARY:
        return "primary"
    if any(x in n for x in DEFENSIVE):
        return "def_pct" if u == "%" else "def_flat"
    if u == "%":
        return "off_pct" if any(n == x or n.startswith(x) for x in OFF_PCT) else "def_pct"
    return "off_flat" if any(n == x or n.startswith(x) for x in OFF_FLAT) else "def_flat"


def totals(entry) -> dict:
    """Somme de chaque stat sur toutes les lignes et pierres d'un build détaillé."""
    x = defaultdict(float)
    for g in entry["gear"]:
        for name, v, u in g["stats"]:
            if v is not None:
                x[key(name, u)] += v
        for name, v, _ in g["stones"]:
            if v is not None:
                x[key(name, "")] += v
    return x


SAME_F = ("ilvl", "stigma_lvl", "active_lvl", "passive_lvl", "dae_nodes", "titles", "arcana_lvl", "wing_ench",
          "pet_lvl", "ench_total", "exceed")


def observe(old: dict, new: dict, cls: str | None = None, max_days: float = 4) -> dict | None:
    """« Avant/après » propre : entre deux lectures du même joueur, SEULES ses lignes et pierres ont
    changé (mêmes objets, enchant, niveau d'objet, skills, stigmas, Daevanion, titres…). Le CP gagné
    ou perdu vient alors de ces lignes : c'est une mesure presque directe de leur valeur."""
    fo, fn = old.get("f"), new.get("f")
    if not fo or not fn or new.get("t", 0) - old.get("t", 0) > max_days * 86400:
        return None
    if any(abs(fo.get(k, 0) - fn.get(k, 0)) > 1e-9 for k in SAME_F):
        return None
    go = {g["slot"]: (g["name"], g["ench"], g["exc"]) for g in old["gear"]}
    gn = {g["slot"]: (g["name"], g["ench"], g["exc"]) for g in new["gear"]}
    if go != gn or [a["skills"] for a in old.get("arcana", [])] != [a["skills"] for a in new.get("arcana", [])]:
        return None
    to, tn = totals(old), totals(new)
    dx = {k: round(tn.get(k, 0) - to.get(k, 0), 3) for k in set(to) | set(tn)}
    dx = {k: v for k, v in dx.items() if abs(v) > 1e-6}
    dcp = new["cp"] - old["cp"]
    if not dx:
        return None
    return {"t": round(new.get("t", 0)), "cls": cls, "dcp": dcp, "dx": dx}


def build_rows(dbs: list[dict]):
    """Builds détaillés du jour + archive des jours précédents (le plus récent par joueur)."""
    rows = []
    for db in dbs:
        samples = db.get("samples") or {}
        entries = dict(db.get("archive") or {})
        entries.update(db.get("top") or {})
        for cid, t in entries.items():
            f = t.get("f") or (samples.get(cid) or {}).get("f")
            cp = t.get("cp") or (samples.get(cid) or {}).get("cp")
            if not f or not cp:
                continue
            rows.append((db.get("class"), cp, f, totals(t)))
    return rows


def fit(dbs: list[dict], min_share: float = 0.03, prev: dict | None = None) -> dict:
    rows = build_rows(dbs)
    if len(rows) < 200:
        raise ValueError("pas assez de builds détaillés")
    cnt = Counter(k for *_, x in rows for k in x)
    keys = [k for k, c in cnt.items() if c >= min_share * len(rows)]
    cols = BASE + keys
    A = np.array([[r[2].get(k, 0) for k in BASE] + [r[3].get(k, 0) for k in keys] for r in rows], float)
    y = np.array([r[1] for r in rows], float)
    cls = np.array([r[0] for r in rows])
    for c in set(cls):  # effet fixe par classe
        m = cls == c
        A[m] -= A[m].mean(0)
        y[m] -= y[m].mean()
    w0 = np.array([prior(k) for k in cols])
    d2 = (A ** 2).sum(0) + 1e-9

    def solve(A, y, lam):
        u = np.linalg.solve(A.T @ A + lam * np.diag(d2), A.T @ (y - A @ w0))
        return np.maximum(w0 + u, 0)
    rng = np.random.default_rng(1)
    folds = np.array_split(rng.permutation(len(y)), 5)
    best = None
    for lam in (0.03, 0.1, 0.3, 1, 3):
        err = []
        for k in range(5):
            te = folds[k]
            tr = np.concatenate([folds[j] for j in range(5) if j != k])
            err.append(np.abs(A[te] @ solve(A[tr], y[tr], lam) - y[te]))
        mae = float(np.concatenate(err).mean())
        if best is None or mae < best[0]:
            best = (mae, lam)
    lam = best[1]
    w = solve(A, y, lam)
    boots = np.array([solve(A[i], y[i], lam) for i in (rng.integers(0, len(y), len(y)) for _ in range(80))])
    lo, hi = np.percentile(boots, 10, 0), np.percentile(boots, 90, 0)
    ind = {k: (float(w[j]), float(lo[j]), float(hi[j])) for j, k in enumerate(cols)}

    # --- 2. par famille : bien plus robuste que stat par stat. Les meilleurs joueurs ont des objets
    # de haut niveau où TOUTES les lignes sont grosses (même les inutiles) : stat par stat, la
    # régression leur prête du CP à tort. Par famille, l'effet se moyenne.
    fam_keys = {f: [k for k in keys if family(k) == f] for f in FAMILIES}
    F = np.array([[r[2].get(k, 0) for k in BASE] + [sum(r[3].get(k, 0) for k in fam_keys[f]) for f in FAMILIES]
                  for r in rows], float)
    for c in set(cls):
        m = cls == c
        F[m] -= F[m].mean(0)
    f0 = np.array([prior(k) for k in BASE] + [FAMILY_PRIOR[f] for f in FAMILIES])
    fd2 = (F ** 2).sum(0) + 1e-9
    fw = np.maximum(f0 + np.linalg.solve(F.T @ F + 0.1 * np.diag(fd2), F.T @ (y - F @ f0)), 0)
    fam_w = dict(zip(BASE + FAMILIES, fw))

    def combine(k, v):
        if k in BASE:
            return fam_w[k]
        f = family(k)
        if f == "primary":  # Might, Intelligence… : la même stat principale selon la classe
            return fam_w["primary"]
        n = cnt.get(k, 0)
        shrunk = fam_w.get(f, 0) + (v - fam_w.get(f, 0)) * n / (n + SHRINK_N)
        if f in ("off_pct", "off_flat"):
            return min(max(shrunk, 0.5 * fam_w[f]), 1.6 * fam_w[f])
        cap = FAMILY_PRIOR[f] if f == "def_pct" else prior(k)
        return max(0.0, min(shrunk, cap))  # défensif / utilitaire : jamais au-dessus du barème

    out = {}
    for k in cols:
        v, l, h = ind[k]
        cw, cl, ch = combine(k, v), combine(k, l), combine(k, h)
        out[k] = {"w": round(cw, 3), "lo": round(min(cl, cw), 3), "hi": round(max(ch, cw), 3), "prior": prior(k),
                  "obs": int(cnt.get(k, len(rows))), "family": family(k) if k not in BASE else "base"}
    # --- 3. les « avant/après » : mesure presque directe, qui s'accumule nuit après nuit
    obs = [o for db in dbs for o in (db.get("obs") or [])]
    info = refine_with_obs(out, obs, prev)
    return {"t": time.time(), "n": len(rows), "lambda": lam, "mae": round(best[0]),
            "families": {k: round(float(v), 2) for k, v in fam_w.items()}, "weights": out, **info}


OBS_FULL = 30  # à partir de 30 avant/après sur une stat, on fait confiance à cette mesure


def refine_with_obs(out: dict, obs: list[dict], prev: dict | None = None) -> dict:
    """Corrige chaque valeur avec les avant/après où cette stat a changé. Plus une stat a
    d'observations, plus la mesure directe l'emporte sur la comparaison entre joueurs."""
    obs = [o for o in obs if o.get("dx") and all(k in out for k in o["dx"])]
    quality = list((prev or {}).get("quality") or [])
    res = {"nObs": len(obs), "quality": quality}
    if len(obs) < 20:
        return res
    keys = sorted({k for o in obs for k in o["dx"]})
    A = np.array([[o["dx"].get(k, 0) for k in keys] for o in obs], float)
    y = np.array([o["dcp"] for o in obs], float)
    wc = np.array([out[k]["w"] for k in keys])
    n_k = (A != 0).sum(0)
    # erreur, hors échantillon, des valeurs de la veille sur les nouvelles observations
    pw = (prev or {}).get("weights") or {}
    new_obs = [i for i, o in enumerate(obs) if o["t"] > (prev or {}).get("t", 0)]
    mae_prev = None
    if pw and new_obs:
        wp = np.array([(pw.get(k) or {}).get("w", out[k]["w"]) for k in keys])
        mae_prev = float(np.abs(A[new_obs] @ wp - y[new_obs]).mean())
    d = (A ** 2).sum(0) + 1e-9
    # retenue forte avec peu de mesures, quasi nulle quand elles s'accumulent
    u = np.linalg.solve(A.T @ A + np.diag(d * 3.0 / np.maximum(n_k, 1)), A.T @ (y - A @ wc))
    wn = np.maximum(wc + u, 0)
    for j, k in enumerate(keys):
        trust = min(1.0, n_k[j] / OBS_FULL)
        w = wc[j] + (wn[j] - wc[j]) * trust
        if trust < 1 and out[k].get("family") in ("def_pct", "def_flat", "util"):
            w = min(w, max(out[k]["prior"], wc[j]))  # pas encore assez de mesures : on garde le plafond
        out[k]["w"] = round(float(w), 3)
        out[k]["lo"] = round(min(out[k]["lo"], float(w)), 3)
        out[k]["hi"] = round(max(out[k]["hi"], float(w)), 3)
        out[k]["nObs"] = int(n_k[j])
        if trust >= 1:
            out[k]["direct"] = True
    w_final = np.array([out[k]["w"] for k in keys])
    quality.append({"t": round(time.time()), "nObs": len(obs), "newObs": len(new_obs),
                    "maePrev": round(mae_prev) if mae_prev is not None else None,
                    "maeNow": round(float(np.abs(A @ w_final - y).mean()))})
    res["quality"] = quality[-120:]
    return res


def moves(old: dict, new: dict) -> dict | None:
    """Ce qu'un joueur suivi a changé entre deux nuits, et le CP gagné (pour les tendances)."""
    fo, fn = old.get("f"), new.get("f")
    if not fo or not fn:
        return None
    ch = {}
    for k, lab in (("ench_total", "enchant"), ("exceed", "exceed"), ("dae_nodes", "dae"), ("arcana_lvl", "arcana")):
        d = fn.get(k, 0) - fo.get(k, 0)
        if d > 0:
            ch[lab] = d
    go = {g["slot"]: g for g in old["gear"]}
    pieces = [g["slot"] for g in new["gear"] if g["slot"] in go and go[g["slot"]]["name"] != g["name"]]
    rolled = [g["slot"] for g in new["gear"] if g["slot"] in go and go[g["slot"]]["name"] == g["name"]
              and [s[:2] for s in go[g["slot"]]["stats"]] != [s[:2] for s in g["stats"]]]
    if pieces:
        ch["gear"] = len(pieces)
    if rolled:
        ch["roll"] = len(rolled)
    st = {n: lv - (old.get("stigmas") or {}).get(n, 0) for n, lv in (new.get("stigmas") or {}).items()
          if lv > (old.get("stigmas") or {}).get(n, 0)}
    sk = {n: lv - (old.get("skills") or {}).get(n, 0) for n, lv in (new.get("skills") or {}).items()
          if n in (old.get("skills") or {}) and lv > old["skills"][n]}
    if st:
        ch["stigma"] = sum(st.values())
    if sk:
        ch["skill"] = sum(sk.values())
    dcp = new["cp"] - old["cp"]
    if not ch and dcp == 0:
        return None
    return {"t": round(new.get("t", 0)), "dcp": dcp, "ch": ch, "stigmas": st, "skills": sk, "pieces": pieces + rolled}
