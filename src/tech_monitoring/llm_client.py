"""Gemini 호출 공용 래퍼 — analysis/keyword_merge.py(v2: 동의어 병합)와
analysis/relevance_filter.py(v3: 기사-키워드 적합성 판단)가 공유한다.
둘 다 "JSON으로만 답하라"는 동일한 호출 패턴이라 여기 하나로 모았다
(2026-08-13, v3 도입하며 keyword_merge.py에서 분리).

2026-08-13 실사용 중 Gemini 429(RESOURCE_EXHAUSTED)를 겪은 뒤 재시도 로직
추가. 무료 티어는 순간적으로 요청이 몰리면(같은 파이프라인 실행 안에서
고정 키워드 여러 개를 연달아 호출하는 경우 등) 429가 흔하고, 지수
백오프로 몇 초~몇십 초만 기다리면 대부분 풀린다. 반면 일일 한도(RPD) 자체를
다 썼거나 모델 접근 권한이 없는 경우(404 등)는 재시도해도 안 풀리므로,
429가 아닌 에러나 재시도를 다 쓴 429는 그대로 예외를 올려 호출부가
(analysis/keyword_merge.py처럼) "그 주는 이 단계 없이 진행" 같은 판단을
하게 한다.
"""

import time

from tech_monitoring.config import settings

MAX_RETRIES = 5
BASE_DELAY_SECONDS = 2.0  # 2, 4, 8, 16, 32초로 증가(지수 백오프)


def _generate(prompt: str) -> str:
    """실제 SDK 호출 한 번 — 테스트에서 이 함수만 monkeypatch로 대체해
    재시도 루프(call_gemini_json)를 google-genai SDK 없이 검증한다."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return response.text


def _is_rate_limit_error(exc: Exception) -> bool:
    """google.genai.errors.APIError(ClientError 포함)는 .code에 HTTP 상태
    코드를 담아 온다. 그 외 예외 타입은 .code가 없어 getattr 기본값으로
    안전하게 False 처리된다."""
    return getattr(exc, "code", None) == 429


def call_gemini_json(prompt: str) -> str:
    """429는 지수 백오프로 최대 MAX_RETRIES번 재시도한다. 429가 아닌 에러
    (모델 접근 불가 404 등 — 재시도해도 안 풀리는 종류)나 재시도를 다 쓴
    429는 그대로 예외를 올린다."""
    delay = BASE_DELAY_SECONDS
    for attempt in range(MAX_RETRIES):
        try:
            return _generate(prompt)
        except Exception as exc:
            if not _is_rate_limit_error(exc) or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover — 루프가 항상 return/raise로 끝남
