"""analysis/keyword_extraction.py 테스트. 실제 DB 없이 search_results/
fixed_keywords 조회를 fake conn/cursor로 흉내낸다."""

from tech_monitoring.analysis import keyword_extraction as ke


class _FakeCursor:
    def __init__(self, search_results: list[dict], fixed_keywords: list[dict],
                 collected_articles: list[dict] | None = None):
        self._search_results = search_results
        self._fixed_keywords = fixed_keywords
        # 006 공용 풀 + 007 시장별 점수. 행에 score/fixed_keyword_id를 넣으면
        # 그 시장에 점수가 매겨진 기사로 본다.
        self._collected = collected_articles or []
        self._result_rows: list[tuple] = []
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=()):
        if "FROM collected_articles ca" in query:      # 점수 상위만
            fixed_keyword_id, run_id, limit = params
            rows = [
                r for r in self._collected
                if r["run_id"] == run_id and r.get("fixed_keyword_id") == fixed_keyword_id
                and r.get("score") is not None
            ]
            rows.sort(key=lambda r: -r["score"])
            rows = rows[:limit]
            self.description = [type("Col", (), {"name": n})() for n in ("title", "snippet", "source_domain")]
            self._result_rows = [(r["title"], r.get("snippet"), r.get("source_domain")) for r in rows]
        elif "FROM collected_articles" in query:        # 풀 전체
            (run_id,) = params
            rows = [r for r in self._collected if r["run_id"] == run_id]
            self.description = [type("Col", (), {"name": n})() for n in ("title", "snippet", "source_domain")]
            self._result_rows = [(r["title"], r.get("snippet"), r.get("source_domain")) for r in rows]
        elif "FROM search_results" in query:
            run_id, fixed_keyword_id = params
            rows = [
                r for r in self._search_results
                if r["run_id"] == run_id and r["fixed_keyword_id"] == fixed_keyword_id
            ]
            self.description = [type("Col", (), {"name": n})() for n in ("title", "snippet", "source_domain")]
            self._result_rows = [(r["title"], r["snippet"], r.get("source_domain")) for r in rows]
        elif "FROM fixed_keywords" in query:
            self.description = [type("Col", (), {"name": n})() for n in ("id", "keyword")]
            self._result_rows = [(kw["id"], kw["keyword"]) for kw in self._fixed_keywords]
        elif "FROM weekly_runs" in query:
            self._result_rows = [(1,)]

    def fetchone(self):
        return self._result_rows[0] if self._result_rows else None

    def fetchall(self):
        return self._result_rows


class _FakeConn:
    def __init__(self, search_results=None, fixed_keywords=None, collected_articles=None):
        self._search_results = search_results or []
        self._fixed_keywords = fixed_keywords or []
        self._collected = collected_articles or []

    def cursor(self):
        return _FakeCursor(self._search_results, self._fixed_keywords, self._collected)


def test_is_korean_heavy_detects_hangul_dominant_text():
    assert ke._is_korean_heavy("삼성전자가 새로운 인공지능 모델을 공개했다") is True


def test_is_korean_heavy_false_for_english_text():
    assert ke._is_korean_heavy("OpenAI released a new model today") is False


def test_is_korean_heavy_false_for_empty_text():
    assert ke._is_korean_heavy("") is False


def test_extract_candidates_routes_korean_text_to_frequency_bucket():
    """한글이 우세한 기사는 TF-IDF가 아니라 원시 빈도(count_keywords)로 채점돼야 한다."""
    rows = [
        {"title": "삼성전자 새로운 인공지능 모델 공개", "snippet": "삼성전자가 인공지능 모델을 선보였다"},
        {"title": "삼성전자 인공지능 사업 확대", "snippet": "삼성전자는 인공지능 투자를 늘린다"},
    ]
    candidates = ke.extract_candidates(rows)
    korean_candidates = [c for c in candidates if c["method"] == "frequency"]
    assert korean_candidates  # 최소 하나는 있어야 함
    assert all(c["tfidf_score"] is None for c in korean_candidates)
    assert any("삼성전자" in c["phrase"] for c in korean_candidates)


def test_is_entity_like_phrase_accepts_capitalized_terms():
    assert ke._is_entity_like_phrase("OpenAI") is True
    assert ke._is_entity_like_phrase("AI") is True
    assert ke._is_entity_like_phrase("General Catalyst") is True  # 2단어 다 대문자


def test_is_entity_like_phrase_rejects_lowercase_word():
    assert ke._is_entity_like_phrase("access") is False
    assert ke._is_entity_like_phrase("did") is False


def test_is_entity_like_phrase_rejects_phrase_with_any_lowercase_word():
    """"told TechCrunch"처럼 일부만 고유명사인 구는 그 자체로 엔티티가 아니다."""
    assert ke._is_entity_like_phrase("told TechCrunch") is False


def test_is_entity_like_phrase_rejects_empty_string():
    assert ke._is_entity_like_phrase("") is False


