"""v3: 재선정 사이트 중 표준 RSS를 제공하는 2곳(Techmeme·TechCrunch) 전용
수집기 — 고정 키워드 무관하게 사이트 전체를 가져온다(collectors/search_engine.py
와 달리 관련도 판단은 여기서 하지 않고 analysis/relevance_filter.py가 LLM으로
사후 판단한다. 모듈 docstring·README "v2 vs v3 비교 실험" 참고).

표준 RSS 2.0이라 별도 파싱 라이브러리 없이 stdlib xml.etree.ElementTree로
충분하다(프로젝트에 feedparser 등 RSS 전용 의존성이 없어 최소한으로 유지 —
2026-08-13 실제 두 피드로 확인).

GeekNews Weekly·AI타임스는 RSS가 없어(collectors/geeknews_weekly.py·
collectors/aitimes_scraper.py 참고) 별도 스크래핑 모듈로 분리했다.

    ./.venv/Scripts/python.exe -m tech_monitoring.collectors.rss_collector
"""

from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.text import strip_html
from tech_monitoring.utils.url_normalize import normalize_url

USER_AGENT = "Mozilla/5.0 (compatible; tech-monitoring-mcp/0.1; +internal use)"

# 2026-08-13 실측: 둘 다 표준 RSS 2.0, robots.txt 위반 없음(v1 조사·
# 오늘 재확인 근거는 README "v2 vs v3 비교 실험" 참고).
RSS_SOURCES = [
    {"name": "Techmeme", "feed_url": "https://www.techmeme.com/feed.xml"},
    {"name": "TechCrunch", "feed_url": "https://techcrunch.com/feed/"},
]


def _parse_pubdate(value: str | None):
    """RFC 2822(RSS 표준 pubDate 형식) — collectors/search_engine.py의
    _parse_published_date와 같은 이유로 파싱 실패는 예외 대신 None 폴백
    (정렬 참고값일 뿐 필수 데이터가 아님)."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def parse_rss_items(xml_text: str) -> list[dict]:
    """RSS <item> 목록을 (title, url, snippet, published_at) 형태로 뽑는다.
    <description>은 사이트에 따라 HTML이 섞여 오므로(Techmeke가 특히 그렇다
    — 썸네일 이미지·서식 태그 포함) strip_html로 정리한다."""
    root = ElementTree.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        snippet = strip_html(item.findtext("description") or "").strip()
        items.append({
            "title": title,
            "url": link,
            "snippet": snippet,
            "published_at": _parse_pubdate(item.findtext("pubDate")),
        })
    return items


def fetch_rss(feed_url: str) -> list[dict]:
    resp = httpx.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return parse_rss_items(resp.text)


def _extract_domain(url: str) -> str:
    return urlsplit(url).netloc


def _insert_article(conn, *, run_id: int, source_name: str, item: dict) -> bool:
    url = item.get("url")
    if not url:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO collected_articles
                (run_id, source_name, fetch_method, title, url, source_domain, snippet, published_at)
            VALUES (%s, %s, 'rss', %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, url) DO NOTHING
            RETURNING id
            """,
            (
                run_id, source_name, item["title"], normalize_url(url),
                _extract_domain(url), item.get("snippet"), item.get("published_at"),
            ),
        )
        return cur.fetchone() is not None


def collect_source(conn, run_id: int, source: dict) -> dict:
    try:
        items = fetch_rss(source["feed_url"])
    except httpx.HTTPError as exc:
        return {"source": source["name"], "fetched": 0, "inserted": 0, "error": str(exc)}

    inserted = sum(
        _insert_article(conn, run_id=run_id, source_name=source["name"], item=item)
        for item in items
    )
    return {"source": source["name"], "fetched": len(items), "inserted": inserted, "error": None}


def collect_all(conn, run_id: int) -> list[dict]:
    return [collect_source(conn, run_id, source) for source in RSS_SOURCES]


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from tech_monitoring.db.weekly_run import start_weekly_run

    _conn = get_connection()
    try:
        _run_id = start_weekly_run(_conn)
        for _result in collect_all(_conn, _run_id):
            print(_result)
    finally:
        _conn.close()
