# 우리 길드원 관리 — 웹 서버 (화면 + 검색 요청 받기)
#
# 사용 방식 (처음 들어온 사람이 고름, data/guild.db에 저장)
#  - solo  : 혼자 쓰기. 로그인 없음. 설정·기록은 user 0.
#  - shared: 같이 쓰기. 처음 고른 사람이 관리자 계정을 만들고, 관리자가 사용자 계정을 추가한다.
#            모두 로그인해야 하고, API 키·우리 길드·검색 기록은 계정마다 따로.
import os
import re
import secrets
import threading
import time
import traceback
from datetime import timedelta
from functools import wraps

from flask import Flask, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import checker
import nexon
import vision
from db import Store

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 스크린샷 여러 장
store = Store()
store.prune()

# 로그인 쿠키 서명용 비밀값 — 한 번 만들어 DB에 두면 서버를 다시 켜도 로그인이 유지된다
if not store.get_config("secret_key"):
    store.set_config("secret_key", secrets.token_hex(32))
app.secret_key = store.get_config("secret_key")
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30))

jobs = {}          # 진행 중이거나 끝난 검색 작업 (서버를 다시 켜면 사라지고, 결과는 DB에 남는다)
jobs_lock = threading.Lock()

REVERSE_CHOICES = {0, 50, 200, 1000, -1}  # 역방향 검사 범위 (길드 랭킹 상위 N개, -1 = 전체)
NAME_RE = re.compile(r"^[0-9A-Za-z가-힣_.-]{2,20}$")


def err(msg, code=400, **extra):
    return jsonify({"error": msg, **extra}), code


# ── 사용 방식 · 로그인 ──

def mode():
    return store.get_config("mode")  # None(아직 안 고름) / "solo" / "shared"


def current_user():
    """지금 요청한 사람: solo면 {'id': 0}, shared면 로그인한 계정, 아니면 None."""
    m = mode()
    if m == "solo":
        return {"id": 0, "username": None, "is_admin": True}
    if m == "shared" and session.get("uid") is not None:
        u = store.get_user(uid=session["uid"])
        if u:
            return {"id": u["id"], "username": u["username"], "is_admin": bool(u["is_admin"])}
    return None


