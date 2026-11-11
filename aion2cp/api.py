"""Client des données publiques du site officiel AION 2 Global (aion2.plaync.com).

Mêmes appels que la page « Character Info » du site. Rien n'est lu dans le jeu.
L'API n'est pas documentée : si NCSOFT renomme un champ, c'est ici qu'on corrige.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from urllib.parse import unquote

import requests

SITE = "https://aion2.plaync.com"
SEARCH = "https://api-search.plaync.com/aion2global/search/v2/character"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

REGIONS = {"eu": "Europe", "nae": "Amérique Est", "naw": "Amérique Ouest",
           "la": "Amérique latine", "as": "Asie / Japon"}

# Clé de classe (pcdata.className) -> nom affiché par le site (profile.className)
CLASS_KEYS = {"GLADIATOR": "Gladiator", "TEMPLAR": "Templar", "RANGER": "Ranger",
              "ASSASSIN": "Assassin", "SORCERER": "Sorcerer", "ELEMENTALIST": "Spiritmaster",
              "CLERIC": "Cleric", "CHANTER": "Chanter"}


class ApiError(RuntimeError):
    pass


class RateLimiter:
    """Seau à jetons partagé : au plus `rate` requêtes par seconde, toutes connexions confondues."""

    def __init__(self, rate: float = 12.0):
        self.max_rate = rate
        self.rate = rate
        self.tokens = rate
        self.t = time.time()
        self.lock = threading.Lock()
        self.pause_until = 0.0

    def slow_down(self, pause: float = 4.0):
        """Le site sature (429) : on divise le débit et tout le monde fait une pause."""
        with self.lock:
            self.rate = max(1.5, self.rate * 0.5)
            self.tokens = 0
            self.pause_until = max(self.pause_until, time.time() + pause)

    def ok(self):
        with self.lock:
            self.rate = min(self.max_rate, self.rate + 0.05)

    def wait(self, yield_to=None):
        # les requêtes de la collecte (threads « harvest… ») laissent passer celles de ton perso
        if yield_to is not None and threading.current_thread().name.startswith("harvest"):
            while yield_to():
                time.sleep(0.15)
        p = self.pause_until - time.time()
        if p > 0:
            time.sleep(p)
        while True:
            with self.lock:
                now = time.time()
                self.tokens = min(self.rate, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                need = (1 - self.tokens) / self.rate
            time.sleep(need)


class AionAPI:
    def __init__(self, cache_dir: str | None = None, lang: str = "en-US", rate: float = 9.0,
                 timeout: float = 20.0):
        self.lang = lang  # noms de skills / stats / objets en anglais, comme dans le jeu
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.limiter = RateLimiter(rate)
        self.user_limiter = RateLimiter(16.0)  # ton perso : petite rafale plus rapide
        self._local = threading.local()
        self.calls = 0
        self.throttled = 0
        self.user_active = 0  # > 0 pendant le chargement de ton perso : la collecte attend
        self._ua_lock = threading.Lock()
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ http
    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": UA, "Accept": "application/json", "Referer": SITE + "/"})
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8)
            s.mount("https://", adapter)
            self._local.s = s
        return s

    def _cache_path(self, key: str) -> str | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha1(key.encode()).hexdigest()
        return os.path.join(self.cache_dir, h[:2], h + ".json")

    def _get(self, url: str, params: dict, ttl: float = 600) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        path = self._cache_path(key)
        if path and ttl > 0 and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        last = None
        for attempt in range(5):
            lim = self.user_limiter if threading.current_thread().name.startswith("user") else self.limiter
            lim.wait(lambda: self.user_active > 0)
            try:
                self.calls += 1
                r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last = ApiError(f"Connexion impossible au site officiel ({e.__class__.__name__}).")
                time.sleep(1.2 * (attempt + 1))
                continue
            if r.status_code == 429:
                self.throttled += 1
                last = ApiError("Le site officiel limite les requêtes (429) : réessaie dans une minute.")
                lim.slow_down(3.0 * (attempt + 1))
                continue
            if r.status_code >= 500:
                last = ApiError(f"Le site officiel a répondu {r.status_code}.")
                time.sleep(1.5 * (attempt + 1))
                continue
            lim.ok()
            if r.status_code != 200:
                raise ApiError(f"Le site officiel a répondu {r.status_code}.")
            try:
                data = r.json()
            except ValueError:
                raise ApiError("Réponse illisible du site officiel.")
            break
        else:
            raise last or ApiError("Échec de la requête.")
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = f"{path}.{threading.get_ident()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, path)
            except OSError:
                pass
        return data

    def _char(self, region: str, cid: str, sid: int) -> dict:
        return {"lang": self.lang, "region": region, "serverId": sid, "characterId": cid}

    def user_begin(self):
        with self._ua_lock:
            self.user_active += 1

    def user_end(self):
        with self._ua_lock:
            self.user_active = max(0, self.user_active - 1)

    # ------------------------------------------------------------- endpoints
    def servers(self, region: str) -> list[dict]:
        d = self._get(f"{SITE}/{self.lang.lower()}/api/gameinfo/servers",
                      {"region": region, "lang": self.lang}, ttl=7 * 86400)
        return d.get("serverList") or []

    def pcdata(self, region: str = "eu") -> list[dict]:
        d = self._get(f"{SITE}/{self.lang.lower()}/api/gameinfo/pcdata",
                      {"region": region, "lang": self.lang}, ttl=30 * 86400)
        return d.get("pcDataList") or []

    def class_pcids(self, region: str, class_text: str) -> list[int]:
        try:
            rows = self.pcdata(region)
        except ApiError:
            return []
        key = class_text.replace(" ", "").lower()
        return [r["id"] for r in rows if str(r.get("classText", "")).replace(" ", "").lower() == key
                or str(r.get("className", "")).lower() == key]

    def search(self, region: str, keyword: str, server_id: int | None = None, page: int = 1,
               size: int = 40, pcids: list[int] | None = None, sort: str | None = None,
               ttl: float = 3600) -> tuple[list[dict], int]:
        d = self._get(SEARCH, {"keyword": keyword, "page": page, "size": size, "region": region,
                               "localeInfo": self.lang, "serverId": server_id, "sort": sort,
                               "pcId": ",".join(map(str, pcids)) if pcids else None}, ttl=ttl)
        rows = d.get("list") or []
        for row in rows:
            row["characterId"] = unquote(row.get("characterId") or "")
            row["name"] = re.sub(r"<[^>]*>", "", str(row.get("name") or ""))
        end = (d.get("pagination") or {}).get("endPage") or 1
        return rows, int(end)

    def find_character(self, region: str, name: str, server_id: int | None = None) -> dict:
        name_l = name.strip().lower()
        if not name_l:
            raise ApiError("Tape ton pseudo.")
        matches = []
        for sid in ([server_id, None] if server_id else [None]):
            for page in range(1, 4):
                rows, end = self.search(region, name.strip(), server_id=sid, page=page, ttl=120)
                matches += [r for r in rows if r["name"].lower() == name_l]
                if page >= end:
                    break
            if matches:
                break
        if server_id:
            same = [r for r in matches if r.get("serverId") == server_id]
            if not same and matches:
                srv = ", ".join(sorted({str(r.get('serverName')) for r in matches}))
                raise ApiError(f"« {name} » existe, mais pas sur ce serveur (trouvé sur : {srv}).")
            matches = same
        if not matches:
            raise ApiError(f"Personnage « {name} » introuvable en {REGIONS.get(region, region)}.")
        matches.sort(key=lambda r: -(r.get("level") or 0))
        return matches[0]

    def info(self, region, cid, sid, ttl=600) -> dict:
        return self._get(f"{SITE}/api/character/info", self._char(region, cid, sid), ttl=ttl)

    def equipment(self, region, cid, sid, ttl=600) -> dict:
        return self._get(f"{SITE}/api/character/equipment", self._char(region, cid, sid), ttl=ttl)

    def item(self, region, cid, sid, item_id, slot_pos, enchant=0, exceed=0, ttl=600) -> dict:
        p = self._char(region, cid, sid)
        p.update({"id": item_id, "slotPos": slot_pos, "enchantLevel": enchant, "exceedLevel": exceed})
        return self._get(f"{SITE}/api/character/equipment/item", p, ttl=ttl)

    def daevanion(self, region, cid, sid, board_id, ttl=600) -> dict:
        p = self._char(region, cid, sid)
        p["boardId"] = board_id
        return self._get(f"{SITE}/api/character/daevanion/detail", p, ttl=ttl)

    # --------------------------------------------------------------- helpers
    def load_full(self, region, cid, sid, ttl=600, items=True, boards=True, pool=None, workers=12,
                  only_random=False, hint: dict | None = None) -> dict:
        """info + équipement + détail de chaque objet (+ plateaux Daevanion), en parallèle.
        only_random : saute ceinture, amulette et runes (pas de lignes à roll ni de pierres).
        hint : dernier build connu ; ses objets et plateaux sont demandés EN MÊME TEMPS que la fiche
        (une seule vague de requêtes au lieu de deux). Seuls les objets changés sont redemandés."""
        from concurrent.futures import ThreadPoolExecutor
        own = pool is None
        ex = pool or ThreadPoolExecutor(workers, thread_name_prefix="user")

        def eq_list(eq):
            return ((eq or {}).get("equipment") or {}).get("equipmentList") or []

        def key(e):
            return (e.get("slotPos"), e.get("id"), e.get("enchantLevel") or 0, e.get("exceedLevel") or 0)

        def item_job(e):
            return ex.submit(self.item, region, cid, sid, e["id"], e["slotPos"], e.get("enchantLevel") or 0,
                             e.get("exceedLevel") or 0, ttl)
        try:
            f_info = ex.submit(self.info, region, cid, sid, ttl)
            f_eq = ex.submit(self.equipment, region, cid, sid, ttl)
            early_items, early_boards = {}, {}
            if hint:
                if items:
                    early_items = {key(e): item_job(e) for e in eq_list(hint.get("equipment"))}
                if boards:
                    early_boards = {str(b["id"]): ex.submit(self.daevanion, region, cid, sid, b["id"], ttl)
                                    for b in ((hint.get("info") or {}).get("daevanion") or {}).get("boardList") or []
                                    if b.get("open")}
            out = {"info": f_info.result(), "equipment": f_eq.result(), "items": {}, "daevanion": {}}
            jobs = []
            if items:
                for e in eq_list(out["equipment"]):
                    if only_random and e.get("slotPos") in (17, 22, 23, 24):
                        continue
                    jobs.append(("items", str(e["slotPos"]), early_items.get(key(e)) or item_job(e)))
            if boards:
                for b in ((out["info"].get("daevanion") or {}).get("boardList") or []):
                    if b.get("open"):
                        jobs.append(("daevanion", str(b["id"]), early_boards.get(str(b["id"])) or
                                     ex.submit(self.daevanion, region, cid, sid, b["id"], ttl)))
            for kind, key, fut in jobs:
                try:
                    out[kind][key] = fut.result()
                except ApiError:
                    pass
            return out
        finally:
            if own:
                ex.shutdown(wait=False)
