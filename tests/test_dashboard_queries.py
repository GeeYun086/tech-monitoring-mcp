"""dashboard_queries.py 테스트. 실제 DB 없이 각 테이블을 딕셔너리 리스트로
흉내낸 스텁 conn/cursor를 쓴다 — 어떤 SQL이 실행됐는지가 아니라 반환값이
기대한 필터링·정렬을 반영하는지에 집중한다."""

from datetime import date, datetime

from tech_monitoring import dashboard_queries as dq

_COLS_BY_TABLE = {
    "weekly_runs": ("id", "period_start", "period_end", "status", "completed_at", "error_message"),
    "fixed_keywords": ("id", "keyword"),
    "market_keywords": ("canonical_phrase", "variant_phrases", "doc_count", "tfidf_score"),
    "search_results": ("title", "url", "snippet", "source_domain", "published_at", "rank"),
    # 006 공용 기사 풀 — 시장(fixed_keyword_id) 컬럼이 없다.
    # score는 007에서 붙은 시장별 분류기 점수(LEFT JOIN으로 딸려온다).
    "collected_articles": ("title", "url", "snippet", "source_domain", "published_at",
                           "source_name", "score"),
}


# 판정 우선순위 — 서브쿼리에 딸려 나오는 이름보다 바깥 FROM이 먼저 잡히게.
_TABLE_PRIORITY = ("collected_articles", "search_results", "market_keywords",
                   "weekly_runs", "fixed_keywords")


def _sort_by_published_at_desc_nulls_last(rows: list[dict]) -> list[dict]:
    dated = sorted((r for r in rows if r.get("published_at") is not None), key=lambda r: r["published_at"], reverse=True)
    undated = [r for r in rows if r.get("published_at") is None]
    return dated + undated


