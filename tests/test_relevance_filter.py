"""analysis/relevance_filter.py 테스트. 실제 Gemini 호출·DB 없이 call_gemini와
conn.cursor()를 monkeypatch/스텁으로 대체한다."""

import json

from tech_monitoring.analysis import relevance_filter as rf


# ---- parse_relevant_indices ----

def test_parse_relevant_indices_happy_path():
    raw = json.dumps({"relevant_indices": [0, 2]})
    assert rf.parse_relevant_indices(raw, num_articles=3) == {0, 2}


def test_parse_relevant_indices_returns_empty_set_on_invalid_json():
    assert rf.parse_relevant_indices("JSON 아님", num_articles=3) == set()


def test_parse_relevant_indices_drops_out_of_range_indices():
    """환각 방지: 기사 목록 범위 밖 번호는 버려야 한다."""
    raw = json.dumps({"relevant_indices": [0, 99, -1]})
    assert rf.parse_relevant_indices(raw, num_articles=3) == {0}


def test_parse_relevant_indices_ignores_non_list_field():
    raw = json.dumps({"relevant_indices": "전부 관련 있음"})
    assert rf.parse_relevant_indices(raw, num_articles=3) == set()


def test_parse_relevant_indices_ignores_non_int_entries():
    raw = json.dumps({"relevant_indices": [0, "1", 2.5, True]})
    assert rf.parse_relevant_indices(raw, num_articles=3) == {0}


# ---- build_prompt ----

def test_build_prompt_includes_keyword_and_all_article_titles():
    prompt = rf.build_prompt("AX 시장", [
        {"title": "기사A", "snippet": "요약A"}, {"title": "기사B", "snippet": None},
    ])
    assert "AX 시장" in prompt
    assert "0. 기사A | 요약A" in prompt
    assert "1. 기사B | " in prompt


# ---- judge_keyword ----

def test_judge_keyword_returns_zero_when_no_articles():
    result = rf.judge_keyword(conn=None, run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"}, articles=[])
    assert result == {"fixed_keyword": "AX 시장", "judged": 0, "relevant": 0,
                      "method": None, "error": None}


def test_judge_keyword_saves_relevance_for_every_article(monkeypatch):
    saved = []

    def fake_save(conn, run_id, fixed_keyword_id, articles, relevant_indices, scores=None):
        saved.append((fixed_keyword_id, relevant_indices))
        return len(relevant_indices)

    monkeypatch.setattr(rf, "call_gemini", lambda prompt: json.dumps({"relevant_indices": [1]}))
    monkeypatch.setattr(rf, "_save_relevance", fake_save)

    articles = [{"id": 10, "title": "무관 기사"}, {"id": 11, "title": "관련 기사"}]
    result = rf.judge_keyword(conn=None, run_id=1, fixed_keyword={"id": 5, "keyword": "AX 시장"}, articles=articles)

    # 모델이 없으면 Gemini로 폴백하고, 어느 쪽을 썼는지 method에 남는다.
    assert result == {"fixed_keyword": "AX 시장", "judged": 2, "relevant": 1,
                      "method": "gemini", "error": None}
    assert saved == [(5, {1})]


def test_judge_keyword_falls_back_safely_on_gemini_failure(monkeypatch):
    """Gemini 호출 자체가 실패해도(네트워크·rate limit 등) 파이프라인이 죽지 않고
    error 필드로 보고해야 한다(collectors/search_engine.py의 실패 격리 원칙과 동일)."""
    def fake_call_gemini(prompt):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(rf, "call_gemini", fake_call_gemini)

    result = rf.judge_keyword(
        conn=None, run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"},
        articles=[{"id": 1, "title": "기사"}],
    )

    assert result["error"] == "RuntimeError: 429 RESOURCE_EXHAUSTED"
    assert result["judged"] == 0
    assert result["relevant"] == 0


def test_judge_keyword_falls_back_to_no_relevant_on_unparseable_response(monkeypatch):
    monkeypatch.setattr(rf, "call_gemini", lambda prompt: "이상한 응답")
    saved = []
    monkeypatch.setattr(rf, "_save_relevance",
                        lambda conn, run_id, kw_id, articles, indices, scores=None: saved.append(indices) or 0)

    result = rf.judge_keyword(
        conn=None, run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"},
        articles=[{"id": 1, "title": "기사"}],
    )

    assert result["relevant"] == 0
    assert saved == [set()]


# ---- judge_all ----

def test_judge_all_reuses_same_articles_across_keywords(monkeypatch):
    """수집(fetch_collected_articles)은 한 번만, 판단은 고정 키워드 수만큼 이뤄져야 한다."""
    fetch_calls = []
    judge_calls = []

    monkeypatch.setattr(rf, "fetch_collected_articles", lambda conn, run_id: fetch_calls.append(run_id) or ["a1", "a2"])
    monkeypatch.setattr(rf, "get_active_fixed_keywords", lambda conn: [
        {"id": 1, "keyword": "AX 시장"}, {"id": 2, "keyword": "교육"},
    ])
    monkeypatch.setattr(rf, "judge_keyword", lambda conn, run_id, kw, articles, bundle=None: judge_calls.append((kw["id"], articles)) or {"ok": kw["id"]})
    # 모델이 없으면 판단 자체를 건너뛰므로(2026-08-19), 이 테스트가 재사용을
    # 검증할 수 있도록 모델이 있는 상황을 만든다.
    monkeypatch.setattr("tech_monitoring.relevance_model.load_model", lambda *a, **k: {"method": "tfidf"})

    results = rf.judge_all(conn=None, run_id=7)

    assert fetch_calls == [7]  # 한 번만 수집
    assert judge_calls == [(1, ["a1", "a2"]), (2, ["a1", "a2"])]
    assert results == [{"ok": 1}, {"ok": 2}]


# ---- fetch_relevant_articles / fetch_collected_articles (DB 조회 shape) ----

class _FakeCursor:
    def __init__(self, rows, columns):
        self._rows = rows
        self.description = [type("Col", (), {"name": c}) for c in columns]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows, columns):
        self._rows = rows
        self._columns = columns

    def cursor(self):
        return _FakeCursor(self._rows, self._columns)


