"""Base des joueurs de ta classe, lue en arrière-plan sur le site officiel.

1. Recherche officielle filtrée par classe et triée par niveau → joueurs au niveau max.
2. Fiche + équipement de ~160 joueurs : CP, niveau d'objet, enchants, skills, stigmas…
   (sert à mesurer le CP et à l'Objectif CP).
3. Détail des objets des ~25 meilleurs : lignes de soul binding, sorts, pierres de mana,
   Arcana (sert aux conseils de rolls).
Tout est gardé sur le disque ; la mise à jour se refait seule quand la base a plus de 3 jours.
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from .api import AionAPI, ApiError
from .build import light_sample, make_build, norm
from .paths import read_json, usr, write_json

KEYWORDS = ["a", "e", "i", "o", "n", "r", "l", "s", "u", "t", "k", "m", "d", "h", "y", "c", "b", "g", "v", "z",
            "p", "f", "j", "x", "w", "q"]
SAMPLE_TARGET = 160     # collecte locale (secours) ; la base partagée en lit 6 000
SEARCH_PAGES = 2        # pages de 40 joueurs par mot-clé
TOP_N = 15          # builds détaillés des meilleurs, première collecte (rapide)
TOP_N_MAX = 30      # les mises à jour suivantes en ajoutent jusqu'à ce total
TOP_ADD = 10
DETAIL_SPREAD = 0   # builds détaillés en plus, répartis sur toute la plage de CP (base partagée : 170)


def select_detail(samples: list[dict], n_top: int, n_spread: int) -> list[dict]:
    """Les n_top meilleurs + n_spread joueurs répartis régulièrement sur le reste de la plage de CP,
    pour que les conseils valent aussi pour les joueurs qui ne sont pas tout en haut."""
    ordered = sorted(samples, key=lambda s: -s["cp"])
    best, rest = ordered[:n_top], ordered[n_top:]
    if n_spread and rest:
        step = len(rest) / min(n_spread, len(rest))
        best += [rest[int(i * step)] for i in range(min(n_spread, len(rest)))]
    return best
MAX_AGE = 3 * 86400       # au-delà : mise à jour automatique
SAMPLE_TTL = 6 * 86400    # un joueur relu au plus tous les 6 jours
DETAIL_TTL = SAMPLE_TTL   # builds détaillés : la base partagée les relit CHAQUE nuit (avant/après)
ARCHIVE_MAX = 0           # builds détaillés gardés en plus (base partagée : jusqu'à 1 000 au total)
ARCHIVE_DAYS = 30
OBS_MAX = 20000           # observations « avant/après » gardées par classe


SHARED_URL = "https://github.com/{owner}/aion2-cp-db/releases/download/data/{file}"
SHARED_OWNER = "TusbruOnthe"
SHARED_LEVEL = 45   # la base partagée contient des joueurs niveau 45


def download_shared(region: str, class_name: str) -> bool:
    """Télécharge la base partagée si elle est plus récente que la base locale."""
    import requests
    if not SHARED_OWNER:
        return False
    name = f"{region}_{class_name.lower().replace(' ', '')}.json"
    try:  # valeurs CP des stats, recalculées chaque nuit sur toutes les classes
        r = requests.get(SHARED_URL.format(owner=SHARED_OWNER, file="stat_weights.json"), timeout=30)
        if r.status_code == 200 and r.json().get("weights"):
            from .cpvalue import reload_weights
            write_json(usr("stat_weights.json"), r.json())
            reload_weights()
    except (requests.RequestException, ValueError):
        pass
    try:
        r = requests.get(SHARED_URL.format(owner=SHARED_OWNER, file=name), timeout=90)
        if r.status_code != 200:
            return False
        db = r.json()
    except (requests.RequestException, ValueError):
        return False
    local = load(region, class_name) or {}
    if not db.get("samples") or db.get("t", 0) <= local.get("t", 0):
        return False
    write_json(db_path(region, class_name), db)
    return True


def db_path(region: str, class_name: str) -> str:
    return usr("classdb", f"{region}_{class_name.lower().replace(' ', '')}.json")


def load(region: str, class_name: str) -> dict | None:
    return read_json(db_path(region, class_name))


def _median(xs):
    xs = [x for x in xs if x is not None]
    return float(statistics.median(xs)) if xs else None


def _top_entry(b: dict) -> dict:
    """Ce qu'on garde d'un des meilleurs joueurs (détail des objets compris)."""
    return {
        "name": b["name"], "server": b["server"], "cp": b["cp"], "ilvl": b["ilvl"], "t": time.time(),
        "gear": [{"slot": g["slot"], "group": g["group"], "name": g["name"], "grade": g["grade"], "icon": g["icon"],
                  "il": g["itemLevel"], "ench": g["enchant"], "exc": g["exceed"], "cap": g["lineCap"],
                  "stats": [[s["name"], s["v"], s["unit"]] for s in g["stats"]],
                  "skills": [s["name"] for s in g["skills"]],
                  "stones": [[s["name"], s["v"], s["grade"]] for s in g["stones"]]} for g in b["gear"] if g["detail"]],
        "arcana": [{"cat": a["category"] or a["slot"], "card": a["name"], "grade": a["grade"], "ench": a["enchant"],
                    "icon": a["icon"], "skills": [s["name"] for s in a["skills"]]} for a in b["arcana"]],
        "stigmas": {s["name"]: s["level"] for s in b["stigma"] if s["equip"]},
        "skills": {s["name"]: s["level"] for s in b["active"] + b["passive"] if s["acquired"]},
        "f": b["features"],
    }


class Harvester:
    """Construit / met à jour la base d'une classe. Thread-safe, une seule collecte à la fois."""

    def __init__(self, api: AionAPI):
        self.api = api
        self.lock = threading.Lock()
        self.running = None
        self.progress = {"stage": "", "done": 0, "total": 0, "msg": "", "t0": 0}
        self.stop = threading.Event()

    def _say(self, stage, done, total, msg=""):
        self.progress.update({"stage": stage, "done": done, "total": total, "msg": msg})

    def needs_update(self, region: str, class_name: str) -> bool:
        db = load(region, class_name)
        return not db or time.time() - db.get("t", 0) > MAX_AGE or len(db.get("samples", {})) < 60 \
            or len(db.get("top", {})) < 10

    def run(self, region: str, class_name: str, min_level: int, on_partial=None) -> dict:
        with self.lock:
            self.running = (region, class_name)
            self.progress["t0"] = time.time()
            try:
                return self._run(region, class_name, min_level, on_partial)
            finally:
                self.running = None

    def _run(self, region, class_name, min_level, on_partial):
        api = self.api
        db = load(region, class_name) or {"class": class_name, "region": region, "samples": {}, "top": {}}
        now = time.time()
        fresh = {k: s for k, s in db["samples"].items() if now - s.get("t", 0) < SAMPLE_TTL}
        pcids = api.class_pcids(region, class_name)
        self._say("Recherche des joueurs", 0, 1, "Recherche des joueurs de ta classe au niveau max…")
        cands = {}
        need_cands = int(SAMPLE_TARGET * 1.3)
        with ThreadPoolExecutor(24, thread_name_prefix="harvest") as ex:
            # page 1 de chaque mot-clé, puis page 2… : on s'arrête dès qu'on a assez de joueurs
            plan = [(kw, page) for page in range(1, SEARCH_PAGES + 1) for kw in KEYWORDS]
            ends, done_s = {}, 0
            while plan and len(cands) < need_cands and not self.stop.is_set():
                batch, plan = plan[:48], plan[48:]
                batch = [(kw, p) for kw, p in batch if p <= ends.get(kw, p)]
                futs = {ex.submit(api.search, region, kw, None, p, 40, pcids or None, "level", 86400): kw
                        for kw, p in batch}
                for f in as_completed(futs):
                    done_s += 1
                    try:
                        rows, end = f.result()
                    except ApiError:
                        continue
                    ends[futs[f]] = end
                    for r in rows:
                        if (r.get("level") or 0) >= min_level and r.get("characterId"):
                            cands.setdefault(r["characterId"], r)
                self._say("Recherche des joueurs", min(len(cands), need_cands), need_cands,
                          f"{len(cands)} joueurs trouvés ({done_s} recherches)")
            todo = [r for cid, r in cands.items() if cid not in fresh][: max(0, SAMPLE_TARGET - len(fresh))]
            # --- 2. fiches + équipement
            total = len(todo)
            done = 0
            self._say("Lecture des joueurs", 0, total, "Fiches des joueurs de ta classe")

            def fetch(r):
                cid, sid = r["characterId"], r.get("serverId")
                info = api.info(region, cid, sid, ttl=SAMPLE_TTL)
                prof = info.get("profile") or {}
                if prof.get("className") != class_name or not prof.get("combatPower"):
                    return None
                eq = api.equipment(region, cid, sid, ttl=SAMPLE_TTL)
                s = light_sample(info, eq)
                s.update({"cid": cid, "sid": sid, "t": time.time()})
                return s
            futs = [ex.submit(fetch, r) for r in todo]
            for f in as_completed(futs):
                done += 1
                try:
                    s = f.result()
                except ApiError:
                    s = None
                if s:
                    fresh[s["cid"]] = s
                if done % 10 == 0 or done == total:
                    self._say("Lecture des joueurs", done, total, f"{len(fresh)} joueurs dans la base")
                if self.stop.is_set():
                    break
            db["samples"] = fresh
            db["t_samples"] = time.time()
            write_json(db_path(region, class_name), db)
            if on_partial:
                on_partial(db)
            # --- 3. détail des objets des meilleurs
            prev_top = dict(db.get("top") or {})
            n_want = (TOP_N_MAX if prev_top else TOP_N) + DETAIL_SPREAD
            best = select_detail(list(fresh.values()), TOP_N_MAX if prev_top else TOP_N, DETAIL_SPREAD)
            if DETAIL_TTL < SAMPLE_TTL:
                # suivi : on garde le même panel de joueurs d'une nuit à l'autre pour voir ce qui change
                keep = [fresh[c] for c in prev_top if c in fresh]
                ids = {s["cid"] for s in keep}
                best = (keep + [s for s in best if s["cid"] not in ids])[:n_want]
            top = {k: v for k, v in prev_top.items()
                   if k in {b["cid"] for b in best} and now - v.get("t", 0) < DETAIL_TTL}
            need = [s for s in best if s["cid"] not in top][:TOP_ADD if db.get("top") else len(best)]
            self._say("Builds des meilleurs", len(top), len(best), "Détail des objets des meilleurs joueurs")
            done = len(top)

            def full(s):
                if self.stop.is_set():
                    return s, None
                raw = api.load_full(region, s["cid"], s["sid"], ttl=DETAIL_TTL, boards=False, pool=ex,
                                    only_random=True)
                b = make_build(raw, region)
                want = sum(1 for g in b["gear"] if g["pos"] not in (17, 22, 23, 24)) + len(b["arcana"])
                got = sum(1 for g in b["gear"] + b["arcana"] if g["detail"])
                if want and got < 0.8 * want:  # trop d'objets illisibles : on réessaiera plus tard
                    raise ApiError("build incomplet")
                return s, _top_entry(b)

            def save_partial():
                db["top"] = dict(top)
                db["agg"] = aggregate(list(top.values()))
                write_json(db_path(region, class_name), db)
                if on_partial:
                    on_partial(db)
            # plusieurs joueurs à la fois ; leurs requêtes partagent le pool `ex`.
            # Chaque build lu est sauvegardé tout de suite : les conseils s'affinent au fil de l'eau,
            # et une collecte interrompue (site saturé, appli fermée) reprend là où elle s'est arrêtée.
            with ThreadPoolExecutor(4, thread_name_prefix="harvest-o") as outer:
                for f in as_completed([outer.submit(full, s) for s in need]):
                    done += 1
                    try:
                        s, entry = f.result()
                    except ApiError:
                        continue
                    if entry:
                        old = prev_top.get(s["cid"])
                        if old:  # même joueur, nuit précédente : ce qui a changé et ce que ça a rapporté
                            from .statcal import observe
                            o = observe(old, entry, class_name)
                            if o:
                                db.setdefault("obs", []).append(o)
                        top[s["cid"]] = entry
                        if len(top) in (3, 6) or len(top) % (5 if len(need) < 40 else 25) == 0:
                            save_partial()
                    msg = f"{s['name']} ({s['cp']:,} CP)".replace(",", " ")
                    if api.limiter.rate < api.limiter.max_rate * 0.6:
                        msg += " · le site ralentit les requêtes, ça continue doucement"
                    self._say("Builds des meilleurs", done, len(best), msg)
        db["top"] = top
        db["agg"] = aggregate(list(top.values()))
        if ARCHIVE_MAX:  # les builds détaillés s'accumulent au fil des jours (pour mesurer la valeur des stats)
            arch = dict(db.get("archive") or {})
            arch.update({k: v for k, v in prev_top.items() if k not in top})
            arch = {k: v for k, v in arch.items() if k not in top and time.time() - v.get("t", 0) < ARCHIVE_DAYS * 86400}
            db["archive"] = dict(sorted(arch.items(), key=lambda kv: -kv[1].get("t", 0))[:ARCHIVE_MAX])
        db["obs"] = (db.get("obs") or [])[-OBS_MAX:]
        db["t"] = time.time()
        write_json(db_path(region, class_name), db)
        self._say("Terminé", 1, 1, f"{len(fresh)} joueurs, {len(top)} builds détaillés")
        return db


