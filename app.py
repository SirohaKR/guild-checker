# 우리 길드원 관리 — 웹 서버 (화면 + 검색 요청 받기)
import os
import secrets
import threading
import traceback

from flask import Flask, jsonify, render_template, request

import checker
import nexon
import vision
from db import Store

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 스크린샷 여러 장
store = Store()
store.prune()

jobs = {}          # 진행 중이거나 끝난 검색 작업 (서버를 다시 켜면 사라지고, 결과는 DB에 남는다)
jobs_lock = threading.Lock()

REVERSE_CHOICES = {0, 50, 200, 1000, -1}  # 역방향 검사 범위 (길드 랭킹 상위 N개, -1 = 전체)


def err(msg, code=400):
    return jsonify({"error": msg}), code


# ── 설정 (data/guild.db에 저장 — 로컬에서 돌려도 같은 방식) ──
# API 키는 '설정' 탭 값이 먼저, 없으면 .env(환경변수) 값을 쓴다.

def settings():
    s = store.get_settings()
    return {
        "nexon_key": s.get("nexon_key") or os.environ.get("NEXON_API_KEY", ""),
        "claude_key": s.get("claude_key") or os.environ.get("ANTHROPIC_API_KEY", ""),
        "claude_model": s.get("claude_model") or os.environ.get("CLAUDE_MODEL") or vision.DEFAULT_MODEL,
        "world": s.get("world") or "",
        "guild": s.get("guild") or "",
    }


def mask(key):
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else ("••••" if key else "")


def nexon_client():
    key = settings()["nexon_key"]
    if not key:
        raise nexon.NexonError("넥슨 API 키가 없어요. '설정' 탭에서 넣어 주세요.")
    return nexon.Client(key)


@app.context_processor
def asset_version():
    # 업데이트 뒤 브라우저가 예전 app.js·style.css를 캐시에서 쓰지 않도록 파일 수정 시각을 주소에 붙인다
    st = os.path.join(app.root_path, "static")
    return {"asset_v": int(max(os.path.getmtime(os.path.join(st, f)) for f in ("app.js", "style.css", "logo.svg")))}


@app.get("/")
def index():
    return render_template("index.html", worlds=checker.WORLDS, models=vision.MODELS)


@app.get("/api/settings")
def get_settings():
    s = settings()
    saved = store.get_settings()
    return jsonify({
        "world": s["world"], "guild": s["guild"], "claude_model": s["claude_model"],
        "nexon_key": mask(s["nexon_key"]), "claude_key": mask(s["claude_key"]),
        # 키가 .env에서 온 것인지 (설정 탭에서 지워도 .env 값이 남는다)
        "nexon_from_env": bool(s["nexon_key"]) and not saved.get("nexon_key"),
        "claude_from_env": bool(s["claude_key"]) and not saved.get("claude_key"),
    })


@app.post("/api/settings")
def save_settings():
    body = request.get_json(silent=True) or {}
    values = {}
    if "world" in body:
        if body["world"] and body["world"] not in checker.WORLDS:
            return err("월드를 골라 주세요.")
        values["world"] = body["world"] or None
    if "guild" in body:
        values["guild"] = (body["guild"] or "").strip() or None
    if "claude_model" in body:
        if body["claude_model"] not in vision.MODELS:
            return err("모델을 골라 주세요.")
        values["claude_model"] = body["claude_model"]
    # 키는 새로 넣은 경우에만 바꾸고, clear_*가 오면 지운다 (화면에는 가려진 값만 보낸다)
    for k in ("nexon_key", "claude_key"):
        v = (body.get(k) or "").strip()
        if v:
            values[k] = v
        elif body.get("clear_" + k):
            values[k] = None
    store.set_settings(values)
    return get_settings()


@app.post("/api/settings/test")
def test_settings():
    """넥슨 키로 우리 길드를 실제로 찾아본다."""
    s = settings()
    if not (s["world"] and s["guild"]):
        return err("월드와 우리 길드 이름을 먼저 저장해 주세요.")
    try:
        c = nexon_client()
        gid = None
        for w in checker.WORLD_ALIASES.get(s["world"], [s["world"]]):
            gid = c.guild_id(s["guild"], w)
            if gid:
                b = c.guild_basic(gid)
                return jsonify({"ok": True, "guild": b.get("guild_name"), "members": len(b.get("guild_member") or []),
                                "master": b.get("guild_master_name")})
        return err(f"'{s['world']}'에서 '{s['guild']}' 길드를 찾지 못했어요.")
    except nexon.NexonError as e:
        return err(str(e), 502)


# ── 길드원 검색 / 길드 검색 (백그라운드 작업) ──

