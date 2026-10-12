# 메이플 이중길드 검사기 — 웹 서버 (화면 + 검사 요청 받기)
import os
import secrets
import threading
import traceback

from flask import Flask, jsonify, render_template, request

import checker
import nexon
from db import Store

app = Flask(__name__)
store = Store()
store.prune()

jobs = {}          # 진행 중이거나 끝난 검사 작업 (서버를 다시 켜면 사라지고, 결과는 DB에 남는다)
jobs_lock = threading.Lock()

REVERSE_CHOICES = {0, 50, 200, 1000, -1}  # 역방향 검사 범위 (길드 랭킹 상위 N개, -1 = 전체)


def err(msg, code=400):
    return jsonify({"error": msg}), code


@app.get("/")
def index():
    return render_template("index.html", worlds=checker.WORLDS)


@app.post("/api/scan")
def start_scan():
    body = request.get_json(silent=True) or {}
    key = (body.get("api_key") or "").strip()
    world = body.get("world")
    guild = (body.get("guild") or "").strip()
    try:
        reverse_top = int(body.get("reverse_top") or 0)
    except ValueError:
        reverse_top = 0
    activity = bool(body.get("activity"))
    if not key:
        return err("넥슨 API 키를 넣어 주세요.")
    if world not in checker.WORLDS:
        return err("월드를 골라 주세요.")
    if not guild:
        return err("길드 이름을 넣어 주세요.")
    if reverse_top not in REVERSE_CHOICES:
        return err("역방향 범위가 올바르지 않아요.")

    with jobs_lock:
        if any(j.status == "running" for j in jobs.values()):
            return err("이미 다른 검사가 돌고 있어요. 끝난 뒤에 다시 눌러 주세요.", 409)
        job = checker.Job(secrets.token_hex(6), world, guild)
        jobs[job.id] = job

    options = {"reverse_top": reverse_top, "activity": activity}
    threading.Thread(target=run_job, args=(job, key, world, guild, options), daemon=True).start()
    return jsonify(job.to_dict())


@app.post("/api/lookup")
def lookup():
    """이름으로 그 사람 조회 (몇 명뿐이라 바로 처리). API 키는 이 요청 안에서만 쓰고 버린다."""
    body = request.get_json(silent=True) or {}
    key = (body.get("api_key") or "").strip()
    world = body.get("world")
    names = [n.strip() for n in str(body.get("names") or "").replace(",", "\n").splitlines() if n.strip()]
    names = list(dict.fromkeys(names))
    if not key:
        return err("넥슨 API 키를 넣어 주세요.")
    if world not in checker.WORLDS:
        return err("월드를 골라 주세요.")
    if not names:
        return err("조회할 캐릭터 이름을 넣어 주세요.")
    if len(names) > checker.LOOKUP_MAX:
        return err(f"한 번에 {checker.LOOKUP_MAX}명까지 조회할 수 있어요.")
    try:
        return jsonify(checker.lookup(nexon.Client(key), store, world, names))
    except nexon.NexonError as e:
        return err(str(e), 502)


def run_job(job, key, world, guild, options):
    try:
        client = nexon.Client(key)
        sc = checker.Scanner(client, store, job, world, guild, options["reverse_top"], options["activity"])
        result = sc.run()
        g = result["guild"]
        job.scan_id = store.save_scan(world, g["name"], g["date"], options, result["summary"], result)
        job.status = "done"
    except checker.Cancelled:
        job.status, job.error = "cancelled", "검사를 멈췄어요."
    except (checker.ScanError, nexon.NexonError) as e:
        job.status, job.error = "error", str(e)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        job.status, job.error = "error", f"예상 못 한 오류: {e}"


@app.get("/api/jobs/current")
def current_job():
    """화면을 새로 열었을 때 돌고 있는 검사가 있으면 이어서 보여주기 위함."""
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), threaded=True)
