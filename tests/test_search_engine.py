"""검색엔진 수집기(collectors/search_engine.py) 페이지네이션·저장 로직 테스트.
실제 네트워크·DB 없이 _fetch_page와 conn.cursor()를 스텁으로 대체한다
(tests/test_rss_backoff_and_jitter.py와 같은 패턴).
"""

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
    return {"link": url, "title": "제목", "snippet": "요약", "displayLink": "example.com"}


def test_collect_for_keyword_paginates_until_target_reached(monkeypatch):
    """target_count=15면 10+10=20건으로 목표를 넘기는 2번째 페이지에서 멈춰야 한다
    (3번째 페이지는 아예 요청하지 않음 — 불필요한 쿼리로 무료 한도를 낭비하지 않기 위해)."""
    pages = [
        {"items": [_item(f"https://example.com/{i}") for i in range(10)]},
        {"items": [_item(f"https://example.com/{i}") for i in range(10, 20)]},
        {"items": [_item(f"https://example.com/{i}") for i in range(20, 30)]},
    ]
    calls = []

    def fake_fetch_page(keyword, start):
        calls.append(start)
        return pages[len(calls) - 1]

    monkeypatch.setattr(search_engine, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(search_engine.time, "sleep", lambda s: None)

    result = search_engine.collect_for_keyword(
        _FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"}, target_count=15,
    )

    assert calls == [1, 11]
    assert result["fetched"] == 20
    assert result["inserted"] == 20
    assert result["error"] is None


def test_collect_for_keyword_stops_on_empty_page_without_error(monkeypatch):
    """dateRestrict=w1 범위 안에서 결과가 목표 건수보다 먼저 소진되면 정상 종료(에러 아님)."""
    pages = [{"items": [_item("https://example.com/1")]}, {"items": []}]
    calls = []

    def fake_fetch_page(keyword, start):
        calls.append(start)
        return pages[len(calls) - 1]

    monkeypatch.setattr(search_engine, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(search_engine.time, "sleep", lambda s: None)

    result = search_engine.collect_for_keyword(
        _FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"}, target_count=50,
    )

    assert result["fetched"] == 1
    assert result["inserted"] == 1
    assert result["error"] is None


def test_collect_for_keyword_dedups_same_url_within_run(monkeypatch):
    """검색결과에 같은 URL이 중복으로 잡혀도 inserted는 1건만 세야 한다."""
    pages = [
        {"items": [_item("https://example.com/dup"), _item("https://example.com/dup")]},
        {"items": []},  # 다음 페이지부터는 결과 없음 — 목표 건수(10) 미달이어도 정상 종료
    ]
    calls = []

    def fake_fetch_page(keyword, start):
        calls.append(start)
        return pages[len(calls) - 1]

    monkeypatch.setattr(search_engine, "_fetch_page", fake_fetch_page)
    monkeypatch.setattr(search_engine.time, "sleep", lambda s: None)

    result = search_engine.collect_for_keyword(
        _FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"}, target_count=10,
    )

    assert result["fetched"] == 2
    assert result["inserted"] == 1


def test_collect_for_keyword_returns_error_on_http_failure(monkeypatch):
    """네트워크 실패는 예외를 던지지 않고 error 필드로 보고해야 한다(파이프라인 격리)."""

    def fake_fetch_page(keyword, start):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(search_engine, "_fetch_page", fake_fetch_page)

    result = search_engine.collect_for_keyword(
        _FakeConn(), run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"}, target_count=10,
    )

    assert result["error"] == "boom"
    assert result["fetched"] == 0
    assert result["inserted"] == 0


def test_search_once_returns_items(monkeypatch):
    monkeypatch.setattr(search_engine.settings, "google_search_api_key", "key")
    monkeypatch.setattr(search_engine.settings, "google_search_cx", "cx")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [_item("https://example.com/a")]}

    monkeypatch.setattr(search_engine.httpx, "get", lambda url, params, timeout: _FakeResponse())

    results = search_engine.search_once("AX 시장")

    assert len(results) == 1
    assert results[0]["link"] == "https://example.com/a"


def test_search_once_returns_empty_list_without_credentials(monkeypatch):
    monkeypatch.setattr(search_engine.settings, "google_search_api_key", None)
    monkeypatch.setattr(search_engine.settings, "google_search_cx", None)

    assert search_engine.search_once("AX 시장") == []


def test_search_once_returns_empty_list_on_http_error(monkeypatch):
    monkeypatch.setattr(search_engine.settings, "google_search_api_key", "key")
    monkeypatch.setattr(search_engine.settings, "google_search_cx", "cx")

    def fake_get(url, params, timeout):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(search_engine.httpx, "get", fake_get)

    assert search_engine.search_once("AX 시장") == []


def test_collect_all_reports_missing_credentials(monkeypatch):
    monkeypatch.setattr(search_engine.settings, "google_search_api_key", None)
    monkeypatch.setattr(search_engine.settings, "google_search_cx", None)

    results = search_engine.collect_all(run_id=1)

    assert len(results) == 1
    assert "GOOGLE_SEARCH_API_KEY" in results[0]["error"]
