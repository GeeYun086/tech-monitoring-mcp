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
    assert result == {"fixed_keyword": "AX 시장", "judged": 0, "relevant": 0, "error": None}


def test_judge_keyword_saves_relevance_for_every_article(monkeypatch):
    saved = []

    def fake_save(conn, run_id, fixed_keyword_id, articles, relevant_indices):
        saved.append((fixed_keyword_id, relevant_indices))
        return len(relevant_indices)

    monkeypatch.setattr(rf, "call_gemini", lambda prompt: json.dumps({"relevant_indices": [1]}))
    monkeypatch.setattr(rf, "_save_relevance", fake_save)

    articles = [{"id": 10, "title": "무관 기사"}, {"id": 11, "title": "관련 기사"}]
    result = rf.judge_keyword(conn=None, run_id=1, fixed_keyword={"id": 5, "keyword": "AX 시장"}, articles=articles)

    assert result == {"fixed_keyword": "AX 시장", "judged": 2, "relevant": 1, "error": None}
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
    monkeypatch.setattr(rf, "_save_relevance", lambda conn, run_id, kw_id, articles, indices: saved.append(indices) or 0)

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
    monkeypatch.setattr(rf, "judge_keyword", lambda conn, run_id, kw, articles: judge_calls.append((kw["id"], articles)) or {"ok": kw["id"]})

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
