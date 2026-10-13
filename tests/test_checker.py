# 가짜 넥슨 서버로 검사 흐름 전체를 시험한다.  실행: python tests/test_checker.py
# 확인하는 것: 정방향 / 역방향 / 판별 불가 / 429 재시도 / 같은 캐릭터 중복 호출 없음 / 두 번째 검사는 캐시 사용
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "test.db")
os.environ["NEXON_BASE"] = "http://127.0.0.1:5098/maplestory/v1"

from werkzeug.serving import make_server  # noqa: E402

import checker  # noqa: E402
import mock_nexon  # noqa: E402
import nexon  # noqa: E402
from db import Store  # noqa: E402

srv = make_server("127.0.0.1", 5098, mock_nexon.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
store = Store()


def scan(reverse_top=0, activity=False, guild="단풍잎", world="스카니아"):
    mock_nexon.calls.clear()
    job = checker.Job("t", world, guild)
    client = nexon.Client("test-key")
    res = checker.Scanner(client, store, job, world, guild, reverse_top, activity).run()
    return res, dict(mock_nexon.calls), job


def acc(res, rep):
    return next(a for a in res["accounts"] if a["rep"]["name"] == rep)


ok = 0


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    print("  ✔", msg)


print("1) 정방향 + 30일 성장 확인 (첫 검사, 캐시 없음)")
res, calls, job = scan(activity=True)
s = res["summary"]
members = mock_nexon.GUILD_MEMBERS["단풍잎"]
check(s["characters"] == len(members), f"길드원 {len(members)}명 모두 검사")
check(acc(res, "남의본캐B")["forward"] and acc(res, "남의본캐B")["rep"]["guild"] == "다른길드", "B: 본캐가 다른 길드 → 이중길드")
check({c["name"] for c in acc(res, "남의본캐B")["ours"]} == {"부캐B1", "부캐B2"}, "B: 우리 길드 안 부캐 2명")
check(not acc(res, "본캐A")["dual"] and len(acc(res, "본캐A")["ours"]) == 3, "A: 정상, 캐릭터 3개가 한 계정으로 묶임")
check(not acc(res, "본캐C")["dual"], "C: 역방향 끈 상태에선 안 잡힘")
check(acc(res, "무소속E")["rep_guildless"] and not acc(res, "무소속E")["dual"], "E: 대표가 길드 없음 (이중길드 아님)")
unk = {u["name"]: u["reason"] for u in res["unknown"]}
check("무랭킹D" in unk and "유니온" in unk["무랭킹D"], "D: 유니온 랭킹에 없음 → 판별 불가")
check("개명한캐릭" in unk and "찾을 수 없음" in unk["개명한캐릭"], "개명한 캐릭터 → 판별 불가")
check(acc(res, "잠수F")["stale30"] is True and acc(res, "잠수F")["rep"]["away7"], "F: 30일 성장 없음 + 7일 미접속")
check(acc(res, "본캐A")["stale30"] is False, "A: 성장 있음")
check(acc(res, "메이플러03")["forward"], "메이플러03: 본캐가 세번째길드 → 이중길드")
# 중복 호출 없음: /id는 (길드원 + 길드 밖 대표) 한 번씩, union은 ocid 있는 길드원마다 한 번 (+429 재시도 1번)
outside_reps = {"남의본캐B", "무소속E", "메이플러03"}
check(calls["id"] == len(members) + len(outside_reps), f"/id 호출 {calls['id']}번 = 캐릭터마다 한 번")
check(calls["ranking/union"] == len(members) - 1 + 1, f"유니온 랭킹 {calls['ranking/union']}번 (429 재시도 포함)")
reps = len(res["accounts"])
check(calls["character/basic"] == len(members) - 1 + len(outside_reps) + reps,
      f"character/basic {calls['character/basic']}번 = 캐릭터마다 한 번 + 대표 30일 전 한 번")

print("2) 같은 검사를 다시 → 캐시 재사용")
res2, calls2, _ = scan(activity=True)
check(sum(calls2.values()) == 0, f"넥슨 API 호출 {sum(calls2.values())}번 (전부 캐시)")
check(res2["summary"]["dual"] == s["dual"], "결과 동일")

print("3) 역방향 검사 (상위 50개 길드)")
res3, calls3, job3 = scan(reverse_top=50)
c = acc(res3, "본캐C")
check(c["reverse"] and c["others"][0]["name"] == "부캐C1" and c["others"][0]["guild"] == "다른길드",
      "C: 부캐가 다른 길드 → 역방향으로 잡힘")
check(not any(o["name"] == "남의본캐B" for o in acc(res3, "남의본캐B")["others"]), "B: 대표 자신은 '다른 길드 부캐'에 중복 표시 안 함")
check(res3["summary"]["other_guilds"] == 2, "우리 길드는 빼고 다른 길드 2개 검사")
others_n = len(mock_nexon.GUILD_MEMBERS["다른길드"]) + len(mock_nexon.GUILD_MEMBERS["세번째길드"])
check(calls3["ranking/union"] == others_n, f"다른 길드원 유니온 조회 {calls3['ranking/union']}번 (한 명씩 한 번)")
check(calls3["id"] == others_n - 2, f"/id {calls3['id']}번 (아까 조회한 대표 2명은 캐시)")
check(calls3.get("guild/basic", 0) == 2, "길드원 목록은 다른 길드 2개만 새로 받음")

print("4) 비교 / 저장")
sid1 = store.save_scan("스카니아", "단풍잎", "d", {}, res["summary"], res)
sid3 = store.save_scan("스카니아", "단풍잎", "d", {}, res3["summary"], res3)
diff = checker.compare(res3, store.previous_scan("스카니아", "단풍잎", sid3))
check(diff["new_dual"] == ["본캐C"] and diff["gone_dual"] == [], "지난번과 비교: 새 이중길드 = 본캐C")

print("5) 없는 길드")
try:
    scan(guild="없는길드")
    check(False, "오류가 나야 함")
except checker.ScanError as e:
    check("찾지 못했어요" in str(e), "없는 길드 → 알기 쉬운 오류")

print("6) 날짜 계산")
from datetime import datetime  # noqa: E402
check(checker.data_date(datetime(2026, 10, 12, 1, 59, tzinfo=checker.KST)) == "2026-10-10", "새벽 2시 전 → 그제")
check(checker.data_date(datetime(2026, 10, 12, 2, 0, tzinfo=checker.KST)) == "2026-10-11", "새벽 2시 이후 → 어제")

print("7) 가입 확인 (이름 조회)")
mock_nexon.calls.clear()
r = checker.lookup(nexon.Client("test-key"), store, ["부캐B1", "부캐B2", "남의본캐B", "없는사람"], "단풍잎")
by = {x["name"]: x for x in r["results"]}
check(by["부캐B1"]["world"] == "스카니아", "월드를 캐릭터 정보에서 알아냄")
check(by["부캐B1"]["dual"] and by["부캐B1"]["rep_other_guild"] and not by["부캐B1"]["other_guild"],
      "부캐B1: 자신은 우리 길드, 대표가 다른길드 → 표시")
check(by["남의본캐B"]["other_guild"] and by["남의본캐B"]["is_rep"], "남의본캐B: 다른 길드 대표 캐릭터 → 표시")
check(not by["없는사람"]["found"], "없는 이름 → 찾을 수 없음")
check(r["summary"]["accounts"] == 1 and set(by["부캐B1"]["same_account"]) == {"부캐B2", "남의본캐B"}, "세 캐릭터가 한 계정")
r = checker.lookup(nexon.Client("test-key"), store, ["본캐A", "부캐A1", "본캐C"], "단풍잎")
check(r["summary"]["dual"] == 0 and r["summary"]["accounts"] == 2, "정상 길드원 → 다른 길드 없음, 계정 2개")
r = checker.lookup(nexon.Client("test-key"), store, ["부캐C1"], "")
check(r["results"][0]["dual"], "우리 길드 안 넣어도: 캐릭터와 대표 길드가 다르면 표시")
r = checker.lookup(nexon.Client("test-key"), store, ["부캐C1"], "다른길드")
check(r["results"][0]["rep_other_guild"] and not r["results"][0]["other_guild"], "부캐C1로 '다른길드' 가입 신청: 대표가 단풍잎 → 표시")

print("8) 스크린샷 읽기 (Claude는 가짜로)")
import vision  # noqa: E402


class FakeResp:
    stop_reason = "end_turn"
    model = "fake"

    class usage:
        input_tokens, output_tokens = 1234, 56

    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]


