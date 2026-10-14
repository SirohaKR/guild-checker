# 가짜 넥슨 API 서버 — 진짜 키 없이 검사 흐름을 시험해 보기 위한 것.
# 실행:  python tests/mock_nexon.py          (http://127.0.0.1:5099)
# 그다음 앱을 NEXON_BASE=http://127.0.0.1:5099/maplestory/v1 로 띄우면 이 가짜 데이터로 검사된다.
import random
import threading
from collections import Counter
from datetime import date, timedelta

from flask import Flask, jsonify, request

app = Flask(__name__)
WORLD = "스카니아"
OUR = "단풍잎"
JOBS = ["아크메이지(불,독)", "비숍", "나이트로드", "섀도어", "보우마스터", "신궁", "히어로", "팔라딘",
        "다크나이트", "캐논마스터", "카이저", "엔젤릭버스터", "아델", "일리움", "칼리", "라라", "호영", "제로"]

# 캐릭터 이름 → 정보.  account = 같은 계정끼리 같은 값
CHARS = {}
ACCOUNT_REP = {}       # 계정 → 유니온 대표 캐릭터
NO_RANK = set()        # 유니온 랭킹에 없는 계정
STALE = set()          # 30일 동안 성장 없는 캐릭터
GUILD_MEMBERS = {}     # 길드 이름 → [이름]


def add(name, account, guild, level, away=False, listed=True):
    """listed=False: 캐릭터 정보엔 그 길드로 나오지만 길드원 목록엔 없음 (정보가 옛날 것 — 접속해서 갱신 필요)"""
    CHARS[name] = {"account": account, "guild": guild, "level": level,
                   "job": JOBS[hash(name) % len(JOBS)], "exp": level * 1000 + len(name), "away": away}
    if guild and listed:
        GUILD_MEMBERS.setdefault(guild, []).append(name)


# A: 본캐+부캐 모두 우리 길드 (정상)
add("본캐A", "A", OUR, 285); add("부캐A1", "A", OUR, 262); add("부캐A2", "A", OUR, 240)
ACCOUNT_REP["A"] = "본캐A"
# B: 본캐는 '다른길드', 부캐 둘이 우리 길드 → 정방향 이중길드
add("남의본캐B", "B", "다른길드", 290); add("부캐B1", "B", OUR, 255); add("부캐B2", "B", OUR, 230)
ACCOUNT_REP["B"] = "남의본캐B"
# C: 본캐는 우리 길드, 부캐가 '다른길드' → 역방향에서만 잡힘
add("본캐C", "C", OUR, 280); add("부캐C1", "C", "다른길드", 250)
ACCOUNT_REP["C"] = "본캐C"
# D: 유니온 랭킹에 없는 계정 → 판별 불가
add("무랭킹D", "D", OUR, 210); NO_RANK.add("D"); ACCOUNT_REP["D"] = "무랭킹D"
# E: 대표가 길드 없음
add("무소속E", "E", "", 270); add("부캐E1", "E", OUR, 245); ACCOUNT_REP["E"] = "무소속E"
# F: 30일 성장 없음 + 7일 미접속
add("잠수F", "F", OUR, 260, away=True); STALE.add("잠수F"); ACCOUNT_REP["F"] = "잠수F"
# H: 가입 신청자 (핸즈 스크린샷 예시) — 이미지 인식이 '뇬→논', '쀼→뿌'로 잘못 읽는 이름들. 길드 없음
add("쨋뀨", "H", "", 290); add("췐쀼", "H", "", 260); add("쨋뀨유뇬7", "H", "", 250); add("쨋뀨유논2", "H", "", 200)
ACCOUNT_REP["H"] = "쨋뀨"
# 남의 캐릭터인데 이름이 비슷한 것 (제안할 때 '다른 계정'으로 보여야 함)
add("췐부", "Z", "세번째길드", 230); ACCOUNT_REP["Z"] = "췐부"
# S: 가입 신청자 — 부캐 정보엔 '세번째길드'로 나오지만 그 길드 목록엔 없음 (탈퇴 후 접속 안 해서 정보가 옛날 것)
add("갱신필요S", "S", "", 270); add("갱신필요S부", "S", "세번째길드", 230, listed=False); ACCOUNT_REP["S"] = "갱신필요S"
# T: 우리 길드원 — 대표 정보엔 '세번째길드'지만 그 길드 목록엔 없음 (강퇴·탈퇴 후 접속 안 함 → 이미 나간 것)
add("나간대표T", "T", "세번째길드", 275, listed=False); add("부캐T1", "T", OUR, 240); ACCOUNT_REP["T"] = "나간대표T"
# 길드 목록에는 있는데 개명해서 /id가 안 되는 캐릭터
GUILD_MEMBERS[OUR].append("개명한캐릭")
# 세번째길드: 우리와 관계없는 사람들
for i in range(6):
    add(f"남남{i}", f"X{i}", "세번째길드", 220 + i)
    ACCOUNT_REP[f"X{i}"] = f"남남{i}"
