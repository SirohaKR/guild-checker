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
        self.mode = "ours"  # ours = 우리 길드원 검색 / other = 길드 검색
        self.owner = 0      # 검색한 계정 (혼자 쓰기 = 0)
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
                    "done": self.done, "total": self.total, "error": self.error, "scan_id": self.scan_id,
                    "mode": self.mode}


class Scanner:
    """검사 한 번. fresh=True면 캐릭터·길드 정보는 늘 '최신'(날짜 없이)으로 다시 받는다 — 검사 전 정보 갱신.
    유니온 대표(랭킹)는 하루 단위 데이터라 어제 기준 + 캐시."""

    def __init__(self, client, store, job, world, guild, reverse_top=0, check_activity=False, fresh=True, deep=True):
        self.c, self.db, self.job = client, store, job
        self.world, self.guild_input = world, guild.strip()
        self.reverse_top = reverse_top          # 0 = 역방향 끔, -1 = 전체
        self.check_activity = check_activity
        self.fresh = fresh                      # 최신 정보로 갱신해서 볼지
        self.deep = deep                        # 의심 길드의 길드원까지 확인할지
        self.world_api = world
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

    def rep_of(self, name, world=None):
        """캐릭터 이름 → {'ocid', 'rep'(대표 이름 또는 None), 'reason'}. world를 안 주면 검사 중인 길드의 월드."""
        w = world or self.world_api

        def work():
            def fetch_rep(oc):
                def api():
                    row = self.c.union_rep(oc, w, self.date)
                    if not row or not row.get("character_name"):
                        return None
                    return {"name": row["character_name"], "union_level": row.get("union_level")}
                return self._once(("rep", oc, w), lambda: self._cached(
                    lambda: self.db.get_rep(oc, w), lambda v: self.db.put_rep(oc, w, v), api))

            oc, rep = self._with_ocid(name, fetch_rep)
            if not oc:
                return {"ocid": None, "rep": None, "reason": "캐릭터를 찾을 수 없음 (개명·삭제?)"}
            if not rep:
                return {"ocid": oc, "rep": None, "reason": "유니온 랭킹에 없는 계정"}
            return {"ocid": oc, "rep": rep["name"], "union_level": rep.get("union_level"), "reason": None}
        return self._once(("repof", name, w), work)

    def info(self, name, date=None):
        """캐릭터 기본 정보(길드·레벨·직업·이미지·월드). 못 찾으면 None.
        date 없이 부르면 fresh일 때 최신 정보(갱신), 아니면 어제 기준. 'fresh' 값으로 최신인지 알려 준다."""
        if date is None and self.fresh:
            return self._once(("latest", name), lambda: self._latest(name))
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
            got = self._with_ocid(name, fetch)[1]
            return {**got, "fresh": False} if got else None
        return self._once(("info", name, date), work)

    def _latest(self, name):
        """최신 정보 (캐시 안 함 — 늘 새로). 실패하면 어제 기준 정보라도."""
        try:
            got = self._with_ocid(name, lambda oc: slim(self.c.character_basic(oc)))[1]
        except NexonError as e:
            if e.status != 400:
                raise
            got = None
        if got:
            return {**got, "fresh": True}
        return self.info(name, self.date)

    # ── 길드 단위 ──

    def find_guild(self, name, worlds):
        for w in worlds:
            gid = self._cached(lambda: self.db.get_guild(w, name), lambda v: self.db.put_guild(w, name, v),
                               lambda: self.c.guild_id(name, w))
            if gid:
                return w, gid
        return None, None

    def members_of(self, oguild_id):
        """길드 정보 + 길드원 목록. fresh면 최신(한 검사 안에서만 기억), 아니면 어제 기준 + DB 캐시."""
        def api(date):
            b = self.c.guild_basic(oguild_id, date)
            return {"guild_name": b.get("guild_name"), "master": b.get("guild_master_name"),
                    "level": b.get("guild_level"), "members": b.get("guild_member") or [],
                    "user_count": b.get("guild_user_count")}
        if self.fresh:
            return self._once(("members", oguild_id), lambda: api(None))
        got = self.db.get_members(oguild_id, self.date)
        if got is not None:
            self.cache_hits += 1
            return got
        got = api(self.date)
        self.db.put_members(oguild_id, self.date, got)
        return got

    def guild_list(self, gname, world=None):
        """길드 이름 → 길드원 이름 목록. 길드를 못 찾거나 오류면 None."""
        w = world or self.world_api
        try:
            _, gid = self.find_guild(gname, WORLD_ALIASES.get(w, [w]))
            return self.members_of(gid)["members"] if gid else None
        except NexonError:
            return None

    def verify(self, name, gname, world=None):
        """'name이 gname 길드에 있다'는 정보를 그 길드의 길드원 목록과 대조.
        confirmed = 목록에도 있음(포착) / stale = 목록엔 없음(캐릭터 정보가 옛날 것 → 접속해서 갱신 필요)
        / unknown = 길드 목록을 못 받음."""
        ms = self._once(("glist", gname, world or self.world_api), lambda: self.guild_list(gname, world))
        if ms is None:
            return "unknown"
        return "confirmed" if name in ms else "stale"

    # ── 전체 흐름 ──

    def run(self):
        started = time.time()
        job = self.job
        job.steps = 4 + (1 if self.check_activity else 0) + (2 if self.reverse_top else 0) + (1 if self.deep else 0)

        # 1) 길드 정보 (최신 길드원 목록)
        job.start_phase("길드 정보 확인", 1)
        self.world_api, gid = self.find_guild(self.guild_input, WORLD_ALIASES.get(self.world, [self.world]))
        if not gid:
            raise ScanError(f"'{self.world}' 월드에서 '{self.guild_input}' 길드를 찾지 못했어요. 이름과 월드를 확인해 주세요.")
        ours = self.members_of(gid)
        guild = ours["guild_name"] or self.guild_input
        names = list(dict.fromkeys(ours["members"]))  # 중복 제거 (순서 유지)
        if not names:
            raise ScanError("길드원 목록이 비어 있어요.")
        job.tick()

        # 2) 검사 전에 길드원 정보부터 최신으로 갱신
        job.start_phase("길드원 정보 최신으로 갱신", len(names))
        infos = dict(zip(names, self._pmap(self.info, names)))

        # 3) 길드원마다 대표 캐릭터
        job.start_phase("길드원 대표 캐릭터 조회", len(names))
        reps = dict(zip(names, self._pmap(self.rep_of, names)))

        # 4) 길드 밖에 있는 대표 캐릭터 정보 (최신) + 다른 길드면 그 길드 목록과 대조
        rep_names = sorted({r["rep"] for r in reps.values() if r["rep"]})
        outside = [r for r in rep_names if r not in infos]
        job.start_phase("대표 캐릭터 정보 갱신·길드 대조", len(outside))
        infos.update(zip(outside, self._pmap(self.info, outside)))
        checks = {}  # 대표 이름 → 다른 길드 소속 확인 결과
        for rep in rep_names:
            g = (infos.get(rep) or {}).get("guild")
            if g and g != guild:
                checks[rep] = {"guild": g, "status": self.verify(rep, g)}

        # 5) (선택) 30일 성장 확인 — 대표 캐릭터만, 30일 전 기본 정보와 레벨·경험치 비교
        stale = {}
        if self.check_activity:
            job.start_phase("30일 성장 확인", len(rep_names))
            old = self._pmap(lambda n: self.info(n, self.date30), rep_names)
            for n, o in zip(rep_names, old):
                cur = infos.get(n)
                stale[n] = None if not (cur and o) else (
                    cur.get("level") == o.get("level") and cur.get("exp") == o.get("exp"))

        # 6) 다른 길드에 있는 부캐 찾기
        #    - 심층: 대표(본캐)가 가 있는 '의심 길드'의 길드원을 전부 확인 (부캐가 더 있는지)
        #    - 역방향(선택): 길드 랭킹 상위 N개 길드까지
        others = {}  # 대표 이름 → [다른 길드 캐릭터 …]
        other_stats = {"guilds": 0, "members": 0, "unknown": 0, "deep_guilds": []}
        our_reps = set(rep_names)
        deep_guilds = sorted({c["guild"] for c in checks.values() if c["status"] != "unknown"})
        targets = list(deep_guilds) if self.deep else []
        if self.reverse_top:
            targets += [g for g in self.ranked_guilds(guild) if g not in targets]
        if targets:
            others, other_stats = self.scan_guilds(targets, our_reps)
            other_stats["deep_guilds"] = deep_guilds if self.deep else []

        result = build_result(guild, names, reps, infos, stale, others, self.check_activity, bool(self.reverse_top), checks)
        result["guild"] = {"name": guild, "world": self.world, "master": ours["master"], "level": ours["level"],
                           "user_count": ours["user_count"], "date": self.date, "fresh": self.fresh}
        s = result["summary"]
        s.update({"other_guilds": other_stats["guilds"], "other_members": other_stats["members"],
                  "other_unknown": other_stats["unknown"], "deep_guilds": other_stats["deep_guilds"],
                  "api_calls": self.c.calls, "cache_hits": self.cache_hits,
                  "seconds": round(time.time() - started, 1)})
        return result

    def ranked_guilds(self, our_guild):
        """길드 랭킹 상위 reverse_top개(-1이면 전체) 길드 이름."""
        job = self.job
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
        return guild_names[:limit] if limit else guild_names

    def scan_guilds(self, guild_names, our_reps):
        """여러 길드의 길드원 대표를 확인해서, 대표가 우리 길드원 대표와 같은 캐릭터(= 같은 계정의 부캐)를 찾는다."""
        job = self.job

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
            out.setdefault(r["rep"], []).append({**ci, "name": name, "guild": gname, "status": "confirmed"})
        return out, {"guilds": len(guild_names), "members": len(pairs),
                     "unknown": sum(1 for r in found if not r["rep"])}


