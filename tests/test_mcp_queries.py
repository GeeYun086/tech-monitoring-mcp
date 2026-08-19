"""mcp_server/queries.py 테스트.

server.py(도구 선언)는 MCP SDK에 묶여 있어 실제 프로토콜 통신으로만 확인하고,
여기서는 **Claude에게 무엇을 돌려주는가**를 검증한다. 중점 두 가지:
  - 모르는 시장을 물었을 때 빈 결과 대신 "있는 시장 목록"을 안내하는가
  - 기사 순서의 근거(분류기 점수순인지 최신순인지)를 밝히는가
    — 라벨이 부족해 모델이 없으면 최신순인데, 그걸 모르면 추천 순위로 오해한다.
"""

from datetime import date, datetime

from tech_monitoring.mcp_server import queries


class _FakeConn:
    """dashboard_queries·labeling을 monkeypatch로 갈아끼우므로 conn 자체는 쓰이지 않는다."""


def _patch(monkeypatch, *, run=None, markets=(), articles=(), keywords=(),
           span=None, weeks=(), labels=None):
    from tech_monitoring import dashboard_queries as dq
    from tech_monitoring import labeling

    monkeypatch.setattr(queries.dq, "get_latest_run", lambda conn: run)
    monkeypatch.setattr(queries.dq, "get_fixed_keywords", lambda conn: list(markets))
    monkeypatch.setattr(queries.dq, "get_pool_articles", lambda conn, r, k: list(articles))
    monkeypatch.setattr(queries.dq, "get_market_keywords", lambda conn, r, k, limit=30: list(keywords))
    monkeypatch.setattr(queries.dq, "get_pool_span", lambda conn, r: span or {"oldest": None, "newest": None, "total": 0})
    monkeypatch.setattr(queries.dq, "get_pool_weeks", lambda conn, r: list(weeks))
    monkeypatch.setattr(queries.labeling, "count_labels", lambda conn, k=None: labels or {"relevant": 0, "irrelevant": 0, "total": 0})
    assert dq and labeling  # 임포트 경로가 같은 객체인지 확인용


_RUN = {"id": 1, "period_start": date(2026, 8, 10), "period_end": date(2026, 8, 16),
        "status": "completed", "error_message": None}
_MARKETS = [{"id": 1, "keyword": "교육"}, {"id": 2, "keyword": "비즈니스 실적"}]


def _article(title, score=None, published=datetime(2026, 8, 12)):
    return {"title": title, "url": f"https://a.com/{title}", "snippet": "요약",
            "source_domain": "a.com", "published_at": published, "score": score}


# --- 상태 -----------------------------------------------------------------

def test_status_says_so_when_there_is_no_data(monkeypatch):
    _patch(monkeypatch, run=None)

    result = queries.get_status(_FakeConn())

    assert result["has_data"] is False


def test_status_surfaces_pipeline_failure(monkeypatch):
    """실패를 숨기면 결과가 적은 게 수집 실패 때문인지 원래 그런지 구분이 안 된다."""
    failed = {**_RUN, "status": "failed", "error_message": "collect"}
    _patch(monkeypatch, run=failed, markets=_MARKETS)

    result = queries.get_status(_FakeConn())

    assert result["status"] == "failed"
    assert result["error_message"] == "collect"


def test_status_reports_weeks_as_date_ranges(monkeypatch):
    """화면과 같은 표기("8/10~8/16") — 두 곳을 오가며 볼 때 같은 주인지 헷갈리지 않게."""
    _patch(monkeypatch, run=_RUN, markets=_MARKETS,
           weeks=[{"week_start": date(2026, 8, 10), "total": 46}])

    result = queries.get_status(_FakeConn())

    assert result["weeks"] == [{"range": "8/10~8/16", "count": 46}]


# --- 기사 -----------------------------------------------------------------

def test_articles_declare_they_are_ranked_by_the_classifier(monkeypatch):
    _patch(monkeypatch, run=_RUN, markets=_MARKETS,
           articles=[_article("a", score=0.9), _article("b", score=0.1)])

    result = queries.get_articles(_FakeConn(), "교육")

    assert "분류기" in result["ordering"]
    assert result["articles"][0]["score"] == 0.9


def test_articles_warn_when_ordering_is_only_recency(monkeypatch):
    """모델이 없으면 최신순이다 — 그걸 안 밝히면 추천 순위로 오해한다."""
    _patch(monkeypatch, run=_RUN, markets=_MARKETS, articles=[_article("a"), _article("b")])

    result = queries.get_articles(_FakeConn(), "교육")

    assert "최신순" in result["ordering"]
    assert result["articles"][0]["score"] is None


def test_articles_report_total_even_when_truncated(monkeypatch):
    """응답 길이 때문에 자르지만, 전체가 몇 건인지 알려줘야 "이게 전부"로 오해하지 않는다."""
    _patch(monkeypatch, run=_RUN, markets=_MARKETS,
           articles=[_article(f"a{i}", score=0.5) for i in range(50)])

    result = queries.get_articles(_FakeConn(), "교육", limit=5)

    assert (result["total"], result["returned"]) == (50, 5)
    assert len(result["articles"]) == 5


def test_articles_limit_is_clamped_to_a_sane_range(monkeypatch):
    _patch(monkeypatch, run=_RUN, markets=_MARKETS,
           articles=[_article(f"a{i}") for i in range(500)])

    assert queries.get_articles(_FakeConn(), "교육", limit=9999)["returned"] == queries.MAX_ARTICLE_LIMIT
    assert queries.get_articles(_FakeConn(), "교육", limit=0)["returned"] == 1


def test_unknown_market_lists_what_is_available(monkeypatch):
    """빈 결과만 주면 이름을 틀린 건지 데이터가 없는 건지 구분이 안 된다."""
    _patch(monkeypatch, run=_RUN, markets=_MARKETS)

    result = queries.get_articles(_FakeConn(), "교육ㅋ")

    assert "error" in result
    assert result["available_markets"] == ["교육", "비즈니스 실적"]


def test_market_name_ignores_surrounding_spaces(monkeypatch):
    _patch(monkeypatch, run=_RUN, markets=_MARKETS, articles=[_article("a")])

    assert queries.get_articles(_FakeConn(), "  교육 ")["market"] == "교육"


# --- 키워드 ---------------------------------------------------------------

def test_keywords_carry_their_own_caveat(monkeypatch):
    """근사 규칙으로 뽑은 보조 지표라, 사실처럼 단정해 인용되지 않도록 한계를 함께 보낸다."""
    _patch(monkeypatch, run=_RUN, markets=_MARKETS,
           keywords=[{"canonical_phrase": "OpenAI", "doc_count": 12, "variant_phrases": ["오픈AI"]}])

    result = queries.get_keywords(_FakeConn(), "교육")

    assert result["keywords"][0]["variants"] == ["오픈AI"]
    assert "보조 지표" in result["note"]
