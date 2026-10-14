# 사용 방식(혼자/같이)·로그인·계정별 설정을 시험한다.  실행: python tests/test_app.py
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "app.db")
os.environ["NEXON_API_KEY"] = "env-key-should-not-leak"

import app as web  # noqa: E402
from db import Store  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    print("  ✔", msg)


def fresh(name):
    """빈 DB로 새로 시작"""
    web.store = Store(os.path.join(tmp, name))
    return web.app.test_client(), web.app.test_client()


print("1) 처음 진입 → 같이 쓰기 (관리자 만들기)")
a, b = fresh("shared.db")
check(a.get("/api/status").json["mode"] is None, "처음엔 사용 방식이 정해지지 않음")
check(a.get("/api/settings").status_code == 401, "방식 고르기 전엔 다른 기능 막힘")
r = a.post("/api/setup", json={"mode": "shared", "username": "a", "password": "123456"})
check(r.status_code == 400, "아이디가 너무 짧으면 거절")
r = a.post("/api/setup", json={"mode": "shared", "username": "길마", "password": "secret1"})
check(r.json["mode"] == "shared" and r.json["user"]["is_admin"], "관리자 계정 만들고 바로 로그인")
check(b.post("/api/setup", json={"mode": "shared", "username": "남", "password": "secret1"}).status_code == 409,
      "다른 사람이 또 관리자를 만들 수 없음")
check(b.get("/api/settings").status_code == 401 and b.get("/api/settings").json.get("login"), "로그인 안 한 사람은 막힘")

print("2) 관리자가 운영진 계정 추가 → 로그인")
check(a.post("/api/users", json={"username": "부길마", "password": "pass123"}).status_code == 200, "계정 추가")
check(a.post("/api/users", json={"username": "부길마", "password": "pass123"}).status_code == 400, "같은 아이디는 안 됨")
check(b.post("/api/login", json={"username": "부길마", "password": "틀림틀림"}).status_code == 401, "비밀번호 틀리면 거절")
r = b.post("/api/login", json={"username": "부길마", "password": "pass123"})
check(r.status_code == 200 and not r.json["user"]["is_admin"], "운영진 로그인 (관리자 아님)")
check(b.get("/api/users").status_code == 403, "운영진은 계정 관리 못 함")

print("3) API 키·길드는 계정마다 따로")
a.post("/api/settings", json={"world": "스카니아", "guild": "단풍잎", "nexon_key": "live_admin_key_1234"})
b.post("/api/settings", json={"world": "베라", "guild": "다른길드", "nexon_key": "test_member_key_5678"})
sa, sb = a.get("/api/settings").json, b.get("/api/settings").json
check(sa["guild"] == "단풍잎" and sb["guild"] == "다른길드", "우리 길드 설정이 계정마다 다름")
check(sa["nexon_key"].endswith("1234") and sb["nexon_key"].endswith("5678"), "API 키도 계정마다 다름 (가려서 보여줌)")
check("live_admin_key_1234" not in str(sa), "키 전체는 화면에 안 나감")
web.store.save_scan("스카니아", "단풍잎", "d", {}, {}, {}, owner=web.store.get_user(username="길마")["id"])
check(len(a.get("/api/scans").json) == 1 and len(b.get("/api/scans").json) == 0, "검색 기록도 계정마다 따로")
b.post("/api/settings", json={"clear_nexon_key": True})
check(b.get("/api/settings").json["nexon_key"] == "", "같이 쓰기에선 .env 키를 남에게 빌려주지 않음")

print("4) 비밀번호 바꾸기 · 초기화 · 계정 삭제 · 로그아웃")
check(b.post("/api/me/password", json={"current": "pass123", "new": "newpass1"}).status_code == 200, "내 비밀번호 바꾸기")
uid = web.store.get_user(username="부길마")["id"]
check(a.post(f"/api/users/{uid}/password", json={"password": "reset99"}).status_code == 200, "관리자가 비밀번호 초기화")
b.post("/api/logout")
check(b.get("/api/settings").status_code == 401, "로그아웃하면 막힘")
check(b.post("/api/login", json={"username": "부길마", "password": "reset99"}).status_code == 200, "초기화한 비밀번호로 로그인")
admin_id = web.store.get_user(username="길마")["id"]
check(a.delete(f"/api/users/{admin_id}").status_code == 400, "관리자 계정은 못 지움")
a.delete(f"/api/users/{uid}")
check(b.get("/api/settings").status_code == 401 and web.store.get_settings(uid) == {}, "지운 계정은 바로 막히고 설정도 지워짐")

print("5) 혼자 쓰기 → 나중에 같이 쓰기로 바꾸기")
c, d = fresh("solo.db")
check(c.post("/api/setup", json={"mode": "solo"}).json["mode"] == "solo", "혼자 쓰기 선택")
check(d.get("/api/settings").status_code == 200, "로그인 없이 누구나 바로 사용")
check(d.get("/api/settings").json["nexon_key"].startswith("env-ke"), "혼자 쓰기는 .env 키를 씀")
c.post("/api/settings", json={"guild": "단풍잎"})
web.store.save_scan("스카니아", "단풍잎", "d", {}, {}, {}, owner=0)
r = c.post("/api/setup", json={"mode": "shared", "username": "주인", "password": "secret1"})
check(r.json["mode"] == "shared" and r.json["user"]["username"] == "주인", "같이 쓰기로 바꾸며 관리자 만들기")
check(c.get("/api/settings").json["guild"] == "단풍잎" and len(c.get("/api/scans").json) == 1, "혼자 쓰던 설정·기록은 관리자에게")
check(d.get("/api/settings").status_code == 401, "이제 다른 사람은 로그인해야 함")
check(c.post("/api/setup", json={"mode": "solo"}).status_code == 409, "같이 쓰기에서 아무나 혼자 쓰기로 못 바꿈")

print("6) 예전 DB (설정 표 → 혼자 쓰기 설정으로 옮김)")
import sqlite3  # noqa: E402
old = os.path.join(tmp, "old.db")
con = sqlite3.connect(old)
con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
con.execute("INSERT INTO settings VALUES ('guild', '옛길드')")
con.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY, world TEXT NOT NULL, guild TEXT NOT NULL, data_date TEXT NOT NULL,"
            " created_at TEXT NOT NULL, options TEXT, summary TEXT, result TEXT)")
con.execute("INSERT INTO scans (world, guild, data_date, created_at) VALUES ('스카니아','옛길드','d','2026-10-01')")
con.commit(); con.close()
st = Store(old)
check(st.get_settings(0) == {"guild": "옛길드"} and len(st.list_scans(0)) == 1, "예전 설정·기록이 그대로 남음")

print(f"\n모두 통과 ({ok}개)")
