from tech_monitoring.collectors.geeknews_weekly import (
    parse_latest_code,
    parse_period,
    parse_weekly_issue,
)

# 아래 HTML은 news.hada.io를 실제로 조회해 얻은 구조를 최소 재현한 것이다
# (2026-08-10 실측 — collectors/geeknews_weekly.py 상단 docstring 참고).

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


def test_parse_weekly_issue_extracts_all_items_with_clean_summary():
    items = parse_weekly_issue(ISSUE_HTML_SAMPLE)

    assert len(items) == 2
    assert items[0]["topic_id"] == 32156
    assert items[0]["url"] == "https://news.hada.io/topic?id=32156"
    assert items[0]["title"] == "Canva는 어떻게 수억 건의 사용자 세션을 빠르고 안전하게 유지할까?"
    assert "<" not in items[0]["summary"]  # HTML 태그 제거 확인
    assert "복잡성만 늘어난다" in items[0]["summary"]


def test_parse_weekly_issue_unescapes_html_entities_in_title():
    items = parse_weekly_issue(ISSUE_HTML_SAMPLE)
    # &#039;가 원래 작은따옴표로 복원돼야 한다
    assert "'A.X K2 Raon-Speech'" in items[1]["title"]


def test_parse_weekly_issue_returns_empty_list_when_no_items():
    assert parse_weekly_issue("<html>이번 주는 쉽니다</html>") == []
