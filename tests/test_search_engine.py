"""검색엔진 수집기(collectors/search_engine.py, Tavily 기반) 테스트.
실제 네트워크·DB 없이 _fetch_site_results와 conn.cursor()를 스텁으로 대체한다."""

from datetime import date, datetime, timezone

import httpx
import pytest

from tech_monitoring.collectors import search_engine

_START = date(2026, 8, 10)
_END = date(2026, 8, 16)


# INSERT 문의 url 위치 — 두 저장 경로의 컬럼 순서가 다르다.
#   search_results     : (run_id, fixed_keyword_id, query, rank, title, url, ...)
#   collected_articles : (run_id, source_name, title, url, ...)  ← 006 공용 풀
_URL_INDEX = {"search_results": 5, "collected_articles": 3}


class _FakeCursor:
    """INSERT ... RETURNING id 만 이해하는 최소 스텁. ON CONFLICT DO NOTHING을
    inserted_urls 집합으로 흉내낸다 — 이미 있으면 fetchone()이 None(=미삽입)."""

    def __init__(self, inserted_urls: set[str], inserted_params: list[tuple],
                 inserted_titles: set[str]):
        self._inserted_urls = inserted_urls
        self._inserted_titles = inserted_titles
        self._inserted_params = inserted_params
        self._last_params = None
        self._url_index = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        table = next((t for t in _URL_INDEX if f"INSERT INTO {t}" in query), None)
        assert table is not None, f"스텁이 모르는 질의: {query}"
        self._url_index = _URL_INDEX[table]
        self._last_params = params

    def fetchone(self):
        url = self._last_params[self._url_index]
        # 공용 풀 INSERT는 제목 중복도 막는다(WHERE NOT EXISTS) — 스텁도 같게.
        title = self._last_params[2] if self._url_index == 3 else None
        if url in self._inserted_urls or (title is not None and title in self._inserted_titles):
            return None
        self._inserted_urls.add(url)
        if title is not None:
            self._inserted_titles.add(title)
        self._inserted_params.append(self._last_params)
        return (1,)


class _FakeConn:
    def __init__(self):
        self.inserted_urls: set[str] = set()
        self.inserted_titles: set[str] = set()
        self.inserted_params: list[tuple] = []

    def cursor(self):
        return _FakeCursor(self.inserted_urls, self.inserted_params, self.inserted_titles)

    def close(self):
        pass


def _item(url: str, published_date: str | None = "Tue, 11 Aug 2026 16:25:20 GMT") -> dict:
    # 발행일은 기본으로 채운다 — 없는 기사는 아예 저장하지 않기로 했으므로
    # (2026-08-19), 날짜가 빠지면 모든 저장 테스트가 "0건"이 돼버린다.
    return {"url": url, "title": "제목", "content": "요약", "published_date": published_date}


# ---- is_allowed_url: 담당자가 실제로 준 화이트리스트 패턴 검증 ----
# 이 부분이 틀리면 전혀 관련 없는 페이지가 저장되거나, 원하는 기사가 통째로
# 빠질 수 있어서 가장 꼼꼼하게 테스트한다.

@pytest.mark.parametrize("url", [
    "https://www.itworld.co.kr/article/123456/ai.html",
    "https://www.aitimes.com/news/articleView.html?idxno=999",
    "https://news.hada.io/weekly/123",
    "https://techcrunch.com/2026/08/13/some-story/",
    "https://social.techcrunch.com/2026/08/13/some-story/",
    "https://askedtech.com/knowledge-archive/agent-adoption",
    "https://www.techmeme.com/260813/p1",
    "https://news.hada.io/topic?id=32454",  # 2026-08-13: 위클리만으론 0건이라 개별 글까지 포함
])
def test_is_allowed_url_accepts_include_patterns(url):
    assert search_engine.is_allowed_url(url) is True


