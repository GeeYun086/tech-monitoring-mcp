"""collectors/geeknews_weekly.py 테스트(v3). 실제 네트워크·DB 없이 httpx.get과
conn.cursor()를 스텁으로 대체한다."""

import httpx

from tech_monitoring.collectors.geeknews_weekly import (
    collect_geeknews_weekly,
    parse_latest_code,
    parse_period,
    parse_weekly_issue,
)

# 아래 HTML은 news.hada.io를 실제로 조회해 얻은 구조를 최소 재현한 것이다
# (2026-08-10 v1에서 실측, 2026-08-13 v3에서 동일 구조 재확인 — 모듈 docstring 참고).

ARCHIVE_HTML_SAMPLE = """
<div class=weekly><div>370. </div><div>202632</div><div><a href='/weekly/202632' class='u'> 서버를 키우기 전에 물어야 할 것</a></div>
<div>369. </div><div>202631</div><div><a href='/weekly/202631' class='u'> 지표가 좋아 보이는 회사가 성장을 멈추는 이유</a></div>
</div>
"""

ISSUE_HTML_SAMPLE = """
<h3 class='keepall'>이번 주 주요 뉴스 <span class='weekly-news-period'>: 2026-08-03 – 2026-08-09</span></h3>
<div class='topics'><ul>
<li id='topic-32156' class='weekly-topic-item'><a href='https://news.hada.io/topic?id=32156' class='link bold'>Canva는 어떻게 수억 건의 사용자 세션을 빠르고 안전하게 유지할까?</a>
<div class='content'><p>Redis를 검토했다가 <strong>복잡성만 늘어난다</strong>는 이유로 접은 판단부터 눈에 들어옵니다.</p>
</div></li>
<li id='topic-32125' class='weekly-topic-item'><a href='https://news.hada.io/topic?id=32125' class='link bold'>크래프톤, 21B 한영 이중언어 음성 AI 모델 &#039;A.X K2 Raon-Speech&#039; 공개</a>
<div class='content'><p>30B 이하 공개 음성 모델 중 <strong>한국어 종합 1위</strong>를 기록했습니다.</p>
</div></li>
</ul></div>
"""


def test_parse_latest_code_returns_the_first_and_newest_issue():
    assert parse_latest_code(ARCHIVE_HTML_SAMPLE) == "202632"


def test_parse_latest_code_returns_none_when_not_found():
    assert parse_latest_code("<html>텅 비어있음</html>") is None


def test_parse_period_extracts_start_and_end_dates():
    start, end = parse_period(ISSUE_HTML_SAMPLE)
    assert start.isoformat() == "2026-08-03T00:00:00+00:00"
    assert end.isoformat() == "2026-08-09T00:00:00+00:00"


def test_parse_period_returns_none_when_missing():
    assert parse_period("<html>기간 표시 없음</html>") == (None, None)


def test_parse_weekly_issue_extracts_all_items_with_clean_snippet():
    items = parse_weekly_issue(ISSUE_HTML_SAMPLE)

    assert len(items) == 2
    assert items[0]["topic_id"] == 32156
    assert items[0]["url"] == "https://news.hada.io/topic?id=32156"
    assert items[0]["title"] == "Canva는 어떻게 수억 건의 사용자 세션을 빠르고 안전하게 유지할까?"
    assert "<" not in items[0]["snippet"]  # HTML 태그 제거 확인
    assert "복잡성만 늘어난다" in items[0]["snippet"]


def test_parse_weekly_issue_unescapes_html_entities_in_title():
    items = parse_weekly_issue(ISSUE_HTML_SAMPLE)
    # &#039;가 원래 작은따옴표로 복원돼야 한다
    assert "'A.X K2 Raon-Speech'" in items[1]["title"]


def test_parse_weekly_issue_returns_empty_list_when_no_items():
    assert parse_weekly_issue("<html>이번 주는 쉽니다</html>") == []


# ---- collect_geeknews_weekly (오케스트레이션, v3: collected_articles 직접 적재) ----

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


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


def test_collect_geeknews_weekly_fetches_archive_then_latest_issue(monkeypatch):
    responses = [_FakeResponse(ARCHIVE_HTML_SAMPLE), _FakeResponse(ISSUE_HTML_SAMPLE)]

    def fake_get(url, headers=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(httpx, "get", fake_get)

    result = collect_geeknews_weekly(_FakeConn(), run_id=1)

    assert result == {"source": "GeekNews Weekly", "fetched": 2, "inserted": 2, "error": None, "issue": "202632"}


def test_collect_geeknews_weekly_returns_error_when_archive_fetch_fails(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(httpx, "get", fake_get)

    result = collect_geeknews_weekly(_FakeConn(), run_id=1)

    assert result["error"] == "boom"
    assert result["fetched"] == 0


def test_collect_geeknews_weekly_returns_error_when_code_parsing_fails(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=None: _FakeResponse("<html>텅 빔</html>"))

    result = collect_geeknews_weekly(_FakeConn(), run_id=1)

    assert result["error"] == "최신 주차 코드 파싱 실패"
