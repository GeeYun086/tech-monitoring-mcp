from tech_monitoring.filters.stage2_relevance import _build_tsquery, topic_embedding_text


def test_uses_description_when_keywords_empty():
    """v2.0: 키워드 미지정이 정상 — 'AX 시장' 의미 서술로 관련도를 판단한다."""
    text = topic_embedding_text("AX 시장 전체", "AI 전환 시장 전반", [])
    assert text == "AX 시장 전체: AI 전환 시장 전반"


def test_keywords_appended_when_present():
    text = topic_embedding_text("AX", "설명", ["a", "b"])
    assert text == "AX: 설명: a b"


def test_name_only_when_nothing_else():
    assert topic_embedding_text("AX", None, []) == "AX"


def test_empty_keywords_yield_empty_tsquery():
    """키워드가 없으면 BM25 경로는 비고 dense(의미) 경로만 동작한다."""
    assert _build_tsquery([]) == ""