LOOKUP_MAX = 60  # 이름 조회 한 번에 최대 몇 명 (핸즈 캐릭터 목록 한 계정분)


def lookup(client, store, names, our_guild=""):
    """이름(여러 개)으로 조회: 먼저 캐릭터마다 최신 정보로 갱신 → 길드 + 그 계정의 유니온 대표 캐릭터와 대표의 길드.
    월드는 캐릭터 정보에서 알아낸다(여러 월드가 섞여 있어도 됨).
    our_guild(우리 길드)가 있으면 그 밖의 길드에 있는 캐릭터를 '다른 길드'로 표시하고,
    그 길드의 길드원 목록과 대조해서 포착(confirmed) / 정보 갱신 필요(stale)를 가린다.
    넣은 이름끼리 대표가 같으면 같은 계정 — 핸즈 목록이면 전부 한 계정이어야 정상."""
    our_guild = (our_guild or "").strip()
    names = list(dict.fromkeys(names))[:LOOKUP_MAX]
    job = Job("lookup", "", "")
    sc = Scanner(client, store, job, "", "")

    def one(name):
        info = sc.info(name)
        if not info:
            return {"name": name, "found": False, "reason": "캐릭터를 찾을 수 없음 (이름·개명 확인)"}
        world = info.get("world") or ""
        rep = sc.rep_of(name, world)
        rep_info = sc.info(rep["rep"]) if rep["rep"] else None
        guild = info.get("guild") or ""
        rep_guild = (rep_info or {}).get("guild") or ""
        real = info.get("name") or name
        other = bool(our_guild) and bool(guild) and guild != our_guild
        rep_other = bool(our_guild) and bool(rep_guild) and rep_guild != our_guild and rep["rep"] != real
        dual = other or rep_other or (not our_guild and bool(rep["rep"]) and bool(guild)
                                      and bool(rep_guild) and guild != rep_guild)
        # 다른 길드에 있다고 나오면 그 길드의 길드원 목록과 대조
        targets = []
        if our_guild:
            if other:
                targets.append((real, guild))
            if rep_other:
                targets.append((rep["rep"], rep_guild))
        elif dual:
            targets = [(real, guild), (rep["rep"], rep_guild)]
        checks = [{"who": w, "guild": g, "status": sc.verify(w, g, world)} for w, g in targets]
        return {
            "name": name, "found": True, "world": world, "char": info | {"name": real},
            "rep": (rep_info or {}) | {"name": rep["rep"]} if rep["rep"] else None,
            "union_level": rep.get("union_level"),
            "is_rep": rep["rep"] == real,
            "reason": rep["reason"],
            "other_guild": other,           # 이 캐릭터가 우리 말고 다른 길드에 있음
            "rep_other_guild": rep_other,   # 대표(본캐)가 우리 말고 다른 길드에 있음
            "dual": dual,
            "checks": checks,               # 다른 길드 목록과 대조한 결과
            "fresh": bool(info.get("fresh")),
        }

    out = sc._pmap(one, names)

    # 계정 묶기: 대표(+월드)가 같은 것끼리
    by_rep = {}
    for r in out:
        if r.get("rep"):
            by_rep.setdefault((r["world"], r["rep"]["name"]), []).append(r["name"])
    for r in out:
        if r.get("rep"):
            r["same_account"] = [n for n in by_rep[(r["world"], r["rep"]["name"])] if n != r["name"]]
    suggest_fixes(sc, out, by_rep)
    found = [r for r in out if r["found"]]
    all_checks = [c for r in out for c in r.get("checks", [])]
    return {
        "date": sc.date, "our_guild": our_guild, "results": out,
        "summary": {
            "total": len(out), "found": len(found), "not_found": len(out) - len(found),
            "dual": sum(1 for r in out if r.get("dual")),
            "confirmed": sum(1 for c in all_checks if c["status"] == "confirmed"),
            "stale": sum(1 for c in all_checks if c["status"] == "stale"),
            "not_fresh": sum(1 for r in found if not r.get("fresh")),
            "accounts": len(by_rep),
            "unknown_rep": sum(1 for r in found if not r.get("rep")),
            "worlds": sorted({r["world"] for r in found if r["world"]}),
            "api_calls": client.calls, "cache_hits": sc.cache_hits,
        },
    }


