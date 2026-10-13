# 스크린샷(메이플 핸즈 캐릭터 목록 등)에서 캐릭터 닉네임을 읽어 오는 곳 — Claude API(이미지 인식)를 쓴다.
# 읽은 이름은 그대로 믿지 않고, 이름 조회(checker.lookup)에서 넥슨 API로 하나하나 확인한다.
# Claude API 키: 서버 환경변수 ANTHROPIC_API_KEY가 있으면 그걸 쓰고, 없으면 화면에서 넣은 키를 이 요청에서만 쓴다.
import base64
import json
import os

import anthropic

MODEL = os.environ.get("CLAUDE_MODEL") or "claude-opus-5-5"  # 비어 있으면 기본값
MAX_IMAGES = 10
MAX_BYTES = 5 * 1024 * 1024  # Claude API 이미지 한 장 한도
MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

PROMPT = """메이플스토리 스크린샷이에요. 주로 '메이플 핸즈+' 앱의 캐릭터 목록 화면이에요.
(핸즈 화면: 위쪽 왼편에 월드 선택(예: 스카니아), 오른편 큰 숫자는 그 월드의 캐릭터 수, 아래로 카드가 3열로 있고
 카드마다 캐릭터 그림 밑에 닉네임, 그 밑에 'Lv. 290' 같은 레벨이 있어요. 스크롤해서 찍은 여러 장이 올 수 있어요.)

화면에 보이는 **캐릭터 닉네임을 하나도 빠짐없이** 읽어 주세요. 여러 장이면 모든 장을 합치고, 겹쳐 찍힌 같은 캐릭터는 한 번만 적어요.

- 닉네임은 한글·영문·숫자로 된 2~12글자예요. **뜻 있는 단어가 아니에요.** '뇬·쀼·뀨·쨋·췐'처럼 평소에 잘 안 쓰는 글자가 많으니,
  흔한 글자(논·뿌·규)로 바꿔 읽지 말고 **보이는 획 그대로** 읽어요.
- 특히 모음의 짧은 획 개수를 하나하나 세어요: ㅗ(1개)/ㅛ(2개), ㅜ(1개)/ㅠ(2개), ㅏ/ㅑ, ㅓ/ㅕ, ㅐ/ㅒ, ㅔ/ㅖ.
  자음도 겹자음인지 봐요: ㄱ/ㄲ, ㄷ/ㄸ, ㅂ/ㅃ, ㅅ/ㅆ, ㅈ/ㅉ, 받침 ㅅ/ㅆ. 숫자·영문은 0/O, 1/l/I를 구별해요.
- 같은 사람의 부캐는 이름이 비슷해요(예: 쨋뀨유뇬1, 쨋뀨유뇬2). 그래도 한 장씩 따로 보고, 다른 카드의 글자를 따라 쓰지 말아요.
- 레벨이 보이면 숫자로, 안 보이면 0. 월드는 화면 위 월드 선택에 적힌 이름, 안 보이면 빈 값. 길드가 보이면 적고 아니면 빈 값.
- 화면 아래에서 잘려서 닉네임이 안 보이는 카드는 적지 않아요. 글자 일부가 가려져 확실하지 않으면 unsure를 true로 해요.
- 앱 이름, 메뉴, 시간, 상태바 글자는 닉네임이 아니에요.
- shown_count: 화면에 적힌 캐릭터 수(핸즈 오른쪽 위 큰 숫자). 안 보이면 0.
- note: 이 화면이 무엇인지와 특이사항(예: 아래쪽 카드가 잘림)을 한국어 한 줄로."""

SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "level": {"type": "integer"},
                    "world": {"type": "string"},
                    "guild": {"type": "string"},
                    "unsure": {"type": "boolean"},
                },
                "required": ["name", "level", "world", "guild", "unsure"],
                "additionalProperties": False,
            },
        },
        "shown_count": {"type": "integer"},
        "note": {"type": "string"},
    },
    "required": ["characters", "shown_count", "note"],
    "additionalProperties": False,
}


class VisionError(Exception):
    pass


def has_server_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def read_names(images, api_key=None):
    """images: [(바이트, media_type)] → {'characters': [...], 'note': str}"""
    key = os.environ.get("ANTHROPIC_API_KEY") or (api_key or "").strip()
    if not key:
        raise VisionError("Claude API 키가 없어요. 화면에 넣거나 서버 환경변수 ANTHROPIC_API_KEY를 설정해 주세요.")
    if not images:
        raise VisionError("이미지를 넣어 주세요.")
    if len(images) > MAX_IMAGES:
        raise VisionError(f"이미지는 한 번에 {MAX_IMAGES}장까지예요.")

    content = []
    for data, media in images:
        if media not in MEDIA_TYPES:
            raise VisionError("PNG·JPG·WEBP·GIF 이미지만 넣을 수 있어요.")
        if len(data) > MAX_BYTES:
            raise VisionError("이미지 한 장이 5MB를 넘어요. 조금 줄여서 넣어 주세요.")
        content.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                    "data": base64.standard_b64encode(data).decode()}})
    content.append({"type": "text", "text": PROMPT})

    client = anthropic.Anthropic(api_key=key)
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # 드물게 거절되면 다른 Claude 모델로 자동으로 다시 시도
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as e:
        raise VisionError("Claude API 키가 올바르지 않아요.") from e
    except anthropic.PermissionDeniedError as e:
        raise VisionError("이 Claude API 키로는 쓸 수 없어요(권한·잔액 확인).") from e
    except anthropic.RateLimitError as e:
        raise VisionError("Claude API 호출이 너무 많아요. 잠시 뒤에 다시 해 주세요.") from e
    except anthropic.BadRequestError as e:
        raise VisionError(f"Claude API가 요청을 거절했어요: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise VisionError(f"Claude API 오류 {e.status_code}. 잠시 뒤에 다시 해 주세요.") from e
    except anthropic.APIConnectionError as e:
        raise VisionError("Claude API에 연결하지 못했어요.") from e

    if resp.stop_reason == "refusal":
        raise VisionError("이미지를 읽지 못했어요(거절됨). 다른 스크린샷으로 해 주세요.")
    if resp.stop_reason == "max_tokens":
        raise VisionError("캐릭터가 너무 많아서 다 못 읽었어요. 이미지를 나눠서 넣어 주세요.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        out = json.loads(text)
    except ValueError as e:
        raise VisionError("이미지 인식 결과를 이해하지 못했어요. 다시 해 주세요.") from e

    # 앞뒤 공백 정리, 같은 이름은 한 번만
    seen, chars = set(), []
    for c in out.get("characters", []):
        n = "".join(str(c.get("name", "")).split())
        if n and n not in seen:
            seen.add(n)
            chars.append({**c, "name": n})
    u = resp.usage
    return {"characters": chars, "note": out.get("note", ""), "shown_count": out.get("shown_count") or 0,
            "usage": {"model": resp.model, "input": u.input_tokens, "output": u.output_tokens}}
