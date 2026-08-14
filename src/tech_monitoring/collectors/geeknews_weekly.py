"""v3: GeekNews Weekly(news.hada.io/weekly) 전용 스크래퍼.

RSS가 없어 스크래핑한다(v1에서 2026-08-10 확인한 사실 — 사이트 구조는
2026-08-13 재확인해도 동일). GeekNews 원본 파이어호스(HN처럼 Show GN·개인
프로젝트 소개 같은 노이즈가 섞임)와 달리 Weekly는 사람이 직접 30건 안팎을
골라 큐레이터 코멘트까지 붙인 것이라 노이즈가 훨씬 적다 — v3 재선정
사이트 4개 중 하나로 다시 채택(README "v2 vs v3 비교 실험" 참고).

v1과의 차이: v1은 sources/articles 테이블(소스별 source_trust 등)을 썼지만
v3엔 그런 테이블이 없다 — collected_articles에 source_name='GeekNews Weekly'
로 직접 적재한다(고정 키워드 무관, 관련도 판단은 analysis/relevance_filter.py
가 나중에 LLM으로 한다).

파싱 대상 HTML 구조(실측, 2026-08-13 재확인 — 사이트 개편 시 깨질 수 있음):
    아카이브(/weekly): <a href='/weekly/{code}' ...> — 최신 주차 코드를 얻는다
    개별 주차(/weekly/{code}):
        <li id='topic-{id}' class='weekly-topic-item'>
          <a href='{url}' class='link bold'>{title}</a>
          <div class='content'>{큐레이터 코멘트, HTML}</div>
        </li>
    기간: <span class='weekly-news-period'>: YYYY-MM-DD – YYYY-MM-DD</span>

    ./.venv/Scripts/python.exe -m tech_monitoring.collectors.geeknews_weekly
"""

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.text import strip_html
from tech_monitoring.utils.url_normalize import normalize_url

SOURCE_NAME = "GeekNews Weekly"
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
    페이지에 최신 순으로 나열되므로 첫 매치가 최신호다."""
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

    각 항목의 snippet은 GeekNews 큐레이터가 직접 쓴 코멘트다 — 원문 요약이
    아니라 "왜 볼만한가"에 대한 해설이라 다른 소스의 snippet과 성격이 다르다."""
    items = []
    for topic_id, url, title, content_html in _TOPIC_ITEM_RE.findall(issue_html):
        items.append({
            "topic_id": int(topic_id),
            "url": url,
            "title": html.unescape(title).strip(),
            "snippet": strip_html(content_html).strip(),
        })
    return items


def _extract_domain(url: str) -> str:
    return urlsplit(url).netloc


def _insert_article(conn, *, run_id: int, item: dict, published_at) -> bool:
    url = item.get("url")
    if not url:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO collected_articles
                (run_id, source_name, fetch_method, title, url, source_domain, snippet, published_at)
            VALUES (%s, %s, 'scrape', %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, url) DO NOTHING
            RETURNING id
            """,
            (
                run_id, SOURCE_NAME, item["title"], normalize_url(url),
                _extract_domain(url), item.get("snippet"), published_at,
            ),
        )
        return cur.fetchone() is not None


def collect_geeknews_weekly(conn, run_id: int) -> dict:
    """최신 주차 하나를 가져와 적재한다. 이미 있는 항목은 UNIQUE(run_id, url)
    로 자연히 건너뛰므로, 매번 최신호만 다시 가져와도 안전하다(한 주에 한
    번만 바뀌므로 대부분의 실행은 그냥 중복으로 스킵된다)."""
    try:
        archive_resp = httpx.get(ARCHIVE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        archive_resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"source": SOURCE_NAME, "fetched": 0, "inserted": 0, "error": str(exc)}

    code = parse_latest_code(archive_resp.text)
    if code is None:
        return {"source": SOURCE_NAME, "fetched": 0, "inserted": 0, "error": "최신 주차 코드 파싱 실패"}

    try:
        issue_resp = httpx.get(
            ISSUE_URL_TEMPLATE.format(code=code), headers={"User-Agent": USER_AGENT}, timeout=15
        )
        issue_resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"source": SOURCE_NAME, "fetched": 0, "inserted": 0, "error": str(exc)}

    _, period_end = parse_period(issue_resp.text)
    # 기간 끝조차 못 뽑으면 "오늘"로 근사 — 완전히 버리는 것보단 낫다
    published_at = period_end or datetime.now(timezone.utc)
    items = parse_weekly_issue(issue_resp.text)

    inserted = sum(_insert_article(conn, run_id=run_id, item=item, published_at=published_at) for item in items)

    return {"source": SOURCE_NAME, "fetched": len(items), "inserted": inserted, "error": None, "issue": code}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from tech_monitoring.db.weekly_run import start_weekly_run

    _conn = get_connection()
    try:
        _run_id = start_weekly_run(_conn)
        print(collect_geeknews_weekly(_conn, _run_id))
    finally:
        _conn.close()