class _FakeCursor:
    def __init__(self, tables: dict):
        self._tables = tables
        self._rows: list[tuple] = []
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=()):
        # 주 테이블 판정. collected_articles 질의는 키워드 적중을 세려고
        # 서브쿼리에서 fixed_keywords를 읽으므로(2026-08-19), 사전 순으로 먼저
        # 걸리는 이름을 쓰면 엉뚱한 테이블로 잡힌다. 기사 테이블을 먼저 본다.
        table = next(name for name in _TABLE_PRIORITY if f"FROM {name}" in query)
        cols = _COLS_BY_TABLE[table]
        self.description = [type("Col", (), {"name": n})() for n in cols]

        if table == "weekly_runs":
            rows = sorted(self._tables.get("weekly_runs", []), key=lambda r: -r["id"])[:1]
        elif table == "fixed_keywords":
            rows = sorted(self._tables.get("fixed_keywords", []), key=lambda r: (r["display_order"], r["id"]))
        elif table == "market_keywords":
            run_id, fixed_keyword_id, limit = params
            rows = [
                r for r in self._tables.get("market_keywords", [])
                if r["run_id"] == run_id and r["fixed_keyword_id"] == fixed_keyword_id
            ]
            rows.sort(key=lambda r: (-r["doc_count"], -(r["tfidf_score"] or -1)))
            rows = rows[:limit]
        elif "date_trunc('week'" in query:          # get_pool_weeks(발행 주 목록)
            (run_id,) = params
            counts: dict = {}
            for r in self._tables.get("collected_articles", []):
                if r["run_id"] != run_id or r.get("published_at") is None:
                    continue
                day = r["published_at"].date()
                monday = day.fromordinal(day.toordinal() - day.weekday())
                counts[monday] = counts.get(monday, 0) + 1
            self.description = [type("Col", (), {"name": n})() for n in ("week_start", "total")]
            self._rows = sorted(counts.items(), reverse=True)
            return
        elif query.strip().startswith("SELECT count(*)") and "published_at IS NULL" in query:
            # 기사 목록 질의도 키워드 적중을 세느라 count(*)를 쓰고, 날짜 미상
            # 필터에는 published_at IS NULL이 들어간다 — 둘을 구분하려면
            # "질의가 count(*)로 시작하는가"(주차 집계 전용)를 봐야 한다.
            (run_id,) = params
            undated = [r for r in self._tables.get("collected_articles", [])
                       if r["run_id"] == run_id and r.get("published_at") is None]
            self._rows = [(len(undated),)]
            return
        elif table == "collected_articles":
            # 시장별 정렬 질의는 (fixed_keyword_id, run_id) 순으로 넘어온다.
            scoped = "LEFT JOIN article_keyword_relevance" in query
            if scoped:
                fixed_keyword_id, run_id, *rest = params
            else:
                fixed_keyword_id, (run_id, *rest) = None, params
            rows = [r for r in self._tables.get("collected_articles", []) if r["run_id"] == run_id]
            if "ILIKE ANY" in query:
                patterns, _patterns2, *rest = rest
                needles = [p.strip("%") for p in patterns]
                rows = [
                    r for r in rows
                    if any(n in r["title"] or n in (r.get("snippet") or "") for n in needles)
                ]
            if "published_at >=" in query:
                start, end, *rest = rest
                rows = [r for r in rows
                        if r.get("published_at") is not None and start <= r["published_at"].date() < end]
            elif "published_at IS NULL" in query:
                rows = [r for r in rows if r.get("published_at") is None]
            # 점수는 (기사, 시장) 쌍에만 붙는다 — 없으면 NULL로 딸려온다.
            scores = {
                (s["url"], s["fixed_keyword_id"]): s["score"]
                for s in self._tables.get("article_keyword_relevance", [])
            }
            rows = [{**r, "score": scores.get((r["url"], fixed_keyword_id))} for r in rows]
            rows = _sort_by_published_at_desc_nulls_last(rows)
            if scoped:
                # ORDER BY score DESC NULLS LAST, published_at DESC — 위 최신순
                # 정렬을 유지한 채(파이썬 sort는 안정적) 점수만 앞세운다.
                rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
            if rest:
                rows = rows[:rest[0]]
        elif "ILIKE" in query:
            run_id, fixed_keyword_id, patterns, _patterns2, *rest = params
            limit = rest[0] if rest else None
            needles = [p.strip("%") for p in patterns]
            rows = [
                r for r in self._tables.get("search_results", [])
                if r["run_id"] == run_id and r["fixed_keyword_id"] == fixed_keyword_id
                and any(n in r["title"] or n in (r.get("snippet") or "") for n in needles)
            ]
            rows = _sort_by_published_at_desc_nulls_last(rows)
            if limit is not None:
                rows = rows[:limit]
        else:
            run_id, fixed_keyword_id, *rest = params
            limit = rest[0] if rest else None
            rows = [
                r for r in self._tables.get("search_results", [])
                if r["run_id"] == run_id and r["fixed_keyword_id"] == fixed_keyword_id
            ]
            rows = _sort_by_published_at_desc_nulls_last(rows)
            if limit is not None:
                rows = rows[:limit]

        self._rows = [tuple(r.get(c) for c in cols) for r in rows]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, **tables):
        self.tables = tables

    def cursor(self):
        return _FakeCursor(self.tables)


def test_get_latest_run_returns_none_when_empty():
    conn = _FakeConn(weekly_runs=[])
    assert dq.get_latest_run(conn) is None


def test_get_latest_run_returns_most_recent_by_id():
    conn = _FakeConn(weekly_runs=[
        {"id": 1, "period_start": "2026-08-03", "period_end": "2026-08-09", "status": "completed", "completed_at": None},
        {"id": 2, "period_start": "2026-08-10", "period_end": "2026-08-16", "status": "running", "completed_at": None},
    ])
    run = dq.get_latest_run(conn)
    assert run["id"] == 2
    assert run["status"] == "running"


def test_get_fixed_keywords_orders_by_display_order():
    conn = _FakeConn(fixed_keywords=[
        {"id": 1, "keyword": "생성형 AI", "display_order": 2},
        {"id": 2, "keyword": "AX 시장", "display_order": 1},
    ])
    result = dq.get_fixed_keywords(conn)
    assert [r["keyword"] for r in result] == ["AX 시장", "생성형 AI"]


