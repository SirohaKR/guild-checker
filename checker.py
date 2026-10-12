# 이중길드 검사의 핵심 — 넥슨 API를 어떤 순서로 부르고 결과를 어떻게 묶는지.
#
# 원리: 유니온 랭킹(/ranking/union)에 아무 캐릭터 ocid를 넣으면 그 계정의 '유니온 대표 캐릭터'가 나온다.
#       같은 계정(같은 월드)의 캐릭터는 대표 캐릭터 이름이 같으므로, 대표 이름으로 묶으면 계정별로 모인다.
#  - 정방향: 우리 길드원의 대표 캐릭터가 다른 길드에 있으면 → "본캐가 다른 길드"
#  - 역방향: 다른 길드(길드 랭킹 상위 N개)의 길드원 대표가 우리 길드원 대표와 같으면 → "부캐가 다른 길드"
# 같은 캐릭터를 두 번 조회하지 않도록 한 번 검사 안에서는 메모(dict)에, 검사 사이에는 DB 캐시에 담아 둔다.
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from db import KST
from nexon import NexonError

WORKERS = int(os.environ.get("WORKERS", "8"))  # 동시에 몇 개씩 부를지
GUILDS_PER_PAGE = 200  # 길드 랭킹 한 페이지에 나오는 길드 수

WORLDS = ["스카니아", "베라", "루나", "제니스", "크로아", "유니온", "엘리시움", "이노시스",
          "레드", "오로라", "아케인", "노바", "에오스", "헬리오스"]
# 넥슨 명세에는 '핼리오스'로 적혀 있어서 둘 다 시도한다
WORLD_ALIASES = {"헬리오스": ["헬리오스", "핼리오스"]}


class ScanError(Exception):
    pass


class Cancelled(Exception):
    pass


def data_date(at=None):
    """조회할 날짜: 어제(한국 시간). 어제 데이터는 오늘 새벽 2시부터 나오니 2시 전이면 그제."""
    at = at or datetime.now(KST)
    return (at.date() - timedelta(days=2 if at.hour < 2 else 1)).isoformat()


class Job:
    """백그라운드 검사 하나의 진행 상황 — 화면이 1초마다 물어본다."""

    def __init__(self, job_id, world, guild):
        self.id, self.world, self.guild = job_id, world, guild
        self.status = "running"  # running | done | error | cancelled
        self.step, self.steps, self.phase = 0, 1, "준비 중"
        self.done = self.total = 0
        self.error = None
        self.scan_id = None
        self.cancel_flag = False
        self.lock = threading.Lock()

    def start_phase(self, phase, total=0):
        with self.lock:
            self.step += 1
            self.phase, self.done, self.total = phase, 0, total

    def tick(self):
        with self.lock:
            self.done += 1

    def check(self):
        if self.cancel_flag:
            raise Cancelled()

    def to_dict(self):
        with self.lock:
            return {"id": self.id, "status": self.status, "world": self.world, "guild": self.guild,
                    "step": self.step, "steps": self.steps, "phase": self.phase,
                    "done": self.done, "total": self.total, "error": self.error, "scan_id": self.scan_id}