@pytest.mark.parametrize("url", [
    "https://www.itworld.co.kr/",  # 루트 페이지(기사 아님)
    "https://www.aitimes.com/",
    "https://www.itworld.co.kr/reviews/123",
    "https://www.itworld.co.kr/how-to/123",
    "https://www.itworld.co.kr/newsletters/123",
    "https://www.aitimes.com/news/articleList.html?sc=all",
    "https://www.techmeme.com/river",
    "https://www.techmeme.com/lb/1234",
    "https://www.techmeme.com/about",
    "https://www.techmeme.com/events",
    "https://www.techmeme.com/miniriver",
    "https://techcrunch.com/podcast/equity/",
    "https://techcrunch.com/author/some-writer/",
    "https://techcrunch.com/category/ai/",
    "https://techcrunch.com/tag/openai/",
    "https://example.com/completely-unrelated",  # 화이트리스트에 아예 없는 도메인
])
def test_is_allowed_url_rejects_excluded_or_unlisted(url):
    assert search_engine.is_allowed_url(url) is False


# ---- _parse_published_date: 2026-08-13 실제 Tavily 응답으로 확인한 RFC 2822 형식 ----

def test_parse_published_date_parses_rfc2822():
    result = search_engine._parse_published_date("Tue, 11 Aug 2026 16:25:20 GMT")
    assert result == datetime(2026, 8, 11, 16, 25, 20, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [None, "", "이상한 형식"])
def test_parse_published_date_falls_back_to_none(value):
    assert search_engine._parse_published_date(value) is None


# ---- _terms_for_domain / 다중 검색어(2026-08-13, 실사용 피드백) ----

def test_terms_for_domain_uses_korean_terms_for_korean_sites():
    kw = {"keyword": "교육", "search_terms_ko": ["AI 교육", "에듀테크"], "search_terms_en": ["AI education"]}
    assert search_engine._terms_for_domain(kw, "aitimes.com") == ["AI 교육", "에듀테크"]


def test_terms_for_domain_uses_english_terms_for_english_sites():
    kw = {"keyword": "교육", "search_terms_ko": ["AI 교육"], "search_terms_en": ["AI education", "edtech"]}
    assert search_engine._terms_for_domain(kw, "techcrunch.com") == ["AI education", "edtech"]


def test_terms_for_domain_falls_back_to_keyword_when_empty():
    """아직 언어별 검색어를 등록 안 한 고정 키워드는 keyword 자체로 폴백한다."""
    kw = {"keyword": "교육", "search_terms_ko": [], "search_terms_en": []}
    assert search_engine._terms_for_domain(kw, "aitimes.com") == ["교육"]
    assert search_engine._terms_for_domain(kw, "techcrunch.com") == ["교육"]


def test_terms_for_domain_falls_back_when_keys_missing():
    """search_terms_ko/en 키 자체가 없는 dict(구버전 호출부 호환)도 안전해야 한다."""
    kw = {"keyword": "교육"}
    assert search_engine._terms_for_domain(kw, "aitimes.com") == ["교육"]


def test_collect_for_keyword_queries_once_per_term_per_site(monkeypatch):
    """검색어가 여러 개면 그 사이트에 대해 검색어 개수만큼 각각 호출해야 한다."""
    calls = []

    def fake_fetch(term, domain, start_date, end_date):
        calls.append((domain, term))
        return []

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    kw = {
        "id": 1, "keyword": "교육",
        "search_terms_ko": ["AI 교육", "에듀테크"],
        "search_terms_en": ["AI education"],
    }
    search_engine.collect_for_keyword(_FakeConn(), run_id=1, fixed_keyword=kw, start_date=_START, end_date=_END)

    for domain in search_engine.KOREAN_DOMAINS:
        assert (domain, "AI 교육") in calls
        assert (domain, "에듀테크") in calls
    for domain in search_engine.ENGLISH_DOMAINS:
        assert (domain, "AI education") in calls
    assert len(calls) == len(search_engine.KOREAN_DOMAINS) * 2 + len(search_engine.ENGLISH_DOMAINS) * 1


