"""GeekNews Weekly(news.hada.io/weekly) 전용 수집기.

RSS가 없어 스크래핑으로 대체한다(2026-08-10, 담당자 확인). GeekNews의 원본
파이어호스(collectors/rss.py가 이미 수집 중)는 Show GN·개인 프로젝트 소개
같은 노이즈가 섞이는데, Weekly는 사람이 직접 30건 안팎을 골라 큐레이터
코멘트까지 붙인 것이라 노이즈가 훨씬 적다.

파싱 대상 HTML 구조(실측, 2026-08-10 기준 — 사이트 개편 시 깨질 수 있음):
    아카이브(/weekly): <a href='/weekly/{code}' ...> — 최신 주차 코드를 얻는다
    개별 주차(/weekly/{code}):
        <li id='topic-{id}' class='weekly-topic-item'>
          <a href='{url}' class='link bold'>{title}</a>
          <div class='content'>{큐레이터 코멘트, HTML}</div>
        </li>
    기간: <span class='weekly-news-period'>: YYYY-MM-DD – YYYY-MM-DD</span>

RSS 기반 수집기(rss.py)와 인터페이스를 맞추되, feedparser를 안 쓰므로
collect_all()의 일반 루프에 안 넣는다 — sources.source_type='crawl'로
등록해 collect_all()의 WHERE 절(rss/aggregator/api만 포함)에서 자연히
빠지고, pipeline.py에서 별도 단계로 호출한다.
"""

import html
import re
from datetime import datetime, timedelta, timezone

import httpx

from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.text import strip_html
from tech_monitoring.utils.url_normalize import normalize_url

ARCHIVE_URL = "https://news.hada.io/weekly"
ISSUE_URL_TEMPLATE = "https://news.hada.io/weekly/{code}"
USER_AGENT = "Mozilla/5.0 (compatible; tech-monitoring-mcp/0.1; +internal use)"

_LATEST_CODE_RE = re.compile(r"/weekly/(\d{5,6})'")
_PERIOD_RE = re.compile(
    r"weekly-news-period'>:\s*(\d{4}-\d{2}-\d{2})\s*[-–—]\s*(\d{4}-\d{2}-\d{2})"
)
_TOPIC_ITEM_RE = re.compile(
    r"<li id='topic-(\d+)' class='weekly-topic-item'>"
    r"<a href='([^']+)' class='link bold'>([^<]+)</a>"
    r"\s*<div class='content'>(.*?)</div></li>",
    re.DOTALL,
)


def parse_latest_code(archive_html: str) -> str | None:
    """아카이브 목록 HTML에서 가장 최근 주차 코드(예: '202632')를 뽑는다.

    페이지에 최신 순으로 나열되므로 첫 매치가 최신호다.
    """
    match = _LATEST_CODE_RE.search(archive_html)
    return match.group(1) if match else None


def parse_period(issue_html: str) -> tuple[datetime | None, datetime | None]:
    """이슈 페이지에서 집계 기간(시작~끝)을 뽑는다. 개별 항목엔 발행 시각이
    없어 이 기간의 끝 날짜를 published_at 근사치로 쓴다."""
    match = _PERIOD_RE.search(issue_html)
    if not match:
        return None, None
    start = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(match.group(2)).replace(tzinfo=timezone.utc)
    return start, end


def parse_weekly_issue(issue_html: str) -> list[dict]:
    """이슈 페이지 HTML에서 큐레이션된 항목 목록을 뽑는다.

    각 항목의 summary는 GeekNews 큐레이터가 직접 쓴 코멘트다 — 원문 요약이
    아니라 "왜 볼만한가"에 대한 해설이라 다른 소스의 summary와 성격이 다르다.
    """
    items = []
    for topic_id, url, title, content_html in _TOPIC_ITEM_RE.findall(issue_html):
        items.append({
            "topic_id": int(topic_id),
            "url": url,
            "title": html.unescape(title).strip(),
            "summary": strip_html(content_html).strip(),
        })
    return items


def collect_geeknews_weekly(conn) -> dict:
    """최신 주차 하나를 가져와 적재한다. 이미 있는 항목은 url_canonical
    UNIQUE 제약으로 자연히 건너뛰므로, 매번 최신호만 다시 가져와도 안전하다
    (한 주에 한 번만 바뀌므로 대부분의 실행은 그냥 중복으로 스킵된다)."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, source_trust FROM sources WHERE name = 'GeekNews Weekly'")
        row = cur.fetchone()
    if row is None:
        return {"source": "GeekNews Weekly", "fetched": 0, "inserted": 0, "error": "source not configured"}
    source_id, source_trust = row

    try:
        archive_resp = httpx.get(ARCHIVE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        archive_resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"source": "GeekNews Weekly", "fetched": 0, "inserted": 0, "error": str(exc)}

    code = parse_latest_code(archive_resp.text)
    if code is None:
        return {"source": "GeekNews Weekly", "fetched": 0, "inserted": 0, "error": "최신 주차 코드 파싱 실패"}

    try:
        issue_resp = httpx.get(
            ISSUE_URL_TEMPLATE.format(code=code), headers={"User-Agent": USER_AGENT}, timeout=15
        )
        issue_resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"source": "GeekNews Weekly", "fetched": 0, "inserted": 0, "error": str(exc)}

    _, period_end = parse_period(issue_resp.text)
    # 기간 끝조차 못 뽑으면 "오늘"로 근사 — 완전히 버리는 것보단 낫다
    published_at = period_end or datetime.now(timezone.utc)
    items = parse_weekly_issue(issue_resp.text)

    inserted = 0
    with conn.cursor() as cur:
        for item in items:
            url_canonical = normalize_url(item["url"])
            cur.execute(
                """
                INSERT INTO articles
                    (source_id, source, source_type, source_trust,
                     title, url, url_canonical, summary, published_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url_canonical) DO NOTHING
                RETURNING id
                """,
                (
                    source_id, "GeekNews Weekly", "crawl", source_trust,
                    item["title"], item["url"], url_canonical, item["summary"], published_at,
                ),
            )
            if cur.fetchone() is not None:
                inserted += 1

        cur.execute("UPDATE sources SET last_collected_at = now() WHERE id = %s", (source_id,))

    return {"source": "GeekNews Weekly", "fetched": len(items), "inserted": inserted, "error": None, "issue": code}


if __name__ == "__main__":
    _conn = get_connection()
    print(collect_geeknews_weekly(_conn))
    _conn.close()
