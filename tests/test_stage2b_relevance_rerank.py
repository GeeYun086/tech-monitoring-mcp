from tech_monitoring.filters import stage2b_relevance_rerank as mod


class _StubModel:
    """실제 bge-reranker 없이 predict() 호출 형태만 검증하는 스텁."""

    def __init__(self):
        self.received_pairs = None

    def predict(self, pairs):
        self.received_pairs = pairs
        return [float(i) for i in range(len(pairs))]


def test_score_relevance_builds_query_document_pairs_with_content_priority(monkeypatch):
    stub = _StubModel()
    monkeypatch.setattr(mod, "get_reranker", lambda: stub)

    rows = [
        {"title": "제목1", "summary": "요약1", "content": "본문1"},
        {"title": "제목2", "summary": "요약2", "content": None},
    ]
    scores = mod.score_relevance(rows, query="테스트 질의")

    assert scores == [0.0, 1.0]
    queries_used = {q for q, _ in stub.received_pairs}
    assert queries_used == {"테스트 질의"}
    docs = [d for _, d in stub.received_pairs]
    assert "본문1" in docs[0]  # content가 있으면 content 우선
    assert "요약2" in docs[1]  # content가 없으면 summary로 폴백


def test_score_relevance_empty_input_skips_model_load(monkeypatch):
    def _fail():
        raise AssertionError("빈 입력이면 모델을 로드하지 않아야 한다")

    monkeypatch.setattr(mod, "get_reranker", _fail)
    assert mod.score_relevance([]) == []