def test_get_market_keywords_scopes_by_run_and_fixed_keyword_ordered_by_doc_count():
    conn = _FakeConn(market_keywords=[
        {"run_id": 1, "fixed_keyword_id": 10, "canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"], "doc_count": 5, "tfidf_score": 2.0},
        {"run_id": 1, "fixed_keyword_id": 10, "canonical_phrase": "Anthropic", "variant_phrases": ["Anthropic"], "doc_count": 8, "tfidf_score": 1.0},
        {"run_id": 1, "fixed_keyword_id": 20, "canonical_phrase": "다른시장", "variant_phrases": ["다른시장"], "doc_count": 9, "tfidf_score": None},
        {"run_id": 2, "fixed_keyword_id": 10, "canonical_phrase": "지난주", "variant_phrases": ["지난주"], "doc_count": 100, "tfidf_score": None},
    ])
    result = dq.get_market_keywords(conn, run_id=1, fixed_keyword_id=10)
    assert [r["canonical_phrase"] for r in result] == ["Anthropic", "OpenAI"]


def test_get_market_keywords_respects_limit():
    conn = _FakeConn(market_keywords=[
        {"run_id": 1, "fixed_keyword_id": 10, "canonical_phrase": f"kw{i}", "variant_phrases": [f"kw{i}"], "doc_count": i, "tfidf_score": None}
        for i in range(10)
    ])
    result = dq.get_market_keywords(conn, run_id=1, fixed_keyword_id=10, limit=3)
    assert len(result) == 3
    assert [r["canonical_phrase"] for r in result] == ["kw9", "kw8", "kw7"]


def test_get_search_results_orders_by_published_at_descending():
    """2026-08-13 실사용 확인 — rank는 사이트마다 1부터 따로 매겨지는 값이라
    여러 사이트를 rank로 정렬하면 결과 수가 많은 사이트가 상위를 독점해
    다른 사이트 기사가 화면에 안 보이는 문제가 있었다. 최신순이 공정하다."""
    conn = _FakeConn(search_results=[
        {"run_id": 1, "fixed_keyword_id": 10, "title": "오래된 기사", "url": "u1", "snippet": "", "source_domain": "d", "published_at": datetime(2026, 8, 10), "rank": 1},
        {"run_id": 1, "fixed_keyword_id": 10, "title": "최신 기사", "url": "u2", "snippet": "", "source_domain": "d", "published_at": datetime(2026, 8, 15), "rank": 5},
    ])
    result = dq.get_search_results(conn, run_id=1, fixed_keyword_id=10)
    assert [r["title"] for r in result] == ["최신 기사", "오래된 기사"]


def test_get_search_results_places_missing_published_at_last():
    conn = _FakeConn(search_results=[
        {"run_id": 1, "fixed_keyword_id": 10, "title": "발행일 없음", "url": "u1", "snippet": "", "source_domain": "d", "published_at": None, "rank": 1},
        {"run_id": 1, "fixed_keyword_id": 10, "title": "발행일 있음", "url": "u2", "snippet": "", "source_domain": "d", "published_at": datetime(2026, 8, 10), "rank": 2},
    ])
    result = dq.get_search_results(conn, run_id=1, fixed_keyword_id=10)
    assert [r["title"] for r in result] == ["발행일 있음", "발행일 없음"]


def test_get_search_results_returns_all_rows_when_no_limit_given():
    """2026-08-13 담당자 확인 — top20으로 안 자르고 이번 주 수집분 전체를
    다 보고 싶다(나중에 라벨링 작업에 쓸 예정)."""
    conn = _FakeConn(search_results=[
        {"run_id": 1, "fixed_keyword_id": 10, "title": f"기사{i}", "url": f"u{i}", "snippet": "", "source_domain": "d", "published_at": None, "rank": i}
        for i in range(30)
    ])
    result = dq.get_search_results(conn, run_id=1, fixed_keyword_id=10)
    assert len(result) == 30