def test_collect_for_keyword_stores_actual_term_as_query(monkeypatch):
    """search_results.query엔 카테고리 이름("교육")이 아니라 실제로 검색에
    쓴 구체적인 검색어("에듀테크")가 남아야 한다 — 나중에 어느 검색어가
    이 기사를 찾아왔는지 추적할 수 있게."""
    monkeypatch.setattr(
        search_engine, "_fetch_site_results",
        lambda term, domain, start_date, end_date: (
            [_item("https://www.aitimes.com/news/x.html")] if domain == "aitimes.com" else []
        ),
    )

    conn = _FakeConn()
    kw = {"id": 1, "keyword": "교육", "search_terms_ko": ["에듀테크"], "search_terms_en": []}
    search_engine.collect_for_keyword(conn, run_id=1, fixed_keyword=kw, start_date=_START, end_date=_END)

    assert len(conn.inserted_params) == 1
    stored_query = conn.inserted_params[0][2]  # (run_id, fixed_keyword_id, query, ...)
    assert stored_query == "에듀테크"


# ---- collect_for_keyword ----

def test_collect_for_keyword_queries_each_site_domain_separately(monkeypatch):
    """사이트 하나에 결과가 쏠리는 걸 막기 위해 화이트리스트 사이트마다 개별 호출해야 한다."""
    calls = []

    def fake_fetch(keyword, domain, start_date, end_date):
        calls.append(domain)
        assert (start_date, end_date) == (_START, _END)
        return []

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    search_engine.collect_for_keyword(
        _FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "에이전트 도입"},
        start_date=_START, end_date=_END,
    )

    assert calls == search_engine.SITE_DOMAINS


def test_collect_for_keyword_stores_only_allowed_urls(monkeypatch):
    """Tavily가 도메인은 맞혀도 화이트리스트 경로 밖의 URL(예: techmeme.com/river)을
    돌려주면 저장 단계에서 다시 걸러져야 한다(이중 강제)."""
    def fake_fetch(keyword, domain, start_date, end_date):
        if domain == "techmeme.com":
            return [_item("https://www.techmeme.com/260813/p1"), _item("https://www.techmeme.com/river")]
        return []

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    result = search_engine.collect_for_keyword(
        _FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "교육"}, start_date=_START, end_date=_END,
    )

    assert result["fetched"] == 2  # Tavily가 준 원본 개수
    assert result["inserted"] == 1  # 화이트리스트 통과한 것만 저장
    assert result["error"] is None


def test_collect_for_keyword_dedups_same_url_across_sites(monkeypatch):
    def fake_fetch(keyword, domain, start_date, end_date):
        return [_item("https://techcrunch.com/2026/08/13/story/")]

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    result = search_engine.collect_for_keyword(
        _FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "교육"}, start_date=_START, end_date=_END,
    )

    # 6개 사이트 전부 같은 URL을 돌려줘도(스텁이라 실제로는 안 그러겠지만) 1건만 저장돼야 함
    assert result["inserted"] == 1


def test_collect_for_keyword_returns_error_on_http_failure(monkeypatch):
    """한 사이트 호출이 실패해도 예외를 던지지 않고 error 필드로 보고해야 한다(파이프라인 격리)."""
    def fake_fetch(keyword, domain, start_date, end_date):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    result = search_engine.collect_for_keyword(
        _FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "교육"}, start_date=_START, end_date=_END,
    )

    assert result["error"] == "boom"
    assert result["fetched"] == 0
    assert result["inserted"] == 0


# ---- search_once ----

def test_search_once_returns_only_allowed_urls(monkeypatch):
    monkeypatch.setattr(search_engine.settings, "tavily_api_key", "key")

    def fake_request(payload):
        return {"results": [
            _item("https://techcrunch.com/2026/08/13/story/"),
            _item("https://www.techmeme.com/river"),  # 제외 패턴 — 걸러져야 함
        ]}

    monkeypatch.setattr(search_engine, "_tavily_request", fake_request)

    results = search_engine.search_once("AX 시장")

    assert len(results) == 1
    assert results[0]["url"] == "https://techcrunch.com/2026/08/13/story/"


