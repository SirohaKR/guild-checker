# 가짜 넥슨 서버로 검사 흐름 전체를 시험한다.  실행: python tests/test_checker.py
# 확인하는 것: 최신 갱신 / 정방향 / 의심 길드 대조(포착·갱신 필요) / 심층·역방향 / 판별 불가 / 429 재시도 / 중복 호출 없음 / 캐시
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


def scan(reverse_top=0, activity=False, guild="단풍잎", world="스카니아", deep=True):
    mock_nexon.calls.clear()
    mock_nexon.by_key.clear()
    job = checker.Job("t", world, guild)
    client = nexon.Client("test-key")
    res = checker.Scanner(client, store, job, world, guild, reverse_top, activity, deep=deep).run()
    return res, dict(mock_nexon.calls), job


def max_repeat(path):
    """한 검사 안에서 같은 이름/ocid로 같은 주소를 가장 많이 부른 횟수"""
    return max([n for (p, _, _), n in mock_nexon.by_key.items() if p == path] or [0])


def acc(res, rep):
    return next(a for a in res["accounts"] if a["rep"]["name"] == rep)


ok = 0


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    print("  ✔", msg)


print("1) 우리 길드원 검색 (최신 갱신 → 정방향 → 의심 길드 대조·심층, 30일 성장)")
res, calls, job = scan(activity=True)
s = res["summary"]
members = mock_nexon.GUILD_MEMBERS["단풍잎"]
check(s["characters"] == len(members), f"길드원 {len(members)}명 모두 검사")
check(mock_nexon.by_key[("character/basic", "oc-0", "latest")] == 1, "검사 전에 캐릭터 정보를 최신(날짜 없이)으로 갱신")
b = acc(res, "남의본캐B")
check(b["forward"] and b["rep"]["guild"] == "다른길드", "B: 본캐가 다른 길드 → 의심")
check(b["check"] == {"guild": "다른길드", "status": "confirmed"}, "B: 다른길드 길드원 목록과 대조 → 포착")
check({c["name"] for c in b["ours"]} == {"부캐B1", "부캐B2"}, "B: 우리 길드 안 부캐 2명")
check(not acc(res, "본캐A")["dual"] and len(acc(res, "본캐A")["ours"]) == 3, "A: 정상, 캐릭터 3개가 한 계정으로 묶임")
c = acc(res, "본캐C")
check(c["reverse"] and c["others"][0]["name"] == "부캐C1", "C: 역방향 꺼도 의심 길드(다른길드) 심층 확인에서 부캐 잡힘")
check(set(s["deep_guilds"]) == {"다른길드", "세번째길드"}, "심층 확인한 의심 길드 = 다른길드, 세번째길드")
check(acc(res, "무소속E")["rep_guildless"] and not acc(res, "무소속E")["dual"], "E: 대표가 길드 없음 (의심 아님)")
unk = {u["name"]: u["reason"] for u in res["unknown"]}
check("무랭킹D" in unk and "유니온" in unk["무랭킹D"], "D: 유니온 랭킹에 없음 → 판별 불가")
check("개명한캐릭" in unk and "찾을 수 없음" in unk["개명한캐릭"], "개명한 캐릭터 → 판별 불가")
check(acc(res, "잠수F")["stale30"] is True and acc(res, "잠수F")["rep"]["away7"], "F: 30일 성장 없음 + 7일 미접속")
check(acc(res, "본캐A")["stale30"] is False, "A: 성장 있음")
check(acc(res, "메이플러03")["forward"] and acc(res, "메이플러03")["check"]["status"] == "confirmed", "메이플러03: 본캐가 세번째길드 → 포착")
check(s["confirmed"] >= 3, f"포착 {s['confirmed']}건")
t = acc(res, "나간대표T")
check(not t["dual"] and t["left_guild"] == "세번째길드" and t["check"]["status"] == "stale",
      "T: 대표 정보엔 세번째길드인데 그 길드 목록엔 없음 → 나간 것으로 보고 의심에서 뺌")
check(max_repeat("id") == 1, "/id: 같은 이름은 한 번만")
check(max_repeat("ranking/union") <= 2, "유니온 랭킹: ocid마다 한 번 (429 재시도만 한 번 더)")
check(max_repeat("character/basic") == 1, "character/basic: 같은 캐릭터·날짜는 한 번만")
check(max_repeat("guild/basic") == 1, "길드원 목록: 길드마다 한 번만 (대조·심층이 같이 씀)")

print("2) 같은 검색을 다시 → 대표·ocid는 캐시, 캐릭터·길드 정보는 다시 최신으로")
res2, calls2, _ = scan(activity=True)
check(calls2.get("ranking/union", 0) == 0 and calls2.get("id", 0) == 0, "유니온 대표·ocid는 전부 캐시")
check(calls2.get("character/basic", 0) > 0 and calls2.get("guild/basic", 0) > 0, "캐릭터·길드 정보는 매번 최신으로 갱신")
check(res2["summary"]["dual"] == s["dual"], "결과 동일")

