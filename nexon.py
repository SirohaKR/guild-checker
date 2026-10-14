# 넥슨 오픈 API(메이플스토리) 클라이언트 — 이 앱이 쓰는 주소만 모아 둔 곳.
# 문서: https://openapi.nexon.com/ko/game/maplestory/
# API 키는 '설정' 화면에서 넣고 data/guild.db에 저장된다. 넥슨에는 요청 헤더로만 보낸다.
import os
import threading
import time

import requests

# 테스트할 때는 NEXON_BASE 환경변수로 가짜 서버 주소를 넣는다 (tests/mock_nexon.py)
BASE = os.environ.get("NEXON_BASE", "https://open.api.nexon.com/maplestory/v1")


class NexonError(Exception):
    def __init__(self, msg, status=None, code=None):
        super().__init__(msg)
        self.status = status   # HTTP 상태 코드 (400, 403, 429 …)
        self.code = code       # 넥슨 오류 이름 (OPENAPI00004 …)


class Client:
    """API 키 하나로 여러 스레드가 같이 쓰는 클라이언트. 호출 수를 세어 둔다."""

    def __init__(self, api_key, retries=5):
        if not api_key:
            raise NexonError("넥슨 API 키를 넣어 주세요.")
        self.api_key = api_key
        self.retries = retries
        self.calls = 0
        self._lock = threading.Lock()
        self._local = threading.local()  # 스레드마다 연결(Session)을 따로 둔다

    def _session(self):
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._local.s = requests.Session()
            s.headers["x-nxopen-api-key"] = self.api_key
        return s

    def get(self, path, params=None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.retries + 1):
            with self._lock:
                self.calls += 1
            try:
                r = self._session().get(f"{BASE}{path}", params=params, timeout=15)
            except requests.RequestException as e:
                if attempt < self.retries:
                    time.sleep(1 + attempt)
                    continue
                raise NexonError(f"넥슨 API 연결 실패: {e}") from e
            # 너무 많이 불렀거나(429) 넥슨 서버가 잠깐 아플 때(5xx)는 쉬었다가 다시
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
            if r.status_code != 200:
                try:
                    err = r.json().get("error", {})
                    name, msg = err.get("name", ""), err.get("message", "")
                except ValueError:
                    name, msg = "", ""
                raise NexonError(f"넥슨 API 오류 {r.status_code} {name} {msg}".strip(),
                                 status=r.status_code, code=name)
            return r.json()
        raise NexonError("넥슨 API 재시도 횟수 초과")

    # ── 실제로 쓰는 주소들 ──

    def ocid(self, name):
        """캐릭터 이름 → ocid. 없는 캐릭터(개명·삭제)면 None."""
        try:
            return self.get("/id", {"character_name": name}).get("ocid")
        except NexonError as e:
            if e.status == 400:
                return None
            raise

    def guild_id(self, guild_name, world):
        try:
            return self.get("/guild/id", {"guild_name": guild_name, "world_name": world}).get("oguild_id")
        except NexonError as e:
            if e.status == 400:
                return None
            raise

    def guild_basic(self, oguild_id, date=None):
        """길드 기본 정보 + 길드원 목록. date를 안 주면 최신 정보."""
        return self.get("/guild/basic", {"oguild_id": oguild_id, "date": date})

    def union_rep(self, ocid, world, date):
        """그 캐릭터 계정의 유니온 랭킹 항목 (대표 캐릭터 이름이 들어 있다). 랭킹에 없으면 None."""
        rows = self.get("/ranking/union", {"date": date, "world_name": world, "ocid": ocid}).get("ranking") or []
        return rows[0] if rows else None

    def character_basic(self, ocid, date=None):
        """캐릭터 기본 정보(길드·레벨·이미지). date를 안 주면 최신 정보 (그 캐릭터로 접속하면 갱신된다)."""
        return self.get("/character/basic", {"ocid": ocid, "date": date})

    def guild_ranking(self, world, date, page):
        """주간 명성치(ranking_type=0) 길드 랭킹 한 페이지."""
        return self.get("/ranking/guild", {"date": date, "world_name": world,
                                           "ranking_type": 0, "page": page}).get("ranking") or []
