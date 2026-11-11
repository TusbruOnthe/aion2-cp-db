"""Transforme les réponses brutes du site en un build propre, prêt pour l'analyse et l'écran."""
from __future__ import annotations

import re

SLOT_FR = {
    "MainHand": "Arme", "SubHand": "Main gauche", "Helmet": "Casque", "Shoulder": "Épaules",
    "Torso": "Torse", "Pants": "Jambières", "Gloves": "Gants", "Boots": "Bottes", "Cape": "Cape",
    "Belt": "Ceinture", "Necklace": "Collier", "Earring1": "Boucle d'oreille 1",
    "Earring2": "Boucle d'oreille 2", "Ring1": "Anneau 1", "Ring2": "Anneau 2",
    "Bracelet1": "Bracelet 1", "Bracelet2": "Bracelet 2", "Amulet": "Amulette",
    "Rune1": "Rune 1", "Rune2": "Rune 2",
    "Arcana1": "Arcana 1", "Arcana2": "Arcana 2", "Arcana3": "Arcana 3", "Arcana4": "Arcana 4",
    "Arcana5": "Arcana 5",
}
SLOT_SHORT = {"MainHand": "Arme", "SubHand": "Main G.", "Helmet": "Casque", "Shoulder": "Épaules",
              "Torso": "Torse", "Pants": "Jambes", "Gloves": "Gants", "Boots": "Bottes", "Cape": "Cape",
              "Belt": "Ceinture", "Necklace": "Collier", "Earring1": "Boucle 1", "Earring2": "Boucle 2",
              "Ring1": "Anneau 1", "Ring2": "Anneau 2", "Bracelet1": "Bracelet 1", "Bracelet2": "Bracelet 2",
              "Amulet": "Amulette", "Rune1": "Rune 1", "Rune2": "Rune 2"}
GROUP = {"Earring1": "Earring", "Earring2": "Earring", "Ring1": "Ring", "Ring2": "Ring",
         "Bracelet1": "Bracelet", "Bracelet2": "Bracelet", "Rune1": "Rune", "Rune2": "Rune"}
GRADE_FR = {"Common": "Commun", "Rare": "Rare", "Legend": "Légendaire", "Unique": "Unique",
            "Special": "Spécial", "Epic": "Épique", "Mythic": "Mythique"}
ARCANA_POS = {41, 42, 43, 44, 45}
WEAPON_POS = {1, 2}
ARMOR_POS = {3, 4, 5, 6, 7, 8, 19}
ACC_POS = {10, 11, 12, 13, 14, 15, 16}
ACTIVE_TIERS = (8, 12, 16, 20)


def slot_group(slot: str) -> str:
    return GROUP.get(slot, slot)


def norm(name) -> str:
    n = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    return {"movement speed": "move speed", "crit": "critical hit"}.get(n, n)


def parse_value(v) -> tuple[float | None, str]:
    s = str(v if v is not None else "").strip()
    m = re.search(r"-?\d+(?:[.,]\d+)?", s)
    if not m:
        return None, ""
    return float(m.group(0).replace(",", ".")), ("%" if "%" in s else "")


def _int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _piece(e: dict, d: dict) -> dict:
    sp = _int(e.get("slotPos"))
    slot = e.get("slotPosName") or str(sp)
    stats = []
    for s in (d.get("subStats") or []):
        v, u = parse_value(s.get("value"))
        stats.append({"name": s.get("name") or "?", "text": str(s.get("value") or ""), "v": v, "unit": u,
                      "fixed": s.get("minValue") is not None and not d.get("subStatRandom")})
    skills = [{"name": x.get("name") or "?", "level": _int(x.get("level"), 1), "icon": x.get("icon") or "",
               "id": x.get("id")} for x in (d.get("subSkills") or [])]
    stones = [{"name": m.get("name") or "?", "text": str(m.get("value") or ""), "v": parse_value(m.get("value"))[0],
               "grade": m.get("grade") or "", "icon": m.get("icon") or "", "pos": m.get("slotPos")}
              for m in (d.get("magicStoneStat") or [])]
    cap = _int(d.get("subStatCount"))
    random_lines = bool(d.get("subStatRandom"))
    rollable = [s for s in stats if not s["fixed"]] if random_lines else []
    lines_now = len(rollable) + len(skills)
    if random_lines and lines_now > cap:
        cap = lines_now
    return {
        "pos": sp, "slot": slot, "slotFr": SLOT_FR.get(slot, slot), "short": SLOT_SHORT.get(slot, SLOT_FR.get(slot, slot)),
        "group": slot_group(slot), "name": e.get("name") or "?", "grade": e.get("grade") or "",
        "gradeFr": GRADE_FR.get(e.get("grade"), e.get("grade") or ""), "icon": e.get("icon") or d.get("icon") or "",
        "id": _int(e.get("id")), "enchant": _int(e.get("enchantLevel")), "exceed": _int(e.get("exceedLevel")),
        "maxEnchant": _int(d.get("maxEnchantLevel"), 15 if sp not in ARCANA_POS else 5),
        "maxExceed": _int(d.get("maxExceedEnchantLevel")), "itemLevel": _int(d.get("level")),
        "category": d.get("categoryName") or "", "detail": bool(d),
        "lineCap": cap if random_lines else 0, "skillMax": _int(d.get("subSkillCountMax")) if random_lines else 0,
        "stats": rollable if random_lines else [], "fixedStats": [s for s in stats if s not in rollable],
        "skills": skills, "stones": stones, "stoneSlots": _int(d.get("magicStoneSlotCount")),
        "god": [{"name": g.get("name"), "icon": g.get("icon"), "grade": g.get("grade")} for g in (d.get("godStoneStat") or [])],
        "mainStats": [{"name": m.get("name"), "text": m.get("value"), "extra": m.get("extra")} for m in (d.get("mainStats") or [])],
    }