def test_extract_candidates_filters_out_generic_lowercase_words_from_english_bucket():
    """2026-08-13 실사용 확인 — "access"·"did"·"customers" 같은 일반 단어가
    "이번 주 주요 키워드" 상위권을 차지하던 문제의 회귀 테스트. 기술
    용어·기업명(대문자 시작)만 남아야 한다."""
    rows = [
        {"title": "OpenAI said employees did not tell customers about access issues", "snippet": ""},
        {"title": "OpenAI expands access for enterprise customers who never tell anyone", "snippet": ""},
    ]
    candidates = ke.extract_candidates(rows)
    phrases = {c["phrase"] for c in candidates}
    assert "OpenAI" in phrases
    for generic in ("access", "did", "customers", "tell", "employees", "issues"):
        assert generic not in phrases


def test_extract_candidates_routes_english_text_to_tfidf_bucket():
    rows = [
        {"title": "OpenAI released a new model", "snippet": "The new model improves reasoning"},
        {"title": "OpenAI expands enterprise partnerships", "snippet": "OpenAI signs new deals"},
    ]
    candidates = ke.extract_candidates(rows)
    global_candidates = [c for c in candidates if c["method"] == "tfidf"]
    assert global_candidates
    assert all(c["tfidf_score"] is not None for c in global_candidates)
    assert any(c["phrase"].lower() == "openai" for c in global_candidates)


def test_extract_candidates_handles_mixed_language_corpus_separately():
    """한글 기사와 영문 기사가 섞여 있으면 각자 다른 채점 방식으로 후보가 나와야 한다."""
    rows = [
        {"title": "삼성전자 인공지능 발표", "snippet": "삼성전자가 새 모델을 공개했다"},
        {"title": "OpenAI announces new model", "snippet": "OpenAI's latest release improves accuracy"},
    ]
    candidates = ke.extract_candidates(rows)
    methods = {c["method"] for c in candidates}
    assert methods == {"frequency", "tfidf"}


def test_extract_candidates_empty_input_returns_empty_list():
    assert ke.extract_candidates([]) == []


def test_fetch_search_results_scopes_by_run_and_fixed_keyword():
    conn = _FakeConn(search_results=[
        {"run_id": 1, "fixed_keyword_id": 10, "title": "A", "snippet": "a"},
        {"run_id": 1, "fixed_keyword_id": 20, "title": "B", "snippet": "b"},
        {"run_id": 2, "fixed_keyword_id": 10, "title": "C", "snippet": "c"},
    ])
    rows = ke.fetch_search_results(conn, run_id=1, fixed_keyword_id=10)
    assert len(rows) == 1
    assert rows[0]["title"] == "A"


def test_extract_all_covers_every_active_fixed_keyword():
    conn = _FakeConn(
        search_results=[
            {"run_id": 1, "fixed_keyword_id": 1, "title": "OpenAI new model", "snippet": "great results"},
            {"run_id": 1, "fixed_keyword_id": 2, "title": "삼성전자 발표", "snippet": "새 모델 공개"},
        ],
        fixed_keywords=[{"id": 1, "keyword": "AX 시장"}, {"id": 2, "keyword": "생성형 AI"}],
    )
    result = ke.extract_all(conn, run_id=1)
    assert set(result.keys()) == {1, 2}
    assert result[1]  # 후보가 비어있지 않아야 함
    assert result[2]


# ---- fetch_pool_rows: 점수 상위만 쓰기(007) ----

def _pool(title, *, score=None, fixed_keyword_id=None, run_id=1):
    return {"run_id": run_id, "title": title, "snippet": "요약",
            "source_domain": "example.com", "score": score,
            "fixed_keyword_id": fixed_keyword_id}


def test_fetch_pool_rows_uses_top_scored_articles_for_that_market():
    """풀 전체에서 키워드를 뽑으면 시장을 가리지 않는 일반어가 상위를 먹는다
    (실측 2026-08-19: 시장 셋이 전부 AI/com/Monday). 그 시장 점수 상위만 쓴다."""
    conn = _FakeConn(collected_articles=[
        _pool("알짜 기사", score=0.9, fixed_keyword_id=1),
        _pool("보통 기사", score=0.4, fixed_keyword_id=1),
        _pool("다른 시장 기사", score=0.95, fixed_keyword_id=2),
    ])

    rows = ke.fetch_pool_rows(conn, run_id=1, fixed_keyword_id=1)

    assert [r["title"] for r in rows] == ["알짜 기사", "보통 기사"]


def test_fetch_pool_rows_respects_top_n_limit():
    conn = _FakeConn(collected_articles=[
        _pool(f"기사{i}", score=i / 100, fixed_keyword_id=1)
        for i in range(ke.TOP_ARTICLES_FOR_KEYWORDS + 10)
    ])

    rows = ke.fetch_pool_rows(conn, run_id=1, fixed_keyword_id=1)

    assert len(rows) == ke.TOP_ARTICLES_FOR_KEYWORDS


def test_fetch_pool_rows_falls_back_to_whole_pool_before_any_scoring():
    """첫 주엔 점수가 없다 — 그때 후보를 아예 못 뽑는 것보다 풀 전체를 쓰는
    편이 낫다(시장별로 같은 키워드가 나오는 건 감수)."""
    conn = _FakeConn(collected_articles=[_pool("점수 없는 기사"), _pool("또 하나")])

    rows = ke.fetch_pool_rows(conn, run_id=1, fixed_keyword_id=1)

    assert [r["title"] for r in rows] == ["점수 없는 기사", "또 하나"]