class Scanner:
    def __init__(self, client, store, job, world, guild, reverse_top=0, check_activity=False):
        self.c, self.db, self.job = client, store, job
        self.world, self.guild_input = world, guild.strip()
        self.reverse_top = reverse_top          # 0 = 역방향 끔, -1 = 전체
        self.check_activity = check_activity
        self.date = data_date()
        self.date30 = (datetime.fromisoformat(self.date) - timedelta(days=30)).date().isoformat()
        self.cache_hits = 0
        # 한 번 검사 안에서의 메모 — 같은 이름/ocid는 한 번만 처리
        self._memo = {}
        self._memo_lock = threading.Lock()

    # ── 공통 도구 ──

    def _once(self, key, fn):
        """같은 key는 한 번만 계산 (여러 스레드가 동시에 와도)."""
        with self._memo_lock:
            ev = self._memo.get(key)
            if ev is None:
                ev = self._memo[key] = {"event": threading.Event()}
                owner = True
            else:
                owner = False
        if owner:
            try:
                ev["value"] = fn()
            except Exception as e:  # noqa: BLE001 — 기다리던 다른 스레드에도 같은 오류를 전한다
                ev["error"] = e
            ev["event"].set()
        else:
            ev["event"].wait()
        if "error" in ev:
            raise ev["error"]
        return ev["value"]

    def _pmap(self, fn, items):
        """items를 WORKERS개씩 동시에 처리하고 진행률을 올린다. 결과 순서는 items 순서."""
        out = [None] * len(items)
        with ThreadPoolExecutor(WORKERS) as ex:
            futs = {ex.submit(self._guarded, fn, it): i for i, it in enumerate(items)}
            try:
                for f in as_completed(futs):
                    out[futs[f]] = f.result()
                    self.job.tick()
            except BaseException:
                self.job.cancel_flag = True  # 남은 작업도 멈추게
                for f in futs:
                    f.cancel()
                raise
        return out

    def _guarded(self, fn, item):
        self.job.check()
        return fn(item)

    def _cached(self, getter, putter, fetch):
        hit = getter()
        if hit is not None:
            self.cache_hits += 1
            return hit[1]
        val = fetch()
        putter(val)
        return val

    # ── 캐릭터 단위 조회 (캐시 → API) ──

    def ocid(self, name):
        return self._once(("ocid", name), lambda: self._cached(
            lambda: self.db.get_ocid(name), lambda v: self.db.put_ocid(name, v), lambda: self.c.ocid(name)))

    def _with_ocid(self, name, fn):
        """ocid로 부르다가 400이 나면 ocid가 바뀐 것일 수 있어서 한 번 새로 받아 다시 시도."""
        oc = self.ocid(name)
        if not oc:
            return None, None
        try:
            return oc, fn(oc)
        except NexonError as e:
            if e.status != 400:
                raise
            self.db.drop_ocid(name)
            new = self.c.ocid(name)
            self.db.put_ocid(name, new)
            if not new or new == oc:
                raise
            return new, fn(new)

    def rep_of(self, name):
        """캐릭터 이름 → {'ocid', 'rep'(대표 이름 또는 None), 'reason'}"""
        def work():
            def fetch_rep(oc):
                def api():
                    row = self.c.union_rep(oc, self.world_api, self.date)
                    if not row or not row.get("character_name"):
                        return None
                    return {"name": row["character_name"], "union_level": row.get("union_level")}
                return self._once(("rep", oc), lambda: self._cached(
                    lambda: self.db.get_rep(oc, self.world_api), lambda v: self.db.put_rep(oc, self.world_api, v), api))

            oc, rep = self._with_ocid(name, fetch_rep)
            if not oc:
                return {"ocid": None, "rep": None, "reason": "캐릭터를 찾을 수 없음 (개명·삭제?)"}
            if not rep:
                return {"ocid": oc, "rep": None, "reason": "유니온 랭킹에 없는 계정"}
            return {"ocid": oc, "rep": rep["name"], "union_level": rep.get("union_level"), "reason": None}
        return self._once(("repof", name), work)

    def info(self, name, date=None):
        """캐릭터 기본 정보(길드·레벨·직업·이미지). 못 찾으면 None."""
        date = date or self.date

        def work():
            def fetch(oc):
                def api():
                    try:
                        return slim(self.c.character_basic(oc, date))
                    except NexonError as e:
                        if e.status == 400 and date != self.date:
                            return None  # 그날엔 없던 캐릭터 (30일 전 비교용)
                        raise
                return self._cached(lambda: self.db.get_char(oc, date), lambda v: self.db.put_char(oc, date, v), api)
            return self._with_ocid(name, fetch)[1]
        return self._once(("info", name, date), work)

    # ── 길드 단위 ──

    def find_guild(self, name, worlds):
        for w in worlds:
            gid = self._cached(lambda: self.db.get_guild(w, name), lambda v: self.db.put_guild(w, name, v),
                               lambda: self.c.guild_id(name, w))
            if gid:
                return w, gid
        return None, None

    def members_of(self, oguild_id):
        got = self.db.get_members(oguild_id, self.date)
        if got is not None:
            self.cache_hits += 1
            return got
        b = self.c.guild_basic(oguild_id, self.date)
        got = {"guild_name": b.get("guild_name"), "master": b.get("guild_master_name"),
               "level": b.get("guild_level"), "members": b.get("guild_member") or [],
               "user_count": b.get("guild_user_count")}
        self.db.put_members(oguild_id, self.date, got)
        return got

    # ── 전체 흐름 ──

    def run(self):
        started = time.time()
        job = self.job
        job.steps = 3 + (1 if self.check_activity else 0) + (2 if self.reverse_top else 0)

        # 1) 우리 길드
        job.start_phase("길드 정보 확인", 1)
        self.world_api, gid = self.find_guild(self.guild_input, WORLD_ALIASES.get(self.world, [self.world]))
        if not gid:
            raise ScanError(f"'{self.world}' 월드에서 '{self.guild_input}' 길드를 찾지 못했어요. 이름과 월드를 확인해 주세요.")
        ours = self.members_of(gid)
        guild = ours["guild_name"] or self.guild_input
        names = list(dict.fromkeys(ours["members"]))  # 중복 제거 (순서 유지)
        if not names:
            raise ScanError(f"{self.date} 기준 길드원 목록이 비어 있어요.")
        job.tick()

        # 2) 길드원마다 대표 캐릭터
        job.start_phase("길드원 대표 캐릭터 조회", len(names))
        reps = dict(zip(names, self._pmap(self.rep_of, names)))

        # 3) 캐릭터 정보: 우리 길드원 + 길드 밖에 있는 대표 캐릭터 (각각 한 번씩만)
        rep_names = sorted({r["rep"] for r in reps.values() if r["rep"]})
        need = [n for n in names if reps[n]["ocid"]] + [r for r in rep_names if r not in reps]
        job.start_phase("캐릭터 정보 조회", len(need))
        infos = dict(zip(need, self._pmap(self.info, need)))

        # 4) (선택) 30일 성장 확인 — 대표 캐릭터만, 30일 전 기본 정보와 레벨·경험치 비교
        stale = {}
        if self.check_activity:
            job.start_phase("30일 성장 확인", len(rep_names))
            old = self._pmap(lambda n: self.info(n, self.date30), rep_names)
            for n, o in zip(rep_names, old):
                cur = infos.get(n)
                stale[n] = None if not (cur and o) else (
                    cur.get("level") == o.get("level") and cur.get("exp") == o.get("exp"))

        # 5) (선택) 역방향: 다른 길드원의 대표가 우리 대표와 같은지
        others = {}  # 대표 이름 → [다른 길드 캐릭터 …]
        other_stats = {"guilds": 0, "members": 0, "unknown": 0}
        if self.reverse_top:
            others, other_stats = self.reverse(guild, set(rep_names))

        result = build_result(guild, names, reps, infos, stale, others, self.check_activity, bool(self.reverse_top))
        result["guild"] = {"name": guild, "world": self.world, "master": ours["master"], "level": ours["level"],
                           "user_count": ours["user_count"], "date": self.date}
        s = result["summary"]
        s.update({"other_guilds": other_stats["guilds"], "other_members": other_stats["members"],
                  "other_unknown": other_stats["unknown"], "api_calls": self.c.calls,
                  "cache_hits": self.cache_hits, "seconds": round(time.time() - started, 1)})
        return result

    def reverse(self, our_guild, our_reps):
        job = self.job
        # 길드 랭킹에서 다른 길드 이름 모으기
        job.start_phase("다른 길드 목록 모으기", 0)
        guild_names, page = [], 1
        limit = self.reverse_top if self.reverse_top > 0 else None
        while limit is None or len(guild_names) < limit + 1:
            job.check()
            rows = self.c.guild_ranking(self.world_api, self.date, page)
            if not rows:
                break
            guild_names += [r["guild_name"] for r in rows if r.get("guild_name")]
            job.total = len(guild_names)
            job.done = len(guild_names)
            if len(rows) < GUILDS_PER_PAGE:
                break
            page += 1
        guild_names = [g for g in dict.fromkeys(guild_names) if g != our_guild]
        if limit:
            guild_names = guild_names[:limit]

        def load(gname):
            _, gid = self.find_guild(gname, [self.world_api])
            return (gname, self.members_of(gid)["members"]) if gid else (gname, [])

        job.start_phase("다른 길드 길드원 목록", len(guild_names))
        lists = self._pmap(load, guild_names)
        pairs = [(m, g) for g, ms in lists for m in ms]

        job.start_phase("다른 길드원 대표 캐릭터 조회", len(pairs))
        found = self._pmap(lambda p: self.rep_of(p[0]), pairs)
        hits = [(p, r) for p, r in zip(pairs, found) if r["rep"] in our_reps and p[0] != r["rep"]]
        out = {}
        for (name, gname), r in hits:
            ci = self.info(name) or {}
            out.setdefault(r["rep"], []).append({**ci, "name": name, "guild": gname})
        return out, {"guilds": len(guild_names), "members": len(pairs),
                     "unknown": sum(1 for r in found if not r["rep"])}