@app.post("/api/scan")
def start_scan():
    """mode=ours: 설정의 우리 길드를 심층 검사 / mode=other: 아무 길드나 (길드 검색 탭)."""
    body = request.get_json(silent=True) or {}
    s = settings()
    mode = body.get("mode") or "ours"
    if mode == "ours":
        world, guild = s["world"], s["guild"]
        if not guild:
            return err("'설정' 탭에서 우리 길드를 먼저 정해 주세요.")
    else:
        world, guild = body.get("world"), (body.get("guild") or "").strip()
    try:
        reverse_top = int(body.get("reverse_top") or 0)
    except ValueError:
        reverse_top = 0
    activity = bool(body.get("activity"))
    focus = [str(f) for f in (body.get("focus") or []) if f][:20]
    if world not in checker.WORLDS:
        return err("월드를 골라 주세요." if mode != "ours" else "'설정' 탭에서 월드를 정해 주세요.")
    if not guild:
        return err("길드 이름을 넣어 주세요.")
    if reverse_top not in REVERSE_CHOICES:
        return err("역방향 범위가 올바르지 않아요.")
    try:
        client = nexon_client()
    except nexon.NexonError as e:
        return err(str(e))

    with jobs_lock:
        if any(j.status == "running" for j in jobs.values()):
            return err("이미 다른 검색이 돌고 있어요. 끝난 뒤에 다시 눌러 주세요.", 409)
        job = checker.Job(secrets.token_hex(6), world, guild)
        job.mode = mode
        jobs[job.id] = job

    options = {"mode": mode, "reverse_top": reverse_top, "activity": activity, "focus": focus,
               "deep": mode == "ours"}
    threading.Thread(target=run_job, args=(job, client, world, guild, options), daemon=True).start()
    return jsonify(job.to_dict())


def run_job(job, client, world, guild, options):
    try:
        sc = checker.Scanner(client, store, job, world, guild, options["reverse_top"], options["activity"],
                             deep=options["deep"])
        result = sc.run()
        g = result["guild"]
        job.scan_id = store.save_scan(world, g["name"], g["date"], options, result["summary"], result)
        job.status = "done"
    except checker.Cancelled:
        job.status, job.error = "cancelled", "검색을 멈췄어요."
    except (checker.ScanError, nexon.NexonError) as e:
        job.status, job.error = "error", str(e)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        job.status, job.error = "error", f"예상 못 한 오류: {e}"


@app.get("/api/jobs/current")
def current_job():
    """화면을 새로 열었을 때 돌고 있는 검색이 있으면 이어서 보여주기 위함."""
    with jobs_lock:
        running = [j for j in jobs.values() if j.status == "running"]
    return jsonify(running[0].to_dict() if running else None)


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    return jsonify(job.to_dict()) if job else err("그런 작업이 없어요.", 404)


@app.post("/api/jobs/<job_id>/cancel")
def job_cancel(job_id):
    job = jobs.get(job_id)
    if not job:
        return err("그런 작업이 없어요.", 404)
    job.cancel_flag = True
    return jsonify(job.to_dict())


@app.get("/api/scans")
def scans():
    return jsonify(store.list_scans())


@app.get("/api/scans/<int:scan_id>")
def scan(scan_id):
    s = store.get_scan(scan_id)
    if not s:
        return err("그런 검사 기록이 없어요.", 404)
    s["diff"] = checker.compare(s["result"], store.previous_scan(s["world"], s["guild"], s["id"]))
    return jsonify(s)


@app.delete("/api/scans/<int:scan_id>")
def scan_delete(scan_id):
    store.delete_scan(scan_id)
    return jsonify({"ok": True})


# ── 캐릭터명 검색 ──

@app.post("/api/lookup")
def lookup():
    """이름으로 조회 (가입 신청자 확인용). 우리 길드는 설정 값."""
    body = request.get_json(silent=True) or {}
    names = [n.strip() for n in str(body.get("names") or "").replace(",", "\n").splitlines() if n.strip()]
    names = list(dict.fromkeys(names))
    if not names:
        return err("조회할 캐릭터 이름을 넣어 주세요.")
    if len(names) > checker.LOOKUP_MAX:
        return err(f"한 번에 {checker.LOOKUP_MAX}명까지 조회할 수 있어요.")
    try:
        return jsonify(checker.lookup(nexon_client(), store, names, settings()["guild"]))
    except nexon.NexonError as e:
        return err(str(e), 502)


@app.post("/api/read-image")
def read_image():
    """스크린샷에서 캐릭터 닉네임 읽기 (Claude API)."""
    files = request.files.getlist("images")
    s = settings()
    try:
        imgs = [(f.read(), f.mimetype) for f in files if f]
        return jsonify(vision.read_names(imgs, s["claude_key"], s["claude_model"]))
    except vision.VisionError as e:
        return err(str(e))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), threaded=True)