def aggregate(tops: list[dict]) -> dict:
    """Ce que font les meilleurs joueurs, pièce par pièce."""
    n = len(tops)
    slots = defaultdict(lambda: {"n": 0, "stats": defaultdict(list), "skills": Counter(), "stones": Counter(),
                                 "stones_n": 0, "ench": [], "items": Counter(), "cap": []})
    pool = defaultdict(lambda: defaultdict(list))  # groupe -> (stat, unité) -> [(niveau d'objet, valeur)]
    stig, skills = defaultdict(list), defaultdict(list)
    arcana = defaultdict(lambda: {"n": 0, "cards": Counter(), "skills": Counter(), "icons": {}})
    for t in tops:
        for g in t["gear"]:
            s = slots[g["group"]]
            s["n"] += 1
            s["ench"].append(g["ench"])
            s["items"][g["name"]] += 1
            s["cap"].append(g["cap"])
            for name, v, u in g["stats"]:
                s["stats"][(name, u)].append(v)
                pool[g["group"]][(name, u)].append((g["il"], v))
            for k in g["skills"]:
                s["skills"][k] += 1
            for name, v, gr in g["stones"]:
                s["stones"][name] += 1
                s["stones_n"] += 1
        for a in t["arcana"]:
            x = arcana[a["cat"]]
            x["n"] += 1
            x["cards"][a["card"]] += 1
            x["icons"][a["card"]] = a["icon"]
            for k in a["skills"]:
                x["skills"][k] += 1
        for k, v in t["stigmas"].items():
            stig[k].append(v)
        for k, v in t["skills"].items():
            skills[k].append(v)
    out = {}
    for k, s in slots.items():
        out[k] = {
            "n": s["n"], "ench": _median(s["ench"]), "items": dict(s["items"].most_common(3)),
            "stats": {f"{name}|{u}": {"name": name, "unit": u, "pct": len(v) / s["n"], "med": _median(v),
                                      "max": max(x for x in v if x is not None) if any(x is not None for x in v) else None}
                      for (name, u), v in sorted(s["stats"].items(), key=lambda kv: -len(kv[1]))},
            "skills": {name: c / s["n"] for name, c in s["skills"].most_common()},
            "stones": {name: c / max(s["stones_n"], 1) for name, c in s["stones"].most_common()},
        }
    pools = {}
    for grp, d in pool.items():
        pools[grp] = {f"{name}|{u}": {"name": name, "unit": u, "obs": len(v),
                                      "pts": sorted([[il, x] for il, x in v if x is not None])[:400]}
                      for (name, u), v in d.items()}
    return {
        "n": n, "cp_min": min((t["cp"] for t in tops), default=None),
        "cp_med": _median([t["cp"] for t in tops]), "slots": out, "pools": pools,
        "stigmas": {k: {"pct": len(v) / max(n, 1), "med": _median(v)} for k, v in sorted(stig.items(), key=lambda kv: -len(kv[1]))},
        "skills": {k: {"pct": len(v) / max(n, 1), "med": _median(v)} for k, v in skills.items()},
        "arcana": {k: {"n": a["n"], "cards": {c: x / a["n"] for c, x in a["cards"].most_common()},
                       "icons": a["icons"], "skills": {c: x / a["n"] for c, x in a["skills"].most_common(10)}}
                   for k, a in arcana.items()},
    }