LOOKUP_MAX = 30  # 이름 조회 한 번에 최대 몇 명


def lookup(client, store, world, names):
    """이름(여러 개)으로 그 사람 조회: 캐릭터의 길드 + 계정의 유니온 대표 캐릭터와 그 길드.
    부캐 목록 전체는 넥슨 API로 알 수 없어서, 넣은 이름끼리 대표가 같으면 같은 계정으로 묶어 보여준다."""
    job = Job("lookup", world, "")
    out = []
    for name in names[:LOOKUP_MAX]:
        sc = Scanner(client, store, job, world, "")
        info = sc.info(name)
        if not info:
            out.append({"name": name, "found": False, "reason": "캐릭터를 찾을 수 없음 (이름·개명 확인)"})
            continue
        # 유니온은 월드별이라 그 캐릭터의 월드로 조회 (헬리오스/핼리오스 둘 다 시도)
        worlds = WORLD_ALIASES.get(world, [world])
        rep = None
        for w in worlds:
            sc.world_api = w
            sc._memo.pop(("repof", name), None)
            rep = sc.rep_of(name)
            if rep["rep"]:
                break
        rep_info = sc.info(rep["rep"]) if rep["rep"] else None
        guild, rep_guild = info.get("guild") or "", (rep_info or {}).get("guild")
        out.append({
            "name": name, "found": True, "char": info | {"name": info.get("name") or name},
            "rep": (rep_info or {}) | {"name": rep["rep"]} if rep["rep"] else None,
            "union_level": rep.get("union_level"),
            "is_rep": rep["rep"] == (info.get("name") or name),
            "reason": rep["reason"],
            # 이 캐릭터와 대표 캐릭터가 둘 다 길드에 있는데 서로 다르면 이중길드
            "dual": bool(rep["rep"]) and bool(guild) and bool(rep_guild) and guild != rep_guild,
        })
    # 넣은 이름들 중 대표가 같은 것끼리 같은 계정
    by_rep = {}
    for r in out:
        if r.get("rep"):
            by_rep.setdefault(r["rep"]["name"], []).append(r["name"])
    for r in out:
        if r.get("rep"):
            r["same_account"] = [n for n in by_rep[r["rep"]["name"]] if n != r["name"]]
    return {"date": data_date(), "world": world, "results": out}