def test_get_search_results_for_variants_filters_by_title_or_snippet():
    conn = _FakeConn(search_results=[
        {"run_id": 1, "fixed_keyword_id": 10, "title": "OpenAI announces GPT-6", "url": "u1", "snippet": "", "source_domain": "d", "published_at": None, "rank": 1},
        {"run_id": 1, "fixed_keyword_id": 10, "title": "삼성전자 발표", "url": "u2", "snippet": "오픈AI와 협력", "source_domain": "d", "published_at": None, "rank": 2},
        {"run_id": 1, "fixed_keyword_id": 10, "title": "무관한 기사", "url": "u3", "snippet": "", "source_domain": "d", "published_at": None, "rank": 3},
    ])
    result = dq.get_search_results_for_variants(conn, run_id=1, fixed_keyword_id=10, variant_phrases=["OpenAI", "오픈AI"])
    assert {r["url"] for r in result} == {"u1", "u2"}


def test_get_search_results_for_variants_returns_empty_for_no_variants():
    conn = _FakeConn(search_results=[])
    assert dq.get_search_results_for_variants(conn, run_id=1, fixed_keyword_id=10, variant_phrases=[]) == []


# ---- truncate_summary: 2026-08-13 실사용 확인 — 화면에 문단째로 쏟아지던 문제 ----

def test_truncate_summary_returns_short_text_unchanged():
    assert dq.truncate_summary("짧은 요약") == "짧은 요약"


def test_truncate_summary_handles_none_and_empty():
    assert dq.truncate_summary(None) == ""
    assert dq.truncate_summary("") == ""


def test_truncate_summary_cuts_long_text_at_word_boundary():
    text = "word " * 100  # 500자
    result = dq.truncate_summary(text, max_chars=20)
    assert len(result) <= 21  # 말줄임표(1자) 포함
    assert result.endswith("…")
    assert not result[:-1].endswith(" ")  # 단어 중간이 아니라 공백 기준으로 잘림


def test_truncate_summary_collapses_whitespace_and_removes_chunk_markers():
    """Tavily 응답의 "[...]" 청크 구분자와 개행·중복 공백을 정리해야 한다."""
    text = "첫 문단입니다.\n\n[...] 둘째 문단입니다.   공백이  많음."
    result = dq.truncate_summary(text, max_chars=200)
    assert "[...]" not in result
    assert "  " not in result
    assert "\n" not in result


def test_get_search_results_truncates_long_snippet():
    conn = _FakeConn(search_results=[
        {
            "run_id": 1, "fixed_keyword_id": 10, "title": "A", "url": "u1",
            "snippet": "word " * 100, "source_domain": "d", "published_at": None, "rank": 1,
        },
    ])
    result = dq.get_search_results(conn, run_id=1, fixed_keyword_id=10)
    assert len(result[0]["snippet"]) <= dq._SUMMARY_MAX_CHARS + 1


def test_get_latest_run_includes_error_message_for_failed_run():
    """실패 사유를 화면에 띄우려면 조회에 error_message가 포함돼야 한다(2026-08-18)."""
    conn = _FakeConn(weekly_runs=[
        {"id": 3, "period_start": "2026-08-17", "period_end": "2026-08-23",
         "status": "failed", "completed_at": None, "error_message": "judge_relevance"},
    ])
    run = dq.get_latest_run(conn)
    assert run["status"] == "failed"
    assert run["error_message"] == "judge_relevance"


# --- 006 공용 기사 풀 -------------------------------------------------------

def _pool_row(url, *, run_id=1, title="제목", snippet="요약", published_at=None):
    return {
        "run_id": run_id, "title": title, "url": url, "snippet": snippet,
        "source_domain": "techcrunch.com", "published_at": published_at,
        "source_name": "TechCrunch",
    }


def test_get_pool_articles_returns_whole_pool_newest_first():
    """시장 인자를 받지 않는다 — 풀은 시장과 무관하고, 세 탭이 같은 목록을
    각자 순서로 보게 될 자리다(작업 3에서 분류기 점수로 정렬)."""
    conn = _FakeConn(collected_articles=[
        _pool_row("https://a.com/old", published_at=datetime(2026, 8, 17)),
        _pool_row("https://a.com/none"),
        _pool_row("https://a.com/new", published_at=datetime(2026, 8, 19)),
        _pool_row("https://a.com/other-week", run_id=2),
    ])

    result = dq.get_pool_articles(conn, run_id=1)

    assert [r["url"].rsplit("/", 1)[-1] for r in result] == ["new", "old", "none"]