def test_fetch_relevant_articles_returns_title_snippet_source_domain_shape():
    """keyword_merge.run_for_all_keywords(fetch_rows=...)에 그대로 꽂아 쓰려면
    fetch_search_results와 동일한 컬럼 모양이어야 한다."""
    conn = _FakeConn(rows=[("제목", "요약", "example.com")], columns=["title", "snippet", "source_domain"])

    rows = rf.fetch_relevant_articles(conn, run_id=1, fixed_keyword_id=1)

    assert rows == [{"title": "제목", "snippet": "요약", "source_domain": "example.com"}]


# ---- 로컬 분류기 경로(2026-08-18) ----

def test_judge_keyword_uses_classifier_when_model_is_available(monkeypatch):
    """모델이 있으면 Gemini를 아예 호출하지 않아야 한다 — 이 단계를 API에서
    떼어내는 게 목적이므로, 조용히 Gemini로 새면 목적이 무너진다."""
    gemini_calls = []
    monkeypatch.setattr(rf, "call_gemini", lambda prompt: gemini_calls.append(prompt) or "{}")
    saved = []
    monkeypatch.setattr(rf, "_save_relevance",
                        lambda conn, run_id, kw_id, arts, idx, scores=None: saved.append((idx, scores)) or 0)
    # 0.9 / 0.2 → 기준선(0.5) 위인 0번만 관련 있음으로 잡히고, 확률은 그대로 저장된다.
    monkeypatch.setattr(rf, "score_with_classifier", lambda bundle, kw, arts: [0.9, 0.2])

    result = rf.judge_keyword(
        conn=None, run_id=1, fixed_keyword={"id": 1, "keyword": "교육"},
        articles=[{"id": 1, "title": "관련"}, {"id": 2, "title": "무관"}],
        bundle={"method": "tfidf"},
    )

    assert gemini_calls == []
    assert result["method"] == "classifier:tfidf"
    assert result["relevant"] == 1
    assert saved == [({0}, [0.9, 0.2])]   # 낮은 점수도 버리지 않고 함께 저장(007)