def slim(d):
    """character/basic 응답에서 필요한 것만."""
    if not d:
        return None
    return {"name": d.get("character_name"), "guild": d.get("character_guild_name") or "",
            "level": d.get("character_level"), "job": d.get("character_class"),
            "exp": d.get("character_exp"), "image": d.get("character_image"),
            "access": d.get("access_flag")}


def build_result(guild, names, reps, infos, stale, others, activity, reverse):
    def card(n):
        i = infos.get(n) or {}
        return {"name": n, "level": i.get("level"), "job": i.get("job"), "image": i.get("image"),
                "guild": i.get("guild"), "away7": i.get("access") == "false"}

    accounts = {}
    unknown = []
    for n in names:
        r = reps[n]
        if not r["rep"]:
            unknown.append({**card(n), "reason": r["reason"]})
            continue
        a = accounts.get(r["rep"])
        if a is None:
            rc = card(r["rep"])
            if not infos.get(r["rep"]):
                rc["guild"] = None  # 대표 캐릭터 정보 못 받음
            a = accounts[r["rep"]] = {"rep": rc, "union_level": r.get("union_level"), "ours": [], "others": []}
        a["ours"].append(card(n))

    out = []
    for rep, a in accounts.items():
        rg = a["rep"]["guild"]
        a["rep_in_ours"] = rg == guild
        a["forward"] = bool(rg) and rg != guild         # 본캐(대표)가 다른 길드
        a["rep_guildless"] = rg == ""                    # 대표가 길드 없음
        a["others"] = sorted(others.get(rep, []), key=lambda c: -(c.get("level") or 0))
        a["reverse"] = bool(a["others"])                 # 부캐가 다른 길드
        a["dual"] = a["forward"] or a["reverse"]
        a["stale30"] = stale.get(rep) if activity else None
        a["ours"].sort(key=lambda c: (c["name"] != rep, -(c.get("level") or 0)))
        out.append(a)
    out.sort(key=lambda a: (not a["dual"], -len(a["ours"]), -(a["rep"].get("level") or 0)))

    return {
        "accounts": out,
        "unknown": unknown,
        "summary": {
            "characters": len(names),
            "accounts": len(out),
            "unknown": len(unknown),
            "dual": sum(a["dual"] for a in out),
            "forward": sum(a["forward"] for a in out),
            "reverse": sum(a["reverse"] for a in out),
            "multi": sum(len(a["ours"]) > 1 for a in out),
            "stale30": sum(1 for a in out if a["stale30"]),
            "reverse_on": reverse, "activity_on": activity,
        },
    }


def compare(cur, prev):
    """지난 검사와 비교: 새로/더 이상 이중길드인 계정, 새로 들어온/나간 캐릭터."""
    if not prev:
        return None
    pd = {a["rep"]["name"] for a in prev["result"].get("accounts", []) if a.get("dual")}
    cd = {a["rep"]["name"] for a in cur.get("accounts", []) if a.get("dual")}

    def chars(res):
        s = {c["name"] for a in res.get("accounts", []) for c in a["ours"]}
        return s | {u["name"] for u in res.get("unknown", [])}

    pc, cc = chars(prev["result"]), chars(cur)
    return {"prev_id": prev["id"], "prev_date": prev["data_date"], "prev_at": prev["created_at"],
            "new_dual": sorted(cd - pd), "gone_dual": sorted(pd - cd),
            "joined": sorted(cc - pc), "left": sorted(pc - cc)}