def test_get_pool_articles_does_not_truncate_by_default():
    """잘라내지 않는 원칙 — 라벨링에는 무관 기사도 필요하고, 분류기가 틀려도
    기사가 사라지면 안 된다."""
    conn = _FakeConn(collected_articles=[_pool_row(f"https://a.com/{i}") for i in range(25)])

    assert len(dq.get_pool_articles(conn, run_id=1)) == 25
    assert len(dq.get_pool_articles(conn, run_id=1, limit=5)) == 5


def test_get_pool_articles_for_variants_filters_by_mention():
    conn = _FakeConn(collected_articles=[
        _pool_row("https://a.com/1", title="생성형 AI 도입 확산"),
        _pool_row("https://a.com/2", title="반도체 수출 증가", snippet="제너레이티브 AI 언급"),
        _pool_row("https://a.com/3", title="무관한 소식", snippet="관련 없음"),
    ])

    result = dq.get_pool_articles_for_variants(
        conn, run_id=1, variant_phrases=["생성형 AI", "제너레이티브 AI"],
    )

    assert {r["url"].rsplit("/", 1)[-1] for r in result} == {"1", "2"}


def test_get_pool_articles_for_variants_returns_nothing_without_phrases():
    conn = _FakeConn(collected_articles=[_pool_row("https://a.com/1")])

    assert dq.get_pool_articles_for_variants(conn, run_id=1, variant_phrases=[]) == []


# --- 007 분류기 점수 정렬 ---------------------------------------------------

def test_get_pool_articles_orders_by_market_score_when_available():
    """같은 풀을 시장별로 다른 순서로 본다 — 이게 라벨링이 화면에 반영되는
    유일한 경로다(v2엔 원래 관련도 판단 단계가 없었다)."""
    conn = _FakeConn(
        collected_articles=[
            _pool_row("https://a.com/low", title="무관", published_at=datetime(2026, 8, 19)),
            _pool_row("https://a.com/high", title="알짜", published_at=datetime(2026, 8, 17)),
        ],
        article_keyword_relevance=[
            {"url": "https://a.com/low", "fixed_keyword_id": 1, "score": 0.05},
            {"url": "https://a.com/high", "fixed_keyword_id": 1, "score": 0.93},
        ],
    )

    result = dq.get_pool_articles(conn, run_id=1, fixed_keyword_id=1)

    # 최신순이면 무관이 먼저지만, 점수 순서가 이를 뒤집어야 한다.
    assert [r["title"] for r in result] == ["알짜", "무관"]
    assert result[0]["score"] == 0.93


def test_get_pool_articles_keeps_low_scoring_articles():
    """잘라내지 않는다 — 분류기가 틀려도 기사가 사라지면 안 되고, 라벨링에는
    도움 안 되는 기사도 필요하다."""
    conn = _FakeConn(
        collected_articles=[_pool_row(f"https://a.com/{i}") for i in range(5)],
        article_keyword_relevance=[
            {"url": f"https://a.com/{i}", "fixed_keyword_id": 1, "score": 0.01} for i in range(5)
        ],
    )

    assert len(dq.get_pool_articles(conn, run_id=1, fixed_keyword_id=1)) == 5


def test_get_pool_articles_puts_unscored_articles_after_scored_ones():
    """판단 전 기사(또는 확률을 주지 않는 Gemini 판단)는 NULLS LAST로 밀린 뒤
    최신순으로 이어진다 — 점수 유무가 곧 "분류기가 봤는지"다."""
    conn = _FakeConn(
        collected_articles=[
            _pool_row("https://a.com/unscored", title="판단 전", published_at=datetime(2026, 8, 19)),
            _pool_row("https://a.com/scored", title="점수 있음", published_at=datetime(2026, 8, 17)),
        ],
        article_keyword_relevance=[
            {"url": "https://a.com/scored", "fixed_keyword_id": 1, "score": 0.2},
        ],
    )

    result = dq.get_pool_articles(conn, run_id=1, fixed_keyword_id=1)

    assert [r["title"] for r in result] == ["점수 있음", "판단 전"]