def need_user(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not mode():
            return err("처음 사용 방식을 먼저 골라 주세요.", 401, setup=True)
        u = current_user()
        if not u:
            return err("로그인해 주세요.", 401, login=True)
        return fn(u, *a, **kw)
    return wrapper


def need_admin(fn):
    @wraps(fn)
    @need_user
    def wrapper(u, *a, **kw):
        if mode() != "shared" or not u["is_admin"]:
            return err("관리자만 할 수 있어요.", 403)
        return fn(u, *a, **kw)
    return wrapper


def check_new_account(username, password):
    if not NAME_RE.match(username or ""):
        return "아이디는 2~20글자 (한글·영문·숫자·_ . -) 로 해 주세요."
    if len(password or "") < 6:
        return "비밀번호는 6글자 이상으로 해 주세요."
    return None


def login_as(uid):
    session.clear()
    session.permanent = True
    session["uid"] = uid


@app.get("/api/status")
def status():
    u = current_user()
    return jsonify({"mode": mode(), "user": u})


@app.post("/api/setup")
def setup():
    """처음 사용 방식 고르기. 혼자 쓰다가 같이 쓰기로 바꾸는 것도 여기서 (기존 설정·기록은 관리자 계정으로)."""
    body = request.get_json(silent=True) or {}
    want, now_mode = body.get("mode"), mode()
    if now_mode and not (now_mode == "solo" and want == "shared"):
        return err("이미 사용 방식이 정해져 있어요.", 409)
    if want == "solo":
        store.set_config("mode", "solo")
        return status()
    if want != "shared":
        return err("사용 방식을 골라 주세요.")
    username, password = (body.get("username") or "").strip(), body.get("password") or ""
    problem = check_new_account(username, password)
    if problem:
        return err(problem)
    uid = store.add_user(username, generate_password_hash(password), is_admin=True)
    store.move_owner(0, uid)  # 혼자 쓰던 설정·기록이 있으면 관리자 것으로
    store.set_config("mode", "shared")
    login_as(uid)
    return status()


@app.post("/api/login")
def login():
    if mode() != "shared":
        return err("로그인이 필요 없는 사용 방식이에요.")
    body = request.get_json(silent=True) or {}
    u = store.get_user(username=(body.get("username") or "").strip())
    if not u or not check_password_hash(u["pw_hash"], body.get("password") or ""):
        time.sleep(1)  # 비밀번호 마구 넣어 보기 늦추기
        return err("아이디나 비밀번호가 맞지 않아요.", 401)
    login_as(u["id"])
    return status()


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/me/password")
@need_user
def change_my_password(u):
    if mode() != "shared":
        return err("혼자 쓰기에는 비밀번호가 없어요.")
    body = request.get_json(silent=True) or {}
    me = store.get_user(uid=u["id"])
    if not check_password_hash(me["pw_hash"], body.get("current") or ""):
        return err("지금 비밀번호가 맞지 않아요.")
    if len(body.get("new") or "") < 6:
        return err("새 비밀번호는 6글자 이상으로 해 주세요.")
    store.set_password(u["id"], generate_password_hash(body["new"]))
    return jsonify({"ok": True})


# ── 계정 관리 (관리자) ──

@app.get("/api/users")
@need_admin
def users(u):
    return jsonify(store.list_users())


@app.post("/api/users")
@need_admin
def add_user(u):
    body = request.get_json(silent=True) or {}
    username, password = (body.get("username") or "").strip(), body.get("password") or ""
    problem = check_new_account(username, password)
    if problem:
        return err(problem)
    if store.get_user(username=username):
        return err("이미 있는 아이디예요.")
    store.add_user(username, generate_password_hash(password))
    return jsonify(store.list_users())


@app.post("/api/users/<int:uid>/password")
@need_admin
def reset_password(u, uid):
    body = request.get_json(silent=True) or {}
    if len(body.get("password") or "") < 6:
        return err("비밀번호는 6글자 이상으로 해 주세요.")
    if not store.get_user(uid=uid):
        return err("그런 계정이 없어요.", 404)
    store.set_password(uid, generate_password_hash(body["password"]))
    return jsonify({"ok": True})


@app.delete("/api/users/<int:uid>")
@need_admin
def delete_user(u, uid):
    target = store.get_user(uid=uid)
    if not target:
        return err("그런 계정이 없어요.", 404)
    if target["is_admin"]:
        return err("관리자 계정은 지울 수 없어요.")
    store.delete_user(uid)
    return jsonify(store.list_users())


# ── 계정별 설정 (data/guild.db) ──
# 설정 탭 값이 먼저, 없으면 .env(환경변수).
#  - 넥슨 키: 같이 쓰기에서는 .env를 쓰지 않는다 (계정마다 자기 키)
#  - Claude 키: 같이 쓰기에서 .env 키는 '공용 키' — 개인 키를 저장한 계정은 개인 키, 아니면 공용 키

def settings(uid):
    s = store.get_settings(uid)
    solo = mode() == "solo"
    return {
        "nexon_key": s.get("nexon_key") or (os.environ.get("NEXON_API_KEY", "") if solo else ""),
        "claude_key": s.get("claude_key") or os.environ.get("ANTHROPIC_API_KEY", ""),
        "claude_model": s.get("claude_model") or os.environ.get("CLAUDE_MODEL") or vision.DEFAULT_MODEL,
        "world": s.get("world") or "",
        "guild": s.get("guild") or "",
    }


def mask(key):
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else ("••••" if key else "")


def nexon_client(uid):
    key = settings(uid)["nexon_key"]
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
@need_user
def get_settings(u):
    s = settings(u["id"])
    saved = store.get_settings(u["id"])
    shared = mode() == "shared"
    # Claude 키 출처: personal(내가 저장) / shared(같이 쓰기의 공용 .env 키) / env(혼자 쓰기의 .env) / none
    if saved.get("claude_key"):
        claude_source = "personal"
    elif s["claude_key"]:
        claude_source = "shared" if shared else "env"
    else:
        claude_source = "none"
    return jsonify({
        "world": s["world"], "guild": s["guild"], "claude_model": s["claude_model"],
        "nexon_key": mask(s["nexon_key"]),
        # 공용 키는 앞뒤 글자도 보여주지 않는다
        "claude_key": "" if claude_source == "shared" else mask(s["claude_key"]),
        "claude_source": claude_source,
        # 키가 .env에서 온 것인지 (설정 탭에서 지워도 .env 값이 남는다)
        "nexon_from_env": bool(s["nexon_key"]) and not saved.get("nexon_key"),
        "claude_from_env": claude_source == "env",
    })


@app.post("/api/settings")
@need_user
def save_settings(u):
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
    store.set_settings(values, u["id"])
    return get_settings()


@app.post("/api/settings/test")
@need_user
def test_settings(u):
    """넥슨 키로 우리 길드를 실제로 찾아본다."""
    s = settings(u["id"])
    if not (s["world"] and s["guild"]):
        return err("월드와 우리 길드 이름을 먼저 저장해 주세요.")
    try:
        c = nexon_client(u["id"])
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
@need_user
def start_scan(u):
    """mode=ours: 설정의 우리 길드를 심층 검사 / mode=other: 아무 길드의 길드원 조회만 (길드 검색 탭)."""
    body = request.get_json(silent=True) or {}
    s = settings(u["id"])
    kind = body.get("mode") or "ours"
    if kind == "ours":
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
        return err("월드를 골라 주세요." if kind != "ours" else "'설정' 탭에서 월드를 정해 주세요.")
    if not guild:
        return err("길드 이름을 넣어 주세요.")
    if reverse_top not in REVERSE_CHOICES:
        return err("역방향 범위가 올바르지 않아요.")
    try:
        client = nexon_client(u["id"])
    except nexon.NexonError as e:
        return err(str(e))

    with jobs_lock:
        if any(j.status == "running" and j.owner == u["id"] for j in jobs.values()):
            return err("이미 검색이 돌고 있어요. 끝난 뒤에 다시 눌러 주세요.", 409)
        job = checker.Job(secrets.token_hex(6), world, guild)
        job.mode, job.owner = kind, u["id"]
        jobs[job.id] = job

    options = {"mode": kind, "reverse_top": reverse_top, "activity": activity, "focus": focus,
               "deep": kind == "ours"}
    threading.Thread(target=run_job, args=(job, client, world, guild, options), daemon=True).start()
    return jsonify(job.to_dict())


def run_job(job, client, world, guild, options):
    try:
        sc = checker.Scanner(client, store, job, world, guild, options["reverse_top"], options["activity"],
                             deep=options["deep"])
        if options["mode"] == "other":
            # 길드 검색: 길드원 캐릭터 조회만, 기록에 남기지 않음
            job.result = sc.members_view()
            job.status = "done"
            return
        result = sc.run()
        g = result["guild"]
        job.scan_id = store.save_scan(world, g["name"], g["date"], options, result["summary"], result, job.owner)
        job.status = "done"
    except checker.Cancelled:
        job.status, job.error = "cancelled", "검색을 멈췄어요."
    except (checker.ScanError, nexon.NexonError) as e:
        job.status, job.error = "error", str(e)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        job.status, job.error = "error", f"예상 못 한 오류: {e}"


def my_job(u, job_id):
    job = jobs.get(job_id)
    return job if job and job.owner == u["id"] else None


@app.get("/api/jobs/current")
@need_user
def current_job(u):
    """화면을 새로 열었을 때 돌고 있는 검색이 있으면 이어서 보여주기 위함."""
    with jobs_lock:
        running = [j for j in jobs.values() if j.status == "running" and j.owner == u["id"]]
    return jsonify(running[0].to_dict() if running else None)


@app.get("/api/jobs/<job_id>")
@need_user
def job_status(u, job_id):
    job = my_job(u, job_id)
    return jsonify(job.to_dict()) if job else err("그런 작업이 없어요.", 404)


@app.post("/api/jobs/<job_id>/cancel")
@need_user
def job_cancel(u, job_id):
    job = my_job(u, job_id)
    if not job:
        return err("그런 작업이 없어요.", 404)
    job.cancel_flag = True
    return jsonify(job.to_dict())


@app.get("/api/scans")
@need_user
def scans(u):
    return jsonify(store.list_scans(u["id"]))


@app.get("/api/scans/<int:scan_id>")
@need_user
def scan(u, scan_id):
    s = store.get_scan(scan_id, u["id"])
    if not s:
        return err("그런 검색 기록이 없어요.", 404)
    s["diff"] = checker.compare(s["result"], store.previous_scan(s["world"], s["guild"], s["id"], u["id"]))
    return jsonify(s)


@app.delete("/api/scans/<int:scan_id>")
@need_user
def scan_delete(u, scan_id):
    store.delete_scan(scan_id, u["id"])
    return jsonify({"ok": True})


# ── 캐릭터명 검색 ──

@app.post("/api/lookup")
@need_user
def lookup(u):
    """이름으로 조회 (가입 신청자 확인용). 우리 길드는 설정 값."""
    body = request.get_json(silent=True) or {}
    names = [n.strip() for n in str(body.get("names") or "").replace(",", "\n").splitlines() if n.strip()]
    names = list(dict.fromkeys(names))
    if not names:
        return err("조회할 캐릭터 이름을 넣어 주세요.")
    if len(names) > checker.LOOKUP_MAX:
        return err(f"한 번에 {checker.LOOKUP_MAX}명까지 조회할 수 있어요.")
    try:
        return jsonify(checker.lookup(nexon_client(u["id"]), store, names, settings(u["id"])["guild"]))
    except nexon.NexonError as e:
        return err(str(e), 502)


@app.post("/api/read-image")
@need_user
def read_image(u):
    """스크린샷에서 캐릭터 닉네임 읽기 (Claude API)."""
    files = request.files.getlist("images")
    s = settings(u["id"])
    try:
        imgs = [(f.read(), f.mimetype) for f in files if f]
        return jsonify(vision.read_names(imgs, s["claude_key"], s["claude_model"]))
    except vision.VisionError as e:
        return err(str(e))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), threaded=True)