# 화면 채우기용 평범한 길드원들 (일부는 부캐 여럿, 하나는 정방향 이중길드)
rnd = random.Random(7)
for i in range(14):
    acc = f"P{i}"
    main = f"메이플러{i:02d}"
    if i == 3:
        add(main, acc, "세번째길드", 288)
    else:
        add(main, acc, OUR, rnd.randint(250, 289))
    ACCOUNT_REP[acc] = main
    for k in range(rnd.randint(0, 2) + (1 if i == 3 else 0)):
        add(f"{main}부{k + 1}", acc, OUR, rnd.randint(200, 260))

GUILDS = {OUR: "g-our", "다른길드": "g-other", "세번째길드": "g-third"}
OCID = {n: f"oc-{i}" for i, n in enumerate(CHARS)}
BY_OCID = {v: k for k, v in OCID.items()}

calls = Counter()
calls_lock = threading.Lock()
fail_once = {"union:부캐A2"}  # 처음 한 번은 429를 돌려서 재시도를 시험


by_key = Counter()  # (주소, 이름/ocid/날짜) → 호출 수


def count(path):
    with calls_lock:
        calls[path] += 1
        key = request.args.get("character_name") or request.args.get("ocid") or request.args.get("oguild_id") or ""
        by_key[(path, key, request.args.get("date") or "latest")] += 1


def bad(msg="invalid", status=400):
    return jsonify({"error": {"name": "OPENAPI00004", "message": msg}}), status


@app.get("/maplestory/v1/id")
def ocid():
    count("id")
    n = request.args.get("character_name")
    return jsonify({"ocid": OCID[n]}) if n in OCID else bad("character not found")


@app.get("/maplestory/v1/guild/id")
def guild_id():
    count("guild/id")
    if request.args.get("world_name") != WORLD:
        return bad("world")
    g = request.args.get("guild_name")
    return jsonify({"oguild_id": GUILDS[g]}) if g in GUILDS else bad("guild")


@app.get("/maplestory/v1/guild/basic")
def guild_basic():
    count("guild/basic")
    name = {v: k for k, v in GUILDS.items()}.get(request.args.get("oguild_id"))
    if not name:
        return bad()
    return jsonify({"guild_name": name, "world_name": WORLD, "guild_level": 28,
                    "guild_master_name": GUILD_MEMBERS[name][0], "guild_member_count": len(GUILD_MEMBERS[name]),
                    "guild_user_count": None, "guild_member": GUILD_MEMBERS[name]})


@app.get("/maplestory/v1/ranking/union")
def union():
    count("ranking/union")
    oc = request.args.get("ocid")
    name = BY_OCID.get(oc)
    if not name:
        return bad()
    with calls_lock:
        if f"union:{name}" in fail_once:
            fail_once.discard(f"union:{name}")
            return jsonify({"error": {"name": "OPENAPI00007", "message": "too many"}}), 429
    acc = CHARS[name]["account"]
    if acc in NO_RANK:
        return jsonify({"ranking": []})
    rep = ACCOUNT_REP[acc]
    return jsonify({"ranking": [{"date": request.args.get("date"), "ranking": 1234, "character_name": rep,
                                 "world_name": WORLD, "union_level": 8000 + CHARS[rep]["level"] * 10}]})


@app.get("/maplestory/v1/character/basic")
def basic():
    count("character/basic")
    name = BY_OCID.get(request.args.get("ocid"))
    if not name:
        return bad()
    c = CHARS[name]
    old = request.args.get("date", "9999") < (date.today() - timedelta(days=15)).isoformat()  # 30일 전 조회
    lv, exp = c["level"], c["exp"]
    if old and name not in STALE:
        lv, exp = lv - 1, exp - 500
    return jsonify({"character_name": name, "world_name": WORLD, "character_class": c["job"],
                    "character_level": lv, "character_exp": exp, "character_guild_name": c["guild"] or None,
                    "character_image": f"{request.host_url}img/{OCID[name]}.svg",
                    "access_flag": "false" if c["away"] else "true"})


@app.get("/maplestory/v1/ranking/guild")
def guild_ranking():
    count("ranking/guild")
    if request.args.get("page", "1") != "1":
        return jsonify({"ranking": []})
    return jsonify({"ranking": [{"ranking": i + 1, "guild_name": g, "world_name": WORLD}
                                for i, g in enumerate(["다른길드", OUR, "세번째길드"])]})


COLORS = ["#ff7a3d", "#3aa6f0", "#23b89d", "#e5484d", "#9b6bff", "#f2b134"]


@app.get("/img/<oc>.svg")
def img(oc):
    # 넥슨 이미지처럼 여백 많은 300x300 그림 (가운데에 작은 캐릭터)
    name = BY_OCID.get(oc, "?")
    col = COLORS[hash(name) % len(COLORS)]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">
<circle cx="150" cy="128" r="22" fill="#ffe2c8"/><rect x="132" y="150" width="36" height="40" rx="8" fill="{col}"/>
<rect x="134" y="188" width="12" height="18" rx="4" fill="#5b4636"/><rect x="154" y="188" width="12" height="18" rx="4" fill="#5b4636"/>
<path d="M126 122 Q150 92 174 122 L174 112 Q150 84 126 112 Z" fill="{col}"/></svg>"""
    return svg, 200, {"Content-Type": "image/svg+xml"}


@app.get("/_stats")
def stats():
    return jsonify(dict(calls))


@app.post("/_reset")
def reset():
    calls.clear()
    by_key.clear()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(port=5099, threaded=True)