def test_search_once_returns_empty_list_without_credentials(monkeypatch):
    monkeypatch.setattr(search_engine.settings, "tavily_api_key", None)
    assert search_engine.search_once("AX 시장") == []


def test_search_once_returns_empty_list_on_http_error(monkeypatch):
    monkeypatch.setattr(search_engine.settings, "tavily_api_key", "key")

    def fake_request(payload):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(search_engine, "_tavily_request", fake_request)

    assert search_engine.search_once("AX 시장") == []


def test_collect_all_reports_missing_credentials(monkeypatch):
    monkeypatch.setattr(search_engine.settings, "tavily_api_key", None)

    results = search_engine.collect_all(run_id=1)

    assert len(results) == 1
    assert "TAVILY_API_KEY" in results[0]["error"]


def test_collect_all_collects_per_site_not_per_keyword(monkeypatch):
    """006 — 수집은 시장과 무관한 공용 풀이다. 사이트마다 한 번씩 돌고,
    이 run의 달력 주(get_run_period)를 그대로 넘긴다. 고정 키워드가 늘어도
    호출 수가 늘지 않는 게 핵심(크레딧이 시장 수와 무관해진다)."""
    monkeypatch.setattr(search_engine.settings, "tavily_api_key", "key")
    monkeypatch.setattr(search_engine, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(search_engine, "get_active_fixed_keywords", lambda conn: [
        {"id": 1, "keyword": "에이전트 도입"}, {"id": 2, "keyword": "교육"},
    ])
    monkeypatch.setattr(search_engine, "get_run_period", lambda conn, run_id: (_START, _END))

    calls = []
    monkeypatch.setattr(
        search_engine, "collect_pool_for_site",
        lambda conn, run_id, domain, start_date, end_date: calls.append((domain, start_date, end_date)),
    )

    search_engine.collect_all(run_id=1)

    assert calls == [(d, _START, _END) for d in search_engine.SITE_DOMAINS]


def test_collect_all_still_needs_an_active_market(monkeypatch):
    """풀 자체는 시장과 무관하지만, 활성 키워드가 없으면 라벨링·판단·화면이
    전부 성립하지 않으므로 조용히 넘어가지 않고 실패로 알린다."""
    monkeypatch.setattr(search_engine.settings, "tavily_api_key", "key")
    monkeypatch.setattr(search_engine, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(search_engine, "get_active_fixed_keywords", lambda conn: [])

    (result,) = search_engine.collect_all(run_id=1)

    assert "활성 키워드 없음" in result["error"]


# ---- collect_pool_for_site (006 공용 풀) ----

def test_pool_queries_each_broad_query_once_per_site(monkeypatch):
    calls = []
    monkeypatch.setattr(
        search_engine, "_fetch_site_results",
        lambda term, domain, start, end: calls.append((term, domain)) or [],
    )

    search_engine.collect_pool_for_site(_FakeConn(), 1, "techcrunch.com", _START, _END)

    assert calls == [(q, "techcrunch.com") for q in search_engine.BROAD_QUERIES_EN]


def test_pool_uses_korean_queries_for_korean_sites(monkeypatch):
    """영어 사이트에 한국어 질의를 던지면 Tavily가 그 도메인 무관 인기글로
    채운다(003·006 실측) — 그래서 사이트 언어에 맞춰 질의를 고른다."""
    calls = []
    monkeypatch.setattr(
        search_engine, "_fetch_site_results",
        lambda term, domain, start, end: calls.append(term) or [],
    )

    search_engine.collect_pool_for_site(_FakeConn(), 1, "aitimes.com", _START, _END)

    assert calls == search_engine.BROAD_QUERIES_KO


def test_pool_stores_without_market_and_marks_fetch_method(monkeypatch):
    """공용 풀 행에는 시장이 없다 — source_name/fetch_method='search'로 저장되고
    "이 기사가 어느 시장에 관련 있나"는 나중에 별도 표가 받는다."""
    monkeypatch.setattr(
        search_engine, "_fetch_site_results",
        lambda term, domain, start, end: [_item("https://techcrunch.com/2026/08/13/a/")],
    )
    conn = _FakeConn()

    search_engine.collect_pool_for_site(conn, 7, "techcrunch.com", _START, _END)

    (params, *_) = conn.inserted_params
    assert params[0] == 7                                    # run_id
    assert params[1] == "TechCrunch"                          # source_name(표시 이름)
    assert params[3] == "https://techcrunch.com/2026/08/13/a" # 정규화된 url
    assert 1 not in params[:2]                                # fixed_keyword_id 자리가 없다


def test_pool_stores_only_allowed_urls(monkeypatch):
    monkeypatch.setattr(search_engine, "_fetch_site_results", lambda term, domain, start, end: [
        _item("https://techcrunch.com/2026/08/13/ok/"),
        _item("https://techcrunch.com/tag/ai/"),      # exclude 패턴
        _item("https://evil.example.com/story"),      # 화이트리스트 외
    ])

    result = search_engine.collect_pool_for_site(_FakeConn(), 1, "techcrunch.com", _START, _END)

    assert result["inserted"] == 1
    assert result["fetched"] == 6   # 질의 2개 × 3건 — 걸러낸 것도 가져온 건 맞다


def test_pool_dedups_same_article_across_broad_queries(monkeypatch):
    """넓은 질의 여러 개가 같은 기사를 물어오는 건 정상 — UNIQUE (run_id, url)로
    한 번만 저장된다."""
    monkeypatch.setattr(
        search_engine, "_fetch_site_results",
        lambda term, domain, start, end: [_item("https://techcrunch.com/2026/08/13/same/")],
    )

    result = search_engine.collect_pool_for_site(_FakeConn(), 1, "techcrunch.com", _START, _END)

    assert (result["fetched"], result["inserted"]) == (2, 1)


def test_pool_returns_error_on_http_failure_without_raising(monkeypatch):
    """한 사이트가 죽어도 나머지 사이트는 계속 진행해야 하므로 예외를 올리지
    않고 error에 담아 리턴한다(파이프라인이 stage_errors로 드러낸다)."""
    def _boom(term, domain, start, end):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(search_engine, "_fetch_site_results", _boom)

    result = search_engine.collect_pool_for_site(_FakeConn(), 1, "aitimes.com", _START, _END)

    assert result["source"] == "AI타임스"
    assert "boom" in result["error"]


# ---- derive_title: 제목이 사이트 이름뿐일 때 스니펫으로 대체 ----
# Techmeme 실측(2026-08-19) — 42건 중 11건이 제목이 "Techmeme"뿐이라
# 라벨링 카드에서 사람이 판단할 수 없었다.

def test_derive_title_keeps_real_titles():
    item = {"title": "Anthropic revenue surges to $65B", "content": "본문 요약"}

    assert search_engine.derive_title(item, "techcrunch.com") == "Anthropic revenue surges to $65B"


@pytest.mark.parametrize("title", ["Techmeme", "techmeme", "techmeme.com", "", "(제목 없음)"])
def test_derive_title_replaces_site_name_with_snippet(title):
    item = {"title": title, "content": "Palona raised a $20M round"}

    assert search_engine.derive_title(item, "techmeme.com") == "Palona raised a $20M round"


def test_derive_title_strips_source_attribution():
    """Techmeme 스니펫은 "기자 / 매체: 헤드라인" 모양이라 앞머리를 떼야
    제목이 읽힌다."""
    item = {"title": "Techmeme", "content": "Mike Wheatley / SiliconANGLE:   Palona raised $20M"}

    assert search_engine.derive_title(item, "techmeme.com") == "Palona raised $20M"


def test_derive_title_keeps_colons_that_are_part_of_the_sentence():
    """본문 콜론까지 지우면 내용이 날아간다 — "@"나 " / "가 있는 출처 표기만 뗀다."""
    item = {"title": "Techmeme", "content": "속보: 오픈AI가 데이터센터를 늘린다"}

    assert search_engine.derive_title(item, "techmeme.com") == "속보: 오픈AI가 데이터센터를 늘린다"


def test_derive_title_truncates_long_snippets_at_word_boundary():
    item = {"title": "Techmeme", "content": "word " * 60}

    result = search_engine.derive_title(item, "techmeme.com")

    assert len(result) <= search_engine.DERIVED_TITLE_MAX + 1   # 말줄임표 한 글자
    assert result.endswith("…")
    assert not result.endswith(" …")


def test_derive_title_falls_back_when_snippet_is_missing_too():
    """조용히 행을 버리지 않는다 — 어떤 후보도 사라지면 안 된다는 기존 원칙."""
    assert search_engine.derive_title({"title": "Techmeme", "content": ""}, "techmeme.com") == "(제목 없음)"


def test_pool_insert_uses_derived_title(monkeypatch):
    monkeypatch.setattr(search_engine, "_fetch_site_results", lambda term, domain, start, end: [
        {"url": "https://www.techmeme.com/260819/p1", "title": "Techmeme",
         "content": "Mike Wheatley / SiliconANGLE: Palona raised $20M", "published_date": "Tue, 11 Aug 2026 16:25:20 GMT"},
    ])
    conn = _FakeConn()

    search_engine.collect_pool_for_site(conn, 1, "techmeme.com", _START, _END)

    (params,) = conn.inserted_params
    assert params[2] == "Palona raised $20M"   # title 자리


def test_derive_title_strips_site_name_prefix():
    """Tavily가 "Techmeme: 실제 헤드라인"처럼 사이트명을 붙여 주는 경우도 있다 —
    출처는 source_name에 이미 있으니 제목에서는 뺀다."""
    item = {"title": "Techmeme: Palona raised $20M", "content": "본문"}

    assert search_engine.derive_title(item, "techmeme.com") == "Palona raised $20M"


def test_pool_skips_duplicate_titles_from_different_urls(monkeypatch):
    """Techmeme 리버 앵커는 URL이 달라도 같은 글을 가리킬 수 있다(실측 41건 중
    6건 중복). 그대로 두면 라벨링에서 같은 내용을 두 번 판단하게 된다."""
    monkeypatch.setattr(search_engine, "_fetch_site_results", lambda term, domain, start, end: [
        {"url": "https://www.techmeme.com/260819/p3", "title": "Techmeme", "content": "같은 헤드라인",
         "published_date": "Tue, 11 Aug 2026 16:25:20 GMT"},
        {"url": "https://www.techmeme.com/260819/p16", "title": "Techmeme", "content": "같은 헤드라인",
         "published_date": "Tue, 11 Aug 2026 16:25:20 GMT"},
    ])
    conn = _FakeConn()

    result = search_engine.collect_pool_for_site(conn, 1, "techmeme.com", _START, _END)

    assert result["inserted"] == 1
    assert [p[2] for p in conn.inserted_params] == ["같은 헤드라인"]


def test_pool_skips_articles_without_a_publication_date(monkeypatch):
    """발행일이 없으면 담지 않는다(2026-08-19 담당자 결정).

    주차가 이 프로젝트의 기준축이라서다 — 화면의 주차 선택, 라벨의 주차 그룹,
    모델 평가의 fold 분리가 전부 발행 주로 돌아간다. 날짜가 없는 기사만 어느
    주에도 속하지 않아 "날짜 미상" 칸이 따로 생기고, 라벨의 주차는 수집 주로
    폴백해 실제 발행 시점과 어긋난다(실측 221건 중 10건뿐이라 예외 경로를
    유지하는 복잡도가 얻는 것보다 컸다)."""
    monkeypatch.setattr(search_engine, "_fetch_site_results", lambda term, domain, start, end: [
        _item("https://techcrunch.com/2026/08/13/dated/"),
        _item("https://techcrunch.com/2026/08/13/undated/", published_date=None),
    ])

    result = search_engine.collect_pool_for_site(_FakeConn(), 1, "techcrunch.com", _START, _END)

    assert result["inserted"] == 1        # 날짜 있는 것만
    assert result["fetched"] == 4         # 가져오긴 다 가져왔다(질의 2개 × 2건)
