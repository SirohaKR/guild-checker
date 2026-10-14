# 우리 길드원 관리

## 개요

메이플스토리 길드의 이중길드 관리를 좀 더 편하게 하려고 만든 웹 페이지입니다.

넥슨 Open API로 길드원을 조회해서, 본캐나 부캐가 다른 길드에 있는 것 같은 사람을 찾아 줍니다. 가입 신청자의 메이플 핸즈 캐릭터 목록 스크린샷을 올려서 한 번에 확인하는 기능도 있습니다.

다만 넥슨 쪽 정보는 캐릭터로 접속해야 갱신되고, 같은 계정을 묶는 데 쓰는 유니온 랭킹은 하루 전 데이터입니다. 그래서 결과가 실제와 다를 수 있습니다. 여기서 나오는 결과는 확정이 아니라 **한 번 확인해 볼 만한 경고** 정도로 봐 주세요.

## 개발 환경

- 언어: Python 3.11 이상 (Flask), 화면은 HTML / CSS / JavaScript
- DB: SQLite (`data/guild.db` 파일 하나)
- 실행: Docker 또는 Python으로 바로 실행

## 로컬에서 실행하기

먼저 저장소를 받습니다.

```bash
git clone https://github.com/SirohaKR/guild-checker.git
cd guild-checker
```

### Docker로 실행

```bash
mkdir -p data
docker compose up -d --build
```

브라우저에서 `http://localhost:5080` 으로 들어가면 됩니다. 끌 때는 `docker compose down` 입니다.

### Python으로 바로 실행

```bash
python -m venv .venv
.venv\Scripts\activate        # 맥/리눅스는 source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://localhost:5000` 으로 들어가면 됩니다.

### 처음 들어가면

혼자 쓸지, 다른 사람과 같이 쓸지 고르는 화면이 나옵니다.
같이 쓰기를 고르면 그때 만든 계정이 관리자가 되고, 관리자가 다른 사람 계정을 추가해 주는 방식입니다.
그다음 **설정** 탭에서 API 키와 우리 길드 이름을 넣으면 바로 쓸 수 있습니다.

## .env 파일과 data 폴더

### data 폴더

설정, 계정, 검색 기록은 모두 `data/guild.db` 에 저장됩니다.

- Docker로 실행할 때는 `data` 폴더를 **먼저 만들어 두세요** (`mkdir -p data`). 시놀로지 같은 NAS에서는 폴더가 없으면 실행이 안 됩니다.
- Python으로 바로 실행하면 폴더가 알아서 만들어집니다.

이 파일에는 API 키가 들어가니 다른 사람에게 주지 마세요. Git에는 올라가지 않게 되어 있습니다.

### .env 파일

꼭 필요하진 않습니다. API 키는 웹의 **설정** 탭에서 넣으면 됩니다.
혼자 쓰기 모드에서 키를 서버에 미리 넣어 두고 싶을 때만 씁니다. 같이 쓰기 모드에서는 계정마다 자기 키를 넣어야 해서 `.env` 키는 쓰지 않습니다.

```bash
cp .env.example .env
```

그다음 `.env` 를 열어서 필요한 값만 채웁니다.

```
NEXON_API_KEY=넥슨 API 키
ANTHROPIC_API_KEY=Claude API 키 (스크린샷 읽기를 쓸 때만)
CLAUDE_MODEL=claude-opus-5-5
```

`.env` 를 바꾼 뒤에는 `docker compose up -d` 로 다시 띄워야 적용됩니다.

## 필요한 API 키

| 키 | 필요 여부 | 받는 곳 |
|---|---|---|
| 넥슨 Open API 키 | 필수 | https://openapi.nexon.com/ko/ |
| Claude API 키 | 선택 (스크린샷에서 닉네임 읽기) | https://console.anthropic.com/ |

- **넥슨**: 로그인 → 내 애플리케이션 → 애플리케이션 등록 → 게임은 메이플스토리, 종류는 **서비스 단계**로 등록하면 `live_` 로 시작하는 키가 나옵니다. 무료입니다. 개발 단계 키(`test_`)는 하루 호출 한도가 너무 적어서 길드 전체를 검색하기엔 부족하니 서비스 단계로 받아 주세요. 서비스 단계는 서비스 이름, 개발 환경, URL, 설명, 대표 이미지를 넣어야 합니다.
- **Claude**: 가입 후 결제 수단을 등록하고 API Keys 메뉴에서 만들면 됩니다. 쓴 만큼 요금이 나옵니다. 스크린샷 읽기를 안 쓰면 없어도 됩니다.
