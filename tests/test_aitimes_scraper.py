"""collectors/aitimes_scraper.py 테스트. 실제 네트워크·DB 없이 fetch_article_list와
conn.cursor()를 스텁으로 대체한다. HTML 샘플은 2026-08-13 실제 목록 페이지
(sc_section_code=S1N3, view_type=sm) 구조를 그대로 재현한 것(모듈 docstring 참고)."""

from datetime import date, datetime, timezone, timedelta

import httpx

from tech_monitoring.collectors import aitimes_scraper

LIST_HTML_SAMPLE = """
<article id="section-list" class="altlist-body">
<ul class="altlist-text">
    <li class="altlist-text-item">
        <div class="altlist-text-group">
            <H2 class="altlist-subject">
                <a href="https://www.aitimes.com/news/articleView.html?idxno=213904" target="_top">
                    마스오토, &apos;AME 2026&apos;서 AI 자율주행 트럭 실물 최초 공개
                </a>
            </H2>
        </div>
        <div class="altlist-info">
            <div class="altlist-info-item">AI 기업</div>
            <div class="altlist-info-item">김해원 기자</div>
            <div class="altlist-info-item">08-13 15:28</div>
        </div>
    </li>
    <li class="altlist-text-item">
        <div class="altlist-text-group">
            <H2 class="altlist-subject">
                <a href="https://www.aitimes.com/news/articleView.html?idxno=213908" target="_top">
                    OCP 재단이 주최하는 &apos;2026 OCP 코리아 테크 데이&apos;, 21일 코엑스서 개최
                </a>
            </H2>
        </div>
        <div class="altlist-info">
            <div class="altlist-info-item">이벤트</div>
            <div class="altlist-info-item">임대준 기자</div>
            <div class="altlist-info-item">08-13 15:26</div>
        </div>
    </li>
</ul>
</article>
"""


# ---- parse_article_list ----

def test_parse_article_list_extracts_title_url_category_and_datetime():
    items = aitimes_scraper.parse_article_list(LIST_HTML_SAMPLE, reference_date=date(2026, 8, 13))

    assert len(items) == 2
    assert items[0]["url"] == "https://www.aitimes.com/news/articleView.html?idxno=213904"
    assert items[0]["title"] == "마스오토, 'AME 2026'서 AI 자율주행 트럭 실물 최초 공개"  # &apos; 복원 확인
    assert items[0]["snippet"] == "AI 기업"
    assert items[0]["published_at"] == datetime(2026, 8, 13, 15, 28, tzinfo=timezone(timedelta(hours=9)))


def test_parse_article_list_returns_empty_list_when_no_items():
    assert aitimes_scraper.parse_article_list("<html>기사가 없습니다</html>") == []


def test_parse_article_list_uses_todays_year_by_default():
    items = aitimes_scraper.parse_article_list(LIST_HTML_SAMPLE)
    assert items[0]["published_at"].year == date.today().year


# ---- _parse_kr_datetime: 연도 없는 "MM-DD HH:MM" 형식 처리 ----

def test_parse_kr_datetime_infers_previous_year_across_year_boundary():
    """1월 초에 수집하는데 기사 날짜가 12월이면(30일 이상 미래로 계산됨) 작년으로 봐야 한다."""
    result = aitimes_scraper._parse_kr_datetime("12-30 10:00", reference_date=date(2027, 1, 3))
    assert result.year == 2026


def test_parse_kr_datetime_returns_none_for_malformed_text():
    assert aitimes_scraper._parse_kr_datetime("이상한 형식", reference_date=date(2026, 8, 13)) is None


# ---- collect_aitimes ----

class _FakeCursor:
    def __init__(self, inserted_urls: set[str]):
        self._inserted_urls = inserted_urls
        self._last_url = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        assert "INSERT INTO collected_articles" in query
        self._last_url = params[3]  # (run_id, source_name, title, url, ...)

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


def test_collect_aitimes_reports_fetched_and_inserted_counts(monkeypatch):
    monkeypatch.setattr(aitimes_scraper, "fetch_article_list", lambda: [
        {"url": "https://www.aitimes.com/news/articleView.html?idxno=1", "title": "제목1", "snippet": "AI 기업", "published_at": None},
        {"url": "https://www.aitimes.com/news/articleView.html?idxno=2", "title": "제목2", "snippet": "산업일반", "published_at": None},
    ])

    result = aitimes_scraper.collect_aitimes(_FakeConn(), run_id=1)

    assert result == {"source": "AI타임스", "fetched": 2, "inserted": 2, "error": None}


def test_collect_aitimes_returns_error_on_http_failure(monkeypatch):
    def fake_fetch():
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(aitimes_scraper, "fetch_article_list", fake_fetch)

    result = aitimes_scraper.collect_aitimes(_FakeConn(), run_id=1)

    assert result == {"source": "AI타임스", "fetched": 0, "inserted": 0, "error": "boom"}


def test_collect_aitimes_dedups_on_rerun(monkeypatch):
    monkeypatch.setattr(aitimes_scraper, "fetch_article_list", lambda: [
        {"url": "https://www.aitimes.com/news/articleView.html?idxno=1", "title": "제목1", "snippet": None, "published_at": None},
    ])
    conn = _FakeConn()

    first = aitimes_scraper.collect_aitimes(conn, run_id=1)
    second = aitimes_scraper.collect_aitimes(conn, run_id=1)

    assert first["inserted"] == 1
    assert second["inserted"] == 0
