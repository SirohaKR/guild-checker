# SQLite 저장소 — 넥슨 API 결과 캐시(다시 검사할 때 빠르게)와 지난 검사 결과.
# API 키는 여기에 저장하지 않는다.
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "guild.db"))

# 캐시를 믿는 기간(일). 대표 캐릭터는 사람이 바꿀 수 있어서 짧게, ocid는 거의 안 바뀌어서 길게.
OCID_TTL_DAYS = int(os.environ.get("OCID_TTL_DAYS", "30"))
REP_TTL_DAYS = int(os.environ.get("REP_TTL_DAYS", "3"))
MISS_TTL_DAYS = 1  # "못 찾음" 결과는 하루만 기억 (다음 날엔 다시 확인)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ocid_cache (      -- 캐릭터 이름 → ocid (ocid가 비어 있으면 '없는 캐릭터')
    name TEXT PRIMARY KEY, ocid TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rep_cache (       -- ocid → 그 계정의 유니온 대표 캐릭터 (비어 있으면 '랭킹에 없음')
    ocid TEXT NOT NULL, world TEXT NOT NULL,
    rep_name TEXT, union_level INTEGER, updated_at TEXT NOT NULL,
    PRIMARY KEY (ocid, world)
);
CREATE TABLE IF NOT EXISTS char_cache (      -- 그날 기준 캐릭터 기본 정보 (지난 날짜 데이터는 안 바뀜)
    ocid TEXT NOT NULL, date TEXT NOT NULL, data TEXT,
    PRIMARY KEY (ocid, date)
);
CREATE TABLE IF NOT EXISTS guild_cache (     -- 길드 이름 → oguild_id
    world TEXT NOT NULL, name TEXT NOT NULL, oguild_id TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY (world, name)
);
CREATE TABLE IF NOT EXISTS guild_members (   -- 그날 기준 길드원 이름 목록
    oguild_id TEXT NOT NULL, date TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (oguild_id, date)
);
CREATE TABLE IF NOT EXISTS scans (           -- 지난 검사 결과
    id INTEGER PRIMARY KEY,
    world TEXT NOT NULL, guild TEXT NOT NULL, data_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    options TEXT, summary TEXT, result TEXT
);
"""


def now():
    return datetime.now(KST)


def _fresh(ts, days):
    return ts and datetime.fromisoformat(ts) > now() - timedelta(days=days)


class Store:
    """여러 스레드가 같이 쓰는 DB 연결 하나 + 잠금."""

    def __init__(self, path=DB_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def q(self, sql, args=(), one=False):
        with self.lock:
            cur = self.conn.execute(sql, args)
            rows = cur.fetchall()
            self.conn.commit()
        return (rows[0] if rows else None) if one else rows

    # ── 캐시: 값이 있으면 (찾았는지, 값)을 돌려주고, 없거나 오래됐으면 None ──

    def get_ocid(self, name):
        r = self.q("SELECT ocid, updated_at FROM ocid_cache WHERE name=?", (name,), one=True)
        if r and _fresh(r["updated_at"], OCID_TTL_DAYS if r["ocid"] else MISS_TTL_DAYS):
            return (True, r["ocid"])
        return None

    def put_ocid(self, name, ocid):
        self.q("INSERT OR REPLACE INTO ocid_cache VALUES (?,?,?)", (name, ocid, now().isoformat()))

    def drop_ocid(self, name):
        self.q("DELETE FROM ocid_cache WHERE name=?", (name,))

    def get_rep(self, ocid, world):
        r = self.q("SELECT rep_name, union_level, updated_at FROM rep_cache WHERE ocid=? AND world=?",
                   (ocid, world), one=True)
        if r and _fresh(r["updated_at"], REP_TTL_DAYS if r["rep_name"] else MISS_TTL_DAYS):
            return (True, {"name": r["rep_name"], "union_level": r["union_level"]} if r["rep_name"] else None)
        return None

    def put_rep(self, ocid, world, rep):
        self.q("INSERT OR REPLACE INTO rep_cache VALUES (?,?,?,?,?)",
               (ocid, world, rep and rep["name"], rep and rep.get("union_level"), now().isoformat()))

    def get_char(self, ocid, date):
        r = self.q("SELECT data FROM char_cache WHERE ocid=? AND date=?", (ocid, date), one=True)
        return (True, json.loads(r["data"]) if r["data"] else None) if r else None

    def put_char(self, ocid, date, data):
        self.q("INSERT OR REPLACE INTO char_cache VALUES (?,?,?)",
               (ocid, date, json.dumps(data, ensure_ascii=False) if data else None))

    def get_guild(self, world, name):
        r = self.q("SELECT oguild_id, updated_at FROM guild_cache WHERE world=? AND name=?", (world, name), one=True)
        if r and _fresh(r["updated_at"], OCID_TTL_DAYS if r["oguild_id"] else MISS_TTL_DAYS):
            return (True, r["oguild_id"])
        return None

    def put_guild(self, world, name, oguild_id):
        self.q("INSERT OR REPLACE INTO guild_cache VALUES (?,?,?,?)", (world, name, oguild_id, now().isoformat()))

    def get_members(self, oguild_id, date):
        r = self.q("SELECT data FROM guild_members WHERE oguild_id=? AND date=?", (oguild_id, date), one=True)
        return json.loads(r["data"]) if r else None

    def put_members(self, oguild_id, date, data):
        self.q("INSERT OR REPLACE INTO guild_members VALUES (?,?,?)",
               (oguild_id, date, json.dumps(data, ensure_ascii=False)))

    def prune(self):
        """오래된 날짜별 캐시 정리 (60일 넘은 것)."""
        cut = (now() - timedelta(days=60)).date().isoformat()
        self.q("DELETE FROM char_cache WHERE date < ?", (cut,))
        self.q("DELETE FROM guild_members WHERE date < ?", (cut,))

    # ── 검사 결과 ──

    def save_scan(self, world, guild, data_date, options, summary, result):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO scans (world, guild, data_date, created_at, options, summary, result) VALUES (?,?,?,?,?,?,?)",
                (world, guild, data_date, now().isoformat(timespec="seconds"),
                 json.dumps(options, ensure_ascii=False), json.dumps(summary, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False)))
            self.conn.commit()
            return cur.lastrowid

    def previous_scan(self, world, guild, before_id):
        r = self.q("SELECT * FROM scans WHERE world=? AND guild=? AND id<? ORDER BY id DESC LIMIT 1",
                   (world, guild, before_id), one=True)
        return self._scan(r) if r else None

    def get_scan(self, scan_id):
        r = self.q("SELECT * FROM scans WHERE id=?", (scan_id,), one=True)
        return self._scan(r) if r else None

    def list_scans(self):
        return [{"id": r["id"], "world": r["world"], "guild": r["guild"], "data_date": r["data_date"],
                 "created_at": r["created_at"], "options": json.loads(r["options"] or "{}"),
                 "summary": json.loads(r["summary"] or "{}")}
                for r in self.q("SELECT id, world, guild, data_date, created_at, options, summary FROM scans ORDER BY id DESC")]

    def delete_scan(self, scan_id):
        self.q("DELETE FROM scans WHERE id=?", (scan_id,))

    @staticmethod
    def _scan(r):
        return {"id": r["id"], "world": r["world"], "guild": r["guild"], "data_date": r["data_date"],
                "created_at": r["created_at"], "options": json.loads(r["options"] or "{}"),
                "summary": json.loads(r["summary"] or "{}"), "result": json.loads(r["result"] or "{}")}