# ── 이미지 인식이 잘 틀리는 한글 글자 바로잡기 ──
# 닉네임엔 '뇬·쀼' 같은 드문 글자가 많아서, 이미지 인식이 흔한 글자(논·뿌)로 읽는 일이 잦다.
# 없는 이름이 나오면 헷갈리기 쉬운 자모를 하나(또는 모음 둘)씩 바꿔 넥슨에서 찾아보고, 같은 계정이면 제안한다.
_CHO = {0: 1, 1: 0, 3: 4, 4: 3, 7: 8, 8: 7, 9: 10, 10: 9, 12: 13, 13: 12}          # ㄱㄲ ㄷㄸ ㅂㅃ ㅅㅆ ㅈㅉ
_JUNG = {0: 2, 2: 0, 4: 6, 6: 4, 8: 12, 12: 8, 13: 17, 17: 13, 1: 3, 3: 1, 5: 7, 7: 5}  # ㅏㅑ ㅓㅕ ㅗㅛ ㅜㅠ ㅐㅒ ㅔㅖ
_JONG = {19: 20, 20: 19, 1: 2, 2: 1}                                                     # 받침 ㅅㅆ ㄱㄲ
SUGGEST_MAX = 40  # 이름 하나당 넥슨에 물어볼 후보 수 상한


