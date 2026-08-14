"""llm_client.py의 429 재시도(지수 백오프) 로직 테스트. 실제 google-genai
SDK·네트워크 없이 _generate만 스텁으로 대체한다."""

import pytest

from tech_monitoring import llm_client


class _FakeAPIError(Exception):
    """google.genai.errors.APIError 흉내 — .code 속성만 있으면 충분."""

    def __init__(self, code):
        self.code = code
        super().__init__(f"fake error {code}")


def test_call_gemini_json_returns_immediately_on_success(monkeypatch):
    monkeypatch.setattr(llm_client, "_generate", lambda prompt: "ok")
    assert llm_client.call_gemini_json("hi") == "ok"


def test_call_gemini_json_retries_429_then_succeeds(monkeypatch):
    calls = []
    sleeps = []

    def fake_generate(prompt):
        calls.append(1)
        if len(calls) < 3:
            raise _FakeAPIError(429)
        return "ok"

    monkeypatch.setattr(llm_client, "_generate", fake_generate)
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: sleeps.append(s))

    result = llm_client.call_gemini_json("hi")

    assert result == "ok"
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]  # 지수 백오프: 2초 → 4초


def test_call_gemini_json_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(llm_client, "_generate", lambda prompt: (_ for _ in ()).throw(_FakeAPIError(429)))
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)

    with pytest.raises(_FakeAPIError):
        llm_client.call_gemini_json("hi")


def test_call_gemini_json_does_not_retry_non_429_errors(monkeypatch):
    """404(모델 접근 불가) 같은 에러는 재시도해도 안 풀리므로 즉시 올려야 한다."""
    calls = []

    def fake_generate(prompt):
        calls.append(1)
        raise _FakeAPIError(404)

    monkeypatch.setattr(llm_client, "_generate", fake_generate)
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("sleep 호출됨")))

    with pytest.raises(_FakeAPIError):
        llm_client.call_gemini_json("hi")

    assert len(calls) == 1  # 재시도 없이 첫 시도에서 바로 실패


def test_call_gemini_json_does_not_retry_errors_without_code_attribute(monkeypatch):
    """.code가 없는 일반 예외(예: 네트워크 오류)는 429 여부를 판단할 수 없으니
    안전하게 "재시도 대상 아님"으로 취급하고 즉시 올린다."""
    monkeypatch.setattr(llm_client, "_generate", lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("sleep 호출됨")))

    with pytest.raises(RuntimeError):
        llm_client.call_gemini_json("hi")