def test_get_pool_articles_ignores_other_markets_scores():
    conn = _FakeConn(
        collected_articles=[
            _pool_row("https://a.com/1", title="가", published_at=datetime(2026, 8, 17)),
            _pool_row("https://a.com/2", title="나", published_at=datetime(2026, 8, 19)),
        ],
        article_keyword_relevance=[
            {"url": "https://a.com/1", "fixed_keyword_id": 2, "score": 0.99},   # 다른 시장
        ],
    )

    result = dq.get_pool_articles(conn, run_id=1, fixed_keyword_id=1)

    assert [r["title"] for r in result] == ["나", "가"]        # 점수 없음 → 최신순
    assert all(r["score"] is None for r in result)


# --- 주차 선택(소급 수집으로 한 run에 여러 주가 섞인다) --------------------

def test_get_pool_weeks_lists_recent_weeks_first_with_counts():
    conn = _FakeConn(collected_articles=[
        _pool_row("https://a.com/1", published_at=datetime(2026, 8, 17)),
        _pool_row("https://a.com/2", published_at=datetime(2026, 8, 18)),
        _pool_row("https://a.com/3", published_at=datetime(2026, 8, 11)),
    ])

    weeks = dq.get_pool_weeks(conn, run_id=1)

    assert weeks == [
        {"week_start": date(2026, 8, 17), "total": 2},
        {"week_start": date(2026, 8, 10), "total": 1},
    ]


def test_get_pool_weeks_surfaces_undated_articles_as_their_own_bucket():
    """발행일 없는 기사는 어느 주에도 안 속한다 — 버킷으로 내주지 않으면
    주차를 고르는 순간 화면에서 통째로 사라지고 라벨링 대상에서도 빠진다."""
    conn = _FakeConn(collected_articles=[
        _pool_row("https://a.com/1", published_at=datetime(2026, 8, 17)),
        _pool_row("https://a.com/2"),
    ])

    assert dq.get_pool_weeks(conn, run_id=1)[-1] == {"week_start": None, "total": 1}


def test_get_pool_articles_can_be_scoped_to_one_week():
    conn = _FakeConn(collected_articles=[
        _pool_row("https://a.com/this-week", title="이번 주", published_at=datetime(2026, 8, 18)),
        _pool_row("https://a.com/last-week", title="지난 주", published_at=datetime(2026, 8, 11)),
    ])

    result = dq.get_pool_articles(conn, run_id=1, fixed_keyword_id=1, week_start=date(2026, 8, 10))

    assert [r["title"] for r in result] == ["지난 주"]


def test_get_pool_articles_can_show_only_undated():
    conn = _FakeConn(collected_articles=[
        _pool_row("https://a.com/dated", title="날짜 있음", published_at=datetime(2026, 8, 18)),
        _pool_row("https://a.com/undated", title="날짜 없음"),
    ])

    from tech_monitoring import labeling
    result = dq.get_pool_articles(conn, run_id=1, fixed_keyword_id=1, week_start=labeling.UNDATED)

    assert [r["title"] for r in result] == ["날짜 없음"]


# ---- 정렬 근거 (2026-08-19, 키워드 임시 정렬 도입) ----

def test_ordering_says_classifier_when_scores_exist():
    articles = [{"score": 0.9}, {"score": 0.1}]

    assert "분류기" in dq.describe_ordering(articles)


def test_ordering_falls_back_to_recency_without_a_model():
    articles = [{"score": None}, {"score": None}]

    assert "최신순" in dq.describe_ordering(articles)


def test_ordering_says_classifier_if_any_article_is_scored():
    """일부만 채점된 상태에서도 근거는 분류기다 — 점수가 앞서 정렬되므로."""
    articles = [{"score": None}, {"score": 0.7}]

    assert "분류기" in dq.describe_ordering(articles)