def hangul_variants(name):
    """헷갈리기 쉬운 자모를 바꾼 후보들 (가까운 것부터). 한 글자 바꾸기 → 모음 두 개 바꾸기."""
    def parts(ch):
        c = ord(ch) - 0xAC00
        return (c // 588, (c % 588) // 28, c % 28) if 0 <= c < 11172 else None

    def make(cho, jung, jong):
        return chr(0xAC00 + cho * 588 + jung * 28 + jong)

    singles, vowel_spots = [], []
    for i, ch in enumerate(name):
        p = parts(ch)
        if not p:
            continue
        cho, jung, jong = p
        if jung in _JUNG:
            singles.append((i, make(cho, _JUNG[jung], jong)))
            vowel_spots.append((i, make(cho, _JUNG[jung], jong)))
        if jung in (5, 1):  # ㅔ↔ㅐ 는 ㅖ/ㅒ 와 별도로 한 번 더
            other = {5: 1, 1: 5}[jung]
            singles.append((i, make(cho, other, jong)))
        if cho in _CHO:
            singles.append((i, make(_CHO[cho], jung, jong)))
        if jong in _JONG:
            singles.append((i, make(cho, jung, _JONG[jong])))
    out = []

    def add(chars):
        n = "".join(chars)
        if n != name and n not in out:
            out.append(n)

    for i, c in singles:
        chars = list(name); chars[i] = c; add(chars)
    for a in range(len(vowel_spots)):
        for b in range(a + 1, len(vowel_spots)):
            chars = list(name)
            for i, c in (vowel_spots[a], vowel_spots[b]):
                chars[i] = c
            add(chars)
    return out[:SUGGEST_MAX]


def suggest_fixes(sc, out, by_rep):
    """없는 이름마다 비슷한 진짜 캐릭터를 찾아 r['suggest']에 넣는다. 같은 계정이면 same_account=True."""
    missing = [r for r in out if not r["found"]]
    if not missing:
        return
    # 이 목록의 주 계정 = 캐릭터가 가장 많이 묶인 대표
    main = max(by_rep, key=lambda k: len(by_rep[k])) if by_rep else None
    pairs = [(r, cand) for r in missing for cand in hangul_variants(r["name"])]
    exists = sc._pmap(lambda p: sc.ocid(p[1]), pairs)
    for (r, cand), oc in zip(pairs, exists):
        if not oc:
            continue
        info = sc.info(cand)
        if not info:
            continue
        rep = sc.rep_of(cand, info.get("world")) if info.get("world") else {"rep": None}
        r.setdefault("suggest", []).append({
            "name": info.get("name") or cand, "level": info.get("level"), "guild": info.get("guild") or "",
            "same_account": bool(main) and (info.get("world"), rep["rep"]) == main,
        })
    for r in missing:
        if r.get("suggest"):
            r["suggest"].sort(key=lambda x: not x["same_account"])


def slim(d):
    """character/basic 응답에서 필요한 것만."""
    if not d:
        return None
    return {"name": d.get("character_name"), "guild": d.get("character_guild_name") or "",
            "level": d.get("character_level"), "job": d.get("character_class"),
            "exp": d.get("character_exp"), "image": d.get("character_image"),
            "access": d.get("access_flag"), "world": d.get("world_name")}


def build_result(guild, names, reps, infos, stale, others, activity, reverse, checks=None):
    def card(n):
        i = infos.get(n) or {}
        return {"name": n, "level": i.get("level"), "job": i.get("job"), "image": i.get("image"),
                "guild": i.get("guild"), "away7": i.get("access") == "false", "fresh": bool(i.get("fresh"))}

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
        a["check"] = (checks or {}).get(rep)             # 그 길드 목록과 대조: confirmed / stale / unknown
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
            "confirmed": sum(1 for a in out if a.get("check") and a["check"]["status"] == "confirmed") +
                         sum(1 for a in out if a["reverse"] and not a["forward"]),
            "stale": sum(1 for a in out if a.get("check") and a["check"]["status"] == "stale"),
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
