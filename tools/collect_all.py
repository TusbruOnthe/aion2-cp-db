"""Collecte partagée : construit la base de chaque classe et l'écrit dans db/<région>_<classe>.json.

Lancé chaque jour par GitHub Actions (.github/workflows/collect.yml). L'appli télécharge
ces fichiers au lieu de refaire la collecte sur chaque PC.
Usage : python tools/collect_all.py [--regions eu,nae] [--classes Cleric,Ranger]
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aion2cp import classdb  # noqa: E402
from aion2cp.api import CLASS_KEYS, AionAPI  # noqa: E402
from aion2cp.paths import read_json, write_json  # noqa: E402

OUT = os.path.join(ROOT, "db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", default=os.environ.get("REGIONS", "eu"))
    ap.add_argument("--classes", default=",".join(CLASS_KEYS.values()))
    ap.add_argument("--level", type=int, default=45)
    a = ap.parse_args()
    api = AionAPI(cache_dir=os.path.join(ROOT, ".cache"), rate=6.0)  # débit prudent
    h = classdb.Harvester(api)
    # base partagée : bien plus large que la collecte locale (qui lit 160 joueurs)
    classdb.SAMPLE_TARGET = int(os.environ.get("SAMPLE_TARGET", "1000"))
    classdb.SEARCH_PAGES = 8
    classdb.TOP_N = classdb.TOP_N_MAX = classdb.TOP_ADD = int(os.environ.get("TOP_N", "60"))
    os.makedirs(OUT, exist_ok=True)
    for region in a.regions.split(","):
        for cls in a.classes.split(","):
            t = time.time()
            # on repart de la base publiée hier : seuls les joueurs de plus de 6 jours sont relus
            prev = os.path.join(OUT, f"{region}_{cls.lower()}.json")
            if os.path.exists(prev):
                write_json(classdb.db_path(region, cls), read_json(prev))
            try:
                db = h.run(region, cls, a.level)
            except Exception as e:  # une classe en échec n'arrête pas les autres
                print(f"{region} {cls} : échec ({e})", flush=True)
                continue
            db.pop("t_samples", None)
            write_json(os.path.join(OUT, f"{region}_{cls.lower()}.json"), db)
            print(f"{region} {cls} : {len(db['samples'])} joueurs, {len(db['top'])} builds, "
                  f"{time.time() - t:.0f} s, {api.calls} requêtes, {api.throttled} refus", flush=True)


if __name__ == "__main__":
    main()