def make_build(raw: dict, region: str = "") -> dict:
    info, eq = raw.get("info") or {}, raw.get("equipment") or {}
    items = raw.get("items") or {}
    prof = info.get("profile") or {}
    stat_list = (info.get("stat") or {}).get("statList") or []
    ilvl = next((_int(s.get("value")) for s in stat_list if s.get("type") == "ItemLevel"), 0)
    gear, arcana = [], []
    for e in ((eq.get("equipment") or {}).get("equipmentList") or []):
        p = _piece(e, items.get(str(e.get("slotPos"))) or {})
        (arcana if p["pos"] in ARCANA_POS else gear).append(p)
    gear.sort(key=lambda g: g["pos"])
    arcana.sort(key=lambda g: g["pos"])
    sk = (eq.get("skill") or {}).get("skillList") or []

    def skl(s):
        return {"name": s.get("name") or "?", "level": _int(s.get("skillLevel")), "icon": s.get("icon") or "",
                "equip": bool(s.get("equip")), "acquired": bool(s.get("acquired", 1)), "id": s.get("id"),
                "need": _int(s.get("needLevel"))}
    active = [skl(s) for s in sk if s.get("category") == "Active"]
    passive = [skl(s) for s in sk if s.get("category") == "Passive"]
    stigma = [skl(s) for s in sk if s.get("category") == "Dp"]
    pw = eq.get("petwing") or {}
    title = info.get("title") or {}
    boards = []
    for b in ((info.get("daevanion") or {}).get("boardList") or []):
        boards.append({"id": b.get("id"), "name": b.get("name"), "icon": b.get("icon"), "open": bool(b.get("open")),
                       "openNodes": _int(b.get("openNodeCount")), "totalNodes": _int(b.get("totalNodeCount")),
                       "pct": _int(b.get("openPercent"))})
    stats = []
    for s in stat_list:
        if s.get("type") == "ItemLevel":
            continue
        stats.append({"name": s.get("name"), "type": s.get("type"), "value": s.get("value"),
                      "second": s.get("statSecondList") or []})
    b = {
        "name": prof.get("characterName") or "?", "cid": prof.get("characterId") or "",
        "className": prof.get("className") or "?", "pcId": _int(prof.get("pcId")),
        "server": prof.get("serverName") or "?", "serverId": _int(prof.get("serverId")), "region": region,
        "level": _int(prof.get("characterLevel")), "cp": _int(prof.get("combatPower")), "ilvl": ilvl,
        "race": prof.get("raceName") or "", "portrait": prof.get("profileImage") or "",
        "title": prof.get("titleName") or "", "gear": gear, "arcana": arcana,
        "active": active, "passive": passive, "stigma": stigma,
        "pet": {"name": (pw.get("pet") or {}).get("name"), "level": _int((pw.get("pet") or {}).get("level")),
                "icon": (pw.get("pet") or {}).get("icon")},
        "wing": {"name": (pw.get("wing") or {}).get("name"), "enchant": _int((pw.get("wing") or {}).get("enchantLevel")),
                 "icon": (pw.get("wing") or {}).get("icon"), "grade": (pw.get("wing") or {}).get("grade")},
        "titles": {"owned": _int(title.get("ownedCount")), "total": _int(title.get("totalCount"))},
        "boards": boards, "stats": stats,
    }
    b["features"] = features(b)
    return b


def features(b: dict) -> dict:
    """Leviers chiffrés du build, utilisés pour comparer aux autres joueurs et mesurer le CP."""
    gear = b["gear"]
    f = {
        "ilvl": b["ilvl"],
        "ench_weapon": sum(g["enchant"] for g in gear if g["pos"] in WEAPON_POS),
        "ench_armor": sum(g["enchant"] for g in gear if g["pos"] in ARMOR_POS),
        "ench_acc": sum(g["enchant"] for g in gear if g["pos"] in ACC_POS),
        "ench_other": sum(g["enchant"] for g in gear if g["pos"] not in WEAPON_POS | ARMOR_POS | ACC_POS),
        "exceed": sum(g["exceed"] for g in gear),
        "arcana_lvl": sum(a["enchant"] for a in b["arcana"]),
        "arcana_unique": sum(1 for a in b["arcana"] if a["grade"] == "Unique"),
        "arcana_n": len(b["arcana"]),
        "dae_nodes": sum(x["openNodes"] for x in b["boards"]),
        "active_lvl": sum(s["level"] for s in b["active"] if s["acquired"]),
        "passive_lvl": sum(s["level"] for s in b["passive"] if s["acquired"]),
        "stigma_lvl": sum(s["level"] for s in b["stigma"] if s["equip"]),
        "wing_ench": b["wing"]["enchant"], "pet_lvl": b["pet"]["level"], "titles": b["titles"]["owned"],
    }
    f["ench_total"] = f["ench_weapon"] + f["ench_armor"] + f["ench_acc"] + f["ench_other"]
    return {k: float(v) for k, v in f.items()}


def light_sample(info: dict, eq: dict) -> dict:
    """Résumé d'un joueur de la base (sans le détail des objets)."""
    b = make_build({"info": info, "equipment": eq})
    return {"name": b["name"], "server": b["server"], "cp": b["cp"], "level": b["level"], "ilvl": b["ilvl"],
            "f": b["features"],
            "skills": {s["name"]: s["level"] for s in b["active"] + b["passive"] if s["acquired"]},
            "stigmas": {s["name"]: s["level"] for s in b["stigma"] if s["equip"]},
            "ench": {g["slot"]: [g["enchant"], g["exceed"]] for g in b["gear"]}}
