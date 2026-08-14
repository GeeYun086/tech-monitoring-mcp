"""Gemini 호출 공용 래퍼 — analysis/keyword_merge.py(v2: 동의어 병합)와
analysis/relevance_filter.py(v3: 기사-키워드 적합성 판단)가 공유한다.
둘 다 "JSON으로만 답하라"는 동일한 호출 패턴이라 여기 하나로 모았다
(2026-08-13, v3 도입하며 keyword_merge.py에서 분리).
"""

from tech_monitoring.config import settings


def call_gemini_json(prompt: str) -> str:
    """실제 Gemini 호출 — 테스트에서는 호출부 모듈의 call_gemini(이 함수를
    감싸는 얇은 wrapper)를 monkeypatch로 대체한다(google-genai SDK·네트워크·
    API 키에 의존하지 않게)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return response.text
