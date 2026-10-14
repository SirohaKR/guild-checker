# SQLite 저장소(data/guild.db) — 사용 방식·계정, 계정별 설정(API 키·우리 길드), 넥슨 API 결과 캐시, 지난 검색 기록.
# API 키가 들어 있으니 이 파일은 남에게 주지 말 것.
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
CREATE TABLE IF NOT EXISTS settings (        -- (예전 버전) 설정 — user_settings로 옮겨진다
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS app_config (      -- 사이트 전체 설정: 사용 방식(solo/shared), 로그인 비밀값
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS users (           -- 같이 쓰기 모드의 계정 (비밀번호는 해시만 저장)
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_settings (   -- 계정별 설정 (API 키, 우리 길드·월드, Claude 모델). 혼자 쓰기 = user_id 0
    user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT,
    PRIMARY KEY (user_id, key)
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
        self._migrate()

    def _migrate(self):
        """예전 DB 고치기: 검색 기록에 주인(owner) 칸 추가, 예전 설정을 혼자 쓰기(user 0) 설정으로."""
        cols = [r["name"] for r in self.q("PRAGMA table_info(scans)")]
        if "owner" not in cols:
            self.q("ALTER TABLE scans ADD COLUMN owner INTEGER NOT NULL DEFAULT 0")
        if not self.q("SELECT 1 FROM user_settings LIMIT 1") and self.q("SELECT 1 FROM settings LIMIT 1"):
            self.q("INSERT INTO user_settings (user_id, key, value) SELECT 0, key, value FROM settings")

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

    # ── 사이트 설정 (사용 방식 등) ──

    def get_config(self, key):
        r = self.q("SELECT value FROM app_config WHERE key=?", (key,), one=True)
        return r["value"] if r else None

    def set_config(self, key, value):
        self.q("INSERT OR REPLACE INTO app_config VALUES (?,?)", (key, value))

    # ── 계정 (같이 쓰기) ──

    def user_count(self):
        return self.q("SELECT COUNT(*) AS n FROM users", one=True)["n"]

    def get_user(self, uid=None, username=None):
        if uid is not None:
            r = self.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
        else:
            r = self.q("SELECT * FROM users WHERE username=?", (username,), one=True)
        return dict(r) if r else None

    def list_users(self):
        return [{"id": r["id"], "username": r["username"], "is_admin": bool(r["is_admin"]), "created_at": r["created_at"]}
                for r in self.q("SELECT * FROM users ORDER BY is_admin DESC, id")]

    def add_user(self, username, pw_hash, is_admin=False):
        with self.lock:
            cur = self.conn.execute("INSERT INTO users (username, pw_hash, is_admin, created_at) VALUES (?,?,?,?)",
                                    (username, pw_hash, int(is_admin), now().isoformat(timespec="seconds")))
            self.conn.commit()
            return cur.lastrowid

    def set_password(self, uid, pw_hash):
        self.q("UPDATE users SET pw_hash=? WHERE id=?", (pw_hash, uid))

    def delete_user(self, uid):
        self.q("DELETE FROM users WHERE id=?", (uid,))
        self.q("DELETE FROM user_settings WHERE user_id=?", (uid,))
        self.q("DELETE FROM scans WHERE owner=?", (uid,))

    def move_owner(self, old, new):
        """혼자 쓰기 → 같이 쓰기로 바꿀 때 기존 설정·기록을 관리자 계정으로."""
        self.q("UPDATE user_settings SET user_id=? WHERE user_id=?", (new, old))
        self.q("UPDATE scans SET owner=? WHERE owner=?", (new, old))

    # ── 계정별 설정 ──

    def get_settings(self, uid=0):
        return {r["key"]: r["value"] for r in self.q("SELECT key, value FROM user_settings WHERE user_id=?", (uid,))}

    def set_settings(self, values, uid=0):
        """값이 None이면 지운다."""
        for k, v in values.items():
            if v is None:
                self.q("DELETE FROM user_settings WHERE user_id=? AND key=?", (uid, k))
            else:
                self.q("INSERT OR REPLACE INTO user_settings VALUES (?,?,?)", (uid, k, v))

    # ── 검사 결과 ──

    def save_scan(self, world, guild, data_date, options, summary, result, owner=0):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO scans (world, guild, data_date, created_at, options, summary, result, owner) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (world, guild, data_date, now().isoformat(timespec="seconds"),
                 json.dumps(options, ensure_ascii=False), json.dumps(summary, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False), owner))
            self.conn.commit()
            return cur.lastrowid

    def previous_scan(self, world, guild, before_id, owner=0):
        r = self.q("SELECT * FROM scans WHERE world=? AND guild=? AND id<? AND owner=? ORDER BY id DESC LIMIT 1",
                   (world, guild, before_id, owner), one=True)
        return self._scan(r) if r else None

    def get_scan(self, scan_id, owner=0):
        r = self.q("SELECT * FROM scans WHERE id=? AND owner=?", (scan_id, owner), one=True)
        return self._scan(r) if r else None

    def list_scans(self, owner=0):
        return [{"id": r["id"], "world": r["world"], "guild": r["guild"], "data_date": r["data_date"],
                 "created_at": r["created_at"], "options": json.loads(r["options"] or "{}"),
                 "summary": json.loads(r["summary"] or "{}")}
                for r in self.q("SELECT id, world, guild, data_date, created_at, options, summary FROM scans "
                                "WHERE owner=? ORDER BY id DESC", (owner,))]

    def delete_scan(self, scan_id, owner=0):
        self.q("DELETE FROM scans WHERE id=? AND owner=?", (scan_id, owner))

    @staticmethod
    def _scan(r):
        return {"id": r["id"], "world": r["world"], "guild": r["guild"], "data_date": r["data_date"],
                "created_at": r["created_at"], "options": json.loads(r["options"] or "{}"),
                "summary": json.loads(r["summary"] or "{}"), "result": json.loads(r["result"] or "{}")}
