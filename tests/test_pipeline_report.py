"""pipeline_report.stage_errors 테스트 — "예외는 안 났지만 실제로는 실패"를
찾아내는 규칙 자체를 검증한다(2026-08-18, 조용한 실패 사고 재발 방지)."""

from tech_monitoring.pipeline_report import stage_errors


def test_no_errors_when_all_items_succeeded():
    result = {"results": [
        {"source": "Techmeme", "fetched": 30, "inserted": 30, "error": None},
        {"source": "TechCrunch", "fetched": 20, "inserted": 20, "error": None},
    ]}

    assert stage_errors(result) == []


def test_reports_partial_failure_with_source_label():
    """수집기는 항목을 "source"로 식별한다 — 어느 사이트가 죽었는지 로그에 남아야 한다."""
    result = {"results": [
        {"source": "Techmeme", "fetched": 30, "inserted": 30, "error": None},
        {"source": "AI타임스", "fetched": 0, "inserted": 0, "error": "ReadTimeout"},
    ]}

    assert stage_errors(result) == ["AI타임스: ReadTimeout"]


def test_reports_relevance_failure_with_fixed_keyword_label():
    """이 사고의 원인이 된 바로 그 모양 — Gemini 429로 전 키워드가 죽은 경우."""
    result = {"results": [
        {"fixed_keyword": "AI 교육", "judged": 0, "relevant": 0,
         "error": "ClientError: 429 RESOURCE_EXHAUSTED"},
        {"fixed_keyword": "비즈니스 실적", "judged": 0, "relevant": 0,
         "error": "ClientError: 429 RESOURCE_EXHAUSTED"},
    ]}

    assert stage_errors(result) == [
        "AI 교육: ClientError: 429 RESOURCE_EXHAUSTED",
        "비즈니스 실적: ClientError: 429 RESOURCE_EXHAUSTED",
    ]


def test_ignores_stages_without_the_error_convention():
    """merge_keywords는 항목별 error 관례가 없다 — 오탐으로 실패 처리하면 안 된다."""
    result = {"results": [{"fixed_keyword": "AI 교육", "groups": 12, "inserted": 12}]}

    assert stage_errors(result) == []


def test_tolerates_unexpected_shapes():
    """단계가 {"results": [...]} 모양이 아니어도 터지지 않아야 한다(테스트 스텁 등)."""
    assert stage_errors({"ok": True}) == []
    assert stage_errors({"results": "not-a-list"}) == []
    assert stage_errors({"results": [None, "junk"]}) == []
    assert stage_errors(None) == []