print("3) 역방향 검색 (상위 50개 길드)")
res3, calls3, job3 = scan(reverse_top=50)
c = acc(res3, "본캐C")
check(c["reverse"] and c["others"][0]["name"] == "부캐C1" and c["others"][0]["guild"] == "다른길드",
      "C: 부캐가 다른 길드 → 잡힘")
check(len(c["others"]) == 1, "의심 길드와 랭킹 길드가 겹쳐도 한 번만")
check(not any(o["name"] == "남의본캐B" for o in acc(res3, "남의본캐B")["others"]), "B: 대표 자신은 '다른 길드 부캐'에 중복 표시 안 함")
check(res3["summary"]["other_guilds"] == 2, "우리 길드는 빼고 다른 길드 2개 검사")
check(max_repeat("ranking/union") <= 1 and max_repeat("guild/basic") == 1, "역방향도 중복 호출 없음")

print("4) 비교 / 저장")
res_shallow, _, _ = scan(deep=False)
check(not acc(res_shallow, "본캐C")["dual"], "심층 끄면 C는 안 잡힘")
sid1 = store.save_scan("스카니아", "단풍잎", "d", {}, res_shallow["summary"], res_shallow)
sid3 = store.save_scan("스카니아", "단풍잎", "d", {}, res3["summary"], res3)
diff = checker.compare(res3, store.previous_scan("스카니아", "단풍잎", sid3))
check(diff["new_dual"] == ["본캐C"] and diff["gone_dual"] == [], "지난번과 비교: 새 의심 = 본캐C")
store.set_settings({"guild": "단풍잎", "nexon_key": "k"})
store.set_settings({"nexon_key": None})
check(store.get_settings() == {"guild": "단풍잎"}, "설정 저장·지우기 (data/guild.db)")

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

r = checker.lookup(nexon.Client("test-key"), store, ["갱신필요S", "갱신필요S부", "남의본캐B"], "단풍잎")
by = {x["name"]: x for x in r["results"]}
check(by["갱신필요S부"]["checks"] == [{"who": "갱신필요S부", "guild": "세번째길드", "status": "stale"}],
      "정보엔 세번째길드인데 그 길드 목록엔 없음 → stale")
check(not by["갱신필요S부"]["dual"] and by["갱신필요S부"]["left_guild"] == "세번째길드",
      "stale이면 나간 것으로 보고 의심에서 뺌 (길드 목록을 더 믿음)")
check(by["남의본캐B"]["checks"][0]["status"] == "confirmed", "남의본캐B: 다른길드 목록에도 있음 → 포착")
check(by["갱신필요S"]["fresh"] and r["summary"]["stale"] == 1 and r["summary"]["confirmed"] == 1, "최신 정보로 조회, 포착 1 · 갱신 필요 1")

print("7-1) 길드 검색 (길드원 조회만)")
mock_nexon.calls.clear()
v = checker.Scanner(nexon.Client("test-key"), store, checker.Job("g", "스카니아", "세번째길드"), "스카니아", "세번째길드").members_view()
names = [m["name"] for m in v["members"]]
check(set(names) == set(mock_nexon.GUILD_MEMBERS["세번째길드"]), "길드원 목록 그대로 (목록에 없는 나간대표T·갱신필요S부는 안 나옴)")
check("ranking/union" not in mock_nexon.calls, "의심 판정 안 함 (유니온 조회 없음)")
check(all(m["fresh"] for m in v["members"]), "길드원 정보는 최신으로")

print("7-2) 잘못 읽은 이름 바로잡기 제안")
v = checker.hangul_variants("쨋뀨유논7")
check("쨋뀨유뇬7" in v and "쨌뀨유논7" in v and len(v) <= checker.SUGGEST_MAX, "헷갈리는 자모 후보 만들기 (ㅗ→ㅛ, 받침 ㅅ→ㅆ)")
r = checker.lookup(nexon.Client("test-key"), store, ["쨋뀨", "췐뿌", "쨋뀨유논7", "쨋뀨유논2"], "")
by = {x["name"]: x for x in r["results"]}
check(by["쨋뀨유논7"]["suggest"][0] == {**by["쨋뀨유논7"]["suggest"][0], "name": "쨋뀨유뇬7", "same_account": True},
      "쨋뀨유논7 → 쨋뀨유뇬7 (같은 계정) 제안")
sg = by["췐뿌"]["suggest"]
check(sg[0]["name"] == "췐쀼" and sg[0]["same_account"], "췐뿌 → 췐쀼 (같은 계정)이 맨 앞")
check(any(x["name"] == "췐부" and not x["same_account"] for x in sg), "남의 캐릭터 췐부는 '다른 계정'으로 표시")
check("suggest" not in by["쨋뀨유논2"], "제대로 읽은 이름은 제안 없음")

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