def test_score_with_classifier_returns_probabilities_with_market_keyword(monkeypatch):
    """분류기 입력은 학습 때와 같은 형식이어야 한다 — 고정 키워드가 함께
    들어가야 한다. 그리고 **확률을 그대로** 돌려줘야 한다(007) — 여기서 잘라
    집합만 넘기면 순위 정보가 사라진다."""
    seen = {}

    def fake_predict_proba(bundle, rows):
        seen["rows"] = rows
        return [0.9, 0.49, 0.5]

    monkeypatch.setattr(
        "tech_monitoring.relevance_model.predict_proba", fake_predict_proba, raising=True,
    )

    scores = rf.score_with_classifier(
        {"method": "tfidf"}, {"id": 1, "keyword": "교육"},
        [{"id": 1, "title": "가", "snippet": "요약"}, {"id": 2, "title": "나"}, {"id": 3, "title": "다"}],
    )

    assert scores == [0.9, 0.49, 0.5]
    assert all(r["fixed_keyword"] == "교육" for r in seen["rows"])
    assert seen["rows"][0]["title"] == "가"


def test_judge_all_skips_judging_when_no_model_trained(monkeypatch):
    """모델이 없으면 판단을 건너뛴다(2026-08-19 결정) — 라벨이 없는 첫 주에
    LLM을 부를 이유가 없다. 점수가 없으면 화면이 최신순 전체를 보여주고,
    그 주 결과물은 사람이 라벨링한 것 자체가 된다. 실패가 아니므로 error는
    비어 있어야 한다(담으면 파이프라인이 매주 실패로 마감된다)."""
    monkeypatch.setattr("tech_monitoring.relevance_model.load_model", lambda *a, **k: None)
    monkeypatch.setattr(rf, "get_active_fixed_keywords", lambda conn: [{"id": 1, "keyword": "교육"}])
    fetched, judged = [], []
    monkeypatch.setattr(rf, "fetch_collected_articles", lambda conn, run_id: fetched.append(run_id) or [])
    monkeypatch.setattr(rf, "judge_keyword",
                        lambda conn, run_id, kw, arts, bundle=None: judged.append(kw) or {"ok": True})

    (result,) = rf.judge_all(conn=None, run_id=1)

    assert result["method"] == "skipped:모델 없음"
    assert result["error"] is None
    assert (fetched, judged) == ([], [])   # 기사도 안 읽고 판단도 안 한다


def test_judge_all_can_still_use_gemini_when_explicitly_allowed(monkeypatch):
    """Gemini 경로는 지우지 않고 비교 실험용으로 남겨둔다."""
    monkeypatch.setattr("tech_monitoring.relevance_model.load_model", lambda *a, **k: None)
    monkeypatch.setattr(rf, "fetch_collected_articles", lambda conn, run_id: [{"id": 1, "title": "기사"}])
    monkeypatch.setattr(rf, "get_active_fixed_keywords", lambda conn: [{"id": 1, "keyword": "교육"}])
    bundles = []
    monkeypatch.setattr(rf, "judge_keyword",
                        lambda conn, run_id, kw, arts, bundle=None: bundles.append(bundle) or {"ok": True})

    rf.judge_all(conn=None, run_id=1, allow_llm_fallback=True)

    assert bundles == [None]


def test_judge_all_loads_model_once_for_all_keywords(monkeypatch):
    """키워드마다 모델을 다시 불러오면 임베딩 방식일 때 로드가 반복돼 느려진다."""
    load_calls = []
    monkeypatch.setattr("tech_monitoring.relevance_model.load_model",
                        lambda *a, **k: load_calls.append(1) or {"method": "tfidf"})
    monkeypatch.setattr(rf, "fetch_collected_articles", lambda conn, run_id: [{"id": 1, "title": "기사"}])
    monkeypatch.setattr(rf, "get_active_fixed_keywords", lambda conn: [
        {"id": 1, "keyword": "교육"}, {"id": 2, "keyword": "실적"}, {"id": 3, "keyword": "도입"},
    ])
    monkeypatch.setattr(rf, "judge_keyword", lambda conn, run_id, kw, arts, bundle=None: {"ok": True})

    rf.judge_all(conn=None, run_id=1)

    assert len(load_calls) == 1


def test_classifier_failure_is_reported_not_swallowed(monkeypatch):
    """분류기 쪽이 터져도(모델 파일 손상 등) 파이프라인이 죽지 않고 error로
    보고해야 한다 — 그리고 그 error는 pipeline_report가 실패로 집계한다."""
    monkeypatch.setattr(rf, "score_with_classifier",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("모델 손상")))

    result = rf.judge_keyword(
        conn=None, run_id=1, fixed_keyword={"id": 1, "keyword": "교육"},
        articles=[{"id": 1, "title": "기사"}], bundle={"method": "tfidf"},
    )

    assert result["error"] == "RuntimeError: 모델 손상"
    assert result["method"] == "classifier:tfidf"
