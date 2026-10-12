# 인계 메모 (2026-10-12 퇴근 전)

다른 컴퓨터에서 이어갈 때 Claude에게 이 파일과 `README.md`를 먼저 읽게 하세요. 사용자 작업 방식·취향·GitHub 주의사항은 **`SirohaKR/maple-ledger`의 `HANDOFF.md` 1번**에 있어요(그라데이션 금지, 파이썬 모름, 회사 PC의 전역 git은 회사 계정이라 저장소별 로컬 설정 사용 등).

## 상태
- 기능은 다 들어가 있음: 정방향/역방향(길드 랭킹 상위 N개)/판별 불가/30일 성장 없음, 백그라운드 검사 + 진행률, 캐시, 지난 검사 저장·비교, 라이트/다크.
- `python tests/test_checker.py` (가짜 넥슨 서버) **26개 모두 통과** (2026-10-12 17:40).
- API 키: 사용자 요청으로 **어디에도 저장하지 않음** — 브라우저 localStorage 저장을 없앴고(예전 `gc_key`는 지움), 서버는 검사 작업 메모리에서만 사용. 키는 헤더로만 보내서 오류 메시지·URL에 안 남음.

## 남은 일
1. **실제 키로 NAS에서 검사해 보기** — 응답 필드 형식이 명세와 다르면 `checker.py` 조정. 특히 `/ranking/union?ocid=`가 `ranking[0]`에 그 계정을 주는지, 월드 이름 '헬리오스/핼리오스' 처리.
2. 브라우저 화면 최종 확인(디자인 취향: 단색, 꽉 차는 레이아웃, 캐릭터 이미지 크게, 라이트/다크).
3. (선택) 디스코드 알림, 정기 검사.

## 참고
- 비슷한 사이트: https://in-fo.github.io/MapleStoryGuildManagement/ (정방향만, 길드원 1명당 8회 호출). 이 프로젝트는 중복 호출을 없앰.
- 넥슨 API 명세 YAML: `https://openapi.nexon.com/static/api/maplestory/18_ko_script20260622043839.yaml`(랭킹), `16_ko_script20260723040144.yaml`(길드), `14_ko_script20260917040003.yaml`(캐릭터).
- NAS: `/volume1/docker/guild-checker`, 포트 5080.
