"""Recalcule la valeur CP de chaque stat à partir des bases de toutes les classes.
Usage : python tools/calibrate.py <dossier contenant eu_*.json> <fichier de sortie>"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aion2cp.statcal import fit  # noqa: E402


def main():
    src, out = sys.argv[1], sys.argv[2]
    dbs = [json.load(open(f, encoding="utf-8")) for f in glob.glob(os.path.join(src, "eu_*.json"))]
    res = fit(dbs)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=0)
    top = sorted(res["weights"].items(), key=lambda kv: -kv[1]["w"])[:12]
    print(f"{res['n']} builds, erreur moyenne {res['mae']} CP")
    for k, v in top:
        print(f"  {k:32s} {v['w']:8.2f}  [{v['lo']:.1f} – {v['hi']:.1f}]")


if __name__ == "__main__":
    main()
