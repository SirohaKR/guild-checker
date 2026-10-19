# 업데이트 내역 — CHANGELOG.md(초안)를 관리자가 불러와 다듬어서 [게시]한 것만 화면에 보인다.
import os
import re

DRAFT_PATH = os.environ.get("CHANGELOG_PATH", os.path.join(os.path.dirname(__file__), "CHANGELOG.md"))
VERSION_RE = re.compile(r"^v[\w.]+$")
_SECTION_RE = re.compile(r"^## (v[\w.]+)(?:\s*[—-]\s*([^\n]+))?$(.*?)(?=^## |\Z)", re.M | re.S)


def version_key(v):
    """'v1.2.3' → (1, 2, 3) (버전 순서 비교용)."""
    return tuple(int(n) for n in re.findall(r"\d+", v))


def drafts(path=DRAFT_PATH):
    """CHANGELOG.md → [{version, date, body}] (위에서부터 = 최신순)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    text = re.sub(r"<!--.*?-->", "", text.replace("\r\n", "\n"), flags=re.S)
    return [{"version": m[1], "date": (m[2] or "").strip(), "body": m[3].strip()} for m in _SECTION_RE.finditer(text)]


def admin_list(store):
    """관리 화면용: 초안과 저장·게시한 글을 버전별로 합친 목록 (최신 버전 먼저)."""
    posts = store.changelog_posts()
    drs = {d["version"]: d for d in drafts()}
    out = []
    for v in sorted({*posts, *drs}, key=version_key, reverse=True):
        p = posts.get(v)
        status = ("draft" if not p else "hidden" if p["hidden"] else "published" if p["published"] else "saved")
        out.append({"version": v, "draft": drs.get(v), "post": p, "status": status})
    return out