def stat_range(agg: dict | None, group: str, name: str, unit: str, item_level: int) -> dict | None:
    """Plage observée d'une ligne sur ce type de pièce (objets de niveau proche en priorité)."""
    p = ((agg or {}).get("pools") or {}).get(group, {})
    e = p.get(f"{name}|{unit}")
    if not e:
        e = next((v for v in p.values() if norm(v["name"]) == norm(name) and v["unit"] == unit), None)
    if not e or not e["pts"]:
        return None
    near = [x for il, x in e["pts"] if abs(il - item_level) <= 8] if item_level else []
    vals = near if len(near) >= 3 else [x for _, x in e["pts"]]
    vals.sort()
    return {"lo": vals[0], "hi": vals[-1], "med": vals[len(vals) // 2], "n": len(vals), "near": len(near) >= 3}


def possible_stats(agg: dict | None, group: str) -> dict:
    """Stats déjà vues sur ce type de pièce chez les joueurs de la base : {norm(nom): (nom, unité, nb)}."""
    p = ((agg or {}).get("pools") or {}).get(group, {})
    return {norm(v["name"]): (v["name"], v["unit"], v["obs"]) for v in p.values()}


def agg_for(db: dict, my_cp: int) -> dict | None:
    """Agrégat de référence pour TES conseils : les ~50 builds détaillés juste au-dessus de ton CP
    (ce qui marche à ton niveau), ou les meilleurs s'il y en a trop peu. Les stats possibles par
    pièce viennent toujours de tous les builds détaillés."""
    tops = list((db or {}).get("top", {}).values())
    if not tops:
        return (db or {}).get("agg")
    above = sorted([t for t in tops if t["cp"] >= my_cp], key=lambda t: t["cp"])
    ref = above[:50] if len(above) >= 15 else sorted(tops, key=lambda t: -t["cp"])[:50]
    a = aggregate(ref)
    a["pools"] = ((db.get("agg") or {}).get("pools")) or aggregate(tops)["pools"]
    a["ref"] = {"n": len(ref), "lo": min(t["cp"] for t in ref), "hi": max(t["cp"] for t in ref),
                "near": len(above) >= 15}
    return a