sent = {}


class FakeClient:
    def __init__(self, api_key):
        sent["key"] = api_key
        self.beta = self
        self.messages = self

    def create(self, **kw):
        sent["kw"] = kw
        return FakeResp('{"characters":[{"name":"본캐 A","level":285,"world":"스카니아","guild":"","unsure":false},'
                        '{"name":"본캐A","level":0,"world":"","guild":"","unsure":false},'
                        '{"name":"부캐A1","level":0,"world":"","guild":"","unsure":true}],"note":"핸즈 목록"}')


real = vision.anthropic.Anthropic
vision.anthropic.Anthropic = FakeClient
os.environ.pop("ANTHROPIC_API_KEY", None)
try:
    vision.read_names([(b"x", "image/jpeg")], "")
    check(False, "키 없으면 오류")
except vision.VisionError as e:
    check("키" in str(e), "Claude 키 없으면 알기 쉬운 오류")
out = vision.read_names([(b"x", "image/jpeg"), (b"y", "image/png")], "sk-test")
check([c["name"] for c in out["characters"]] == ["본캐A", "부캐A1"], "이름 공백 제거 + 중복 제거")
imgs = [b for b in sent["kw"]["messages"][0]["content"] if b["type"] == "image"]
check(len(imgs) == 2 and out["usage"]["input"] == 1234, "여러 장을 한 번에 보냄, 토큰 사용량 돌려줌")
try:
    vision.read_names([(b"x", "image/bmp")], "sk-test")
    check(False, "bmp 오류")
except vision.VisionError:
    check(True, "지원 안 하는 형식 거절")
vision.anthropic.Anthropic = real

srv.shutdown()
print(f"\n모두 통과 ({ok}개)")
