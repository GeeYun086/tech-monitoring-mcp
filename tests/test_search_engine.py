"""검색엔진 수집기(collectors/search_engine.py, Tavily 기반) 테스트.
실제 네트워크·DB 없이 _fetch_site_results와 conn.cursor()를 스텁으로 대체한다."""

from datetime import datetime, timezone

import httpx
import pytest

from tech_monitoring.collectors import search_engine


class _FakeCursor:
    """INSERT INTO search_results ... RETURNING id 만 이해하는 최소 스텁.
    UNIQUE(run_id, fixed_keyword_id, url) ON CONFLICT DO NOTHING을
    inserted_urls 집합으로 흉내낸다 — 이미 있으면 fetchone()이 None(=미삽입)."""

    def __init__(self, inserted_urls: set[str]):
        self._inserted_urls = inserted_urls
        self._last_url = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        assert "INSERT INTO search_results" in query
        self._last_url = params[5]  # (run_id, fixed_keyword_id, query, rank, title, url, ...)

    def fetchone(self):
        if self._last_url in self._inserted_urls:
            return None
        self._inserted_urls.add(self._last_url)
        return (1,)


class _FakeConn:
    def __init__(self):
        self.inserted_urls: set[str] = set()

    def cursor(self):
        return _FakeCursor(self.inserted_urls)


def _item(url: str) -> dict:
    return {"url": url, "title": "제목", "content": "요약"}


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


# ---- collect_for_keyword ----

def test_collect_for_keyword_queries_each_site_domain_separately(monkeypatch):
    """사이트 하나에 결과가 쏠리는 걸 막기 위해 화이트리스트 사이트마다 개별 호출해야 한다."""
    calls = []

    def fake_fetch(keyword, domain):
        calls.append(domain)
        return []

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    search_engine.collect_for_keyword(_FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "에이전트 도입"})

    assert calls == search_engine.SITE_DOMAINS


def test_collect_for_keyword_stores_only_allowed_urls(monkeypatch):
    """Tavily가 도메인은 맞혀도 화이트리스트 경로 밖의 URL(예: techmeme.com/river)을
    돌려주면 저장 단계에서 다시 걸러져야 한다(이중 강제)."""
    def fake_fetch(keyword, domain):
        if domain == "techmeme.com":
            return [_item("https://www.techmeme.com/260813/p1"), _item("https://www.techmeme.com/river")]
        return []

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    result = search_engine.collect_for_keyword(_FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "교육"})

    assert result["fetched"] == 2  # Tavily가 준 원본 개수
    assert result["inserted"] == 1  # 화이트리스트 통과한 것만 저장
    assert result["error"] is None


def test_collect_for_keyword_dedups_same_url_across_sites(monkeypatch):
    def fake_fetch(keyword, domain):
        return [_item("https://techcrunch.com/2026/08/13/story/")]

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    result = search_engine.collect_for_keyword(_FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "교육"})

    # 6개 사이트 전부 같은 URL을 돌려줘도(스텁이라 실제로는 안 그러겠지만) 1건만 저장돼야 함
    assert result["inserted"] == 1


def test_collect_for_keyword_returns_error_on_http_failure(monkeypatch):
    """한 사이트 호출이 실패해도 예외를 던지지 않고 error 필드로 보고해야 한다(파이프라인 격리)."""
    def fake_fetch(keyword, domain):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(search_engine, "_fetch_site_results", fake_fetch)

    result = search_engine.collect_for_keyword(_FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "교육"})

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
