import calendar
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.text import strip_html
from tech_monitoring.utils.url_normalize import normalize_url

# 일부 사이트는 UA 없는 요청을 봇으로 간주해 403을 반환함 (리서치 문서 "크롤링 여부" 권고 반영)
USER_AGENT = "Mozilla/5.0 (compatible; tech-monitoring-mcp/0.1; +internal use)"
REQUEST_TIMEOUT_SECONDS = 20

# 2026-08-11: collect_all()이 소스 20여 개를 지연 없이 연속 호출하고 있었다.
# arXiv처럼 "3초당 1회 이하" 요청을 권고하는 소스가 섞여 있어, 매번 규칙적인
# 간격 없이 몰아치면 레이트리밋에 걸리기 쉽다. 소스 간에 무작위 지연을 둬서
# 패턴을 흐트러뜨리고 arXiv 권고치보다 여유 있게 유지한다.
SOURCE_DELAY_RANGE_SECONDS = (1.0, 3.0)

# arXiv API의 "Rate exceeded."(HTTP 429)는 일시적이므로 짧게 백오프 후 재시도하면
# 성공할 가능성이 있다. 단, 403 같은 영구 차단을 매번 재시도하며 시간을 낭비하던
# 문제(72267e0)가 재발하지 않도록 429 이외의 상태 코드는 재시도하지 않는다.
RATE_LIMIT_MAX_RETRIES = 2
RATE_LIMIT_BASE_DELAY_SECONDS = 3.0  # arXiv 권고("3초당 1회 이하")에 맞춘 기본 대기

# hnrss 피드는 본문에 "<p>Points: 190</p>" 형태로 반향 수치를 실어 보낸다.
# 설계서 v2.0 §5의 파급력 신호 "aggregator points"의 실제 소스.
_HN_POINTS_RE = re.compile(r"Points:\s*(\d+)")


def _entry_published_at(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


def parse_hn_points(summary: str | None) -> int | None:
    match = _HN_POINTS_RE.search(summary or "")
    return int(match.group(1)) if match else None


def _get_with_rate_limit_backoff(url: str) -> httpx.Response:
    """429(레이트리밋)에 한해 지수 백오프로 재시도한다.

    서버가 Retry-After 헤더를 주면 그 값을 우선 쓰고, 없으면 3초부터 시작해 매
    재시도마다 2배로 늘린다(RATE_LIMIT_BASE_DELAY_SECONDS). 403 등 다른 상태
    코드는 여기서 재시도하지 않고 그대로 반환해 호출부가 즉시 판단하게 한다.
    """
    delay = RATE_LIMIT_BASE_DELAY_SECONDS
    resp = httpx.get(
        url, headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True,
    )
    for _ in range(RATE_LIMIT_MAX_RETRIES):
        if resp.status_code != 429:
            return resp
        retry_after = resp.headers.get("Retry-After")
        wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else delay
        time.sleep(wait)
        delay *= 2
        resp = httpx.get(
            url, headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True,
        )
    return resp


def collect_source(conn, source: dict) -> dict:
    """단일 소스를 수집해 마스터DB에 적재. last_collected_at 이후 발행분만 반영(B2 증분 수집)."""
    # 2026-08-11: feedparser에 URL을 직접 넘기면(agent=USER_AGENT) 상태 코드를 안 보고
    # 응답 바디를 그대로 XML로 파싱한다. arXiv API가 요청 과다 시 XML 대신 평문
    # "Rate exceeded."(HTTP 429)를 돌려주는데, 이게 "syntax error"라는 엉뚱한
    # bozo_exception으로 잘못 보고돼 원인 파악이 안 됐다. httpx로 먼저 받아 상태
    # 코드를 확인한 뒤 본문만 feedparser에 넘겨 진짜 원인을 구분한다.
    try:
        resp = _get_with_rate_limit_backoff(source["feed_url"])
    except httpx.HTTPError as exc:
        return {"source": source["name"], "fetched": 0, "inserted": 0, "error": f"request failed: {exc}"}

    if resp.status_code == 429:
        return {"source": source["name"], "fetched": 0, "inserted": 0, "error": "rate limited (HTTP 429, retries exhausted)"}
    if resp.status_code >= 400:
        return {"source": source["name"], "fetched": 0, "inserted": 0, "error": f"HTTP {resp.status_code}"}

    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        return {"source": source["name"], "fetched": 0, "inserted": 0, "error": str(feed.bozo_exception)}

    # 2회차부터는 증분 수집(B2 대응). 최초 수집은 아카이브 전체가 쏟아지는 것을 막기 위해
    # initial_backfill_days로 소급 범위를 제한한다.
    since = source["last_collected_at"]
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=settings.initial_backfill_days)

    inserted = 0
    fetched = 0

    with conn.cursor() as cur:
        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            fetched += 1

            published_at = _entry_published_at(entry)
            # 발행일 불명(published_at is None)은 판단 불가라 통과시키고 이후 필터에 맡긴다
            if published_at is not None and published_at <= since:
                continue

            title = entry.get("title", "").strip()
            raw_summary = entry.get("summary", "") or entry.get("description", "")
            url_canonical = normalize_url(link)

            signals = {}
            # HN points는 원본 HTML의 "<p>Points: N</p>" 패턴에서 뽑으므로 스트립 전에 파싱한다.
            points = parse_hn_points(raw_summary)
            if points is not None:
                signals["hn_points"] = points

            # hnrss 등 일부 피드는 description에 <p><a href=...> 형태 HTML을 그대로 담아 보낸다.
            # keyword_api.py는 이미 strip_html을 쓰는데 이 수집기만 빠져 있었다 —
            # summary가 그대로 검색 응답에 노출되고, content가 없을 때 임베딩 텍스트로도 쓰인다.
            summary = strip_html(raw_summary)

            cur.execute(
                """
                INSERT INTO articles
                    (source_id, source, source_type, source_trust,
                     title, url, url_canonical, summary, published_at, impact_signals)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (url_canonical) DO NOTHING
                RETURNING id
                """,
                (
                    source["id"], source["name"], source["source_type"], source["source_trust"],
                    title, link, url_canonical, summary, published_at, json.dumps(signals),
                ),
            )
            if cur.fetchone() is not None:
                inserted += 1

        cur.execute(
            "UPDATE sources SET last_collected_at = now() WHERE id = %s",
            (source["id"],),
        )

    return {"source": source["name"], "fetched": fetched, "inserted": inserted, "error": None}


def collect_all() -> list[dict]:
    conn = get_connection()
    results = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, source_type, feed_url, source_trust, last_collected_at
            FROM sources
            WHERE active AND feed_url IS NOT NULL
              AND source_type IN ('rss', 'aggregator', 'api')
            """
        )
        columns = [c.name for c in cur.description]
        sources = [dict(zip(columns, row)) for row in cur.fetchall()]

    for i, source in enumerate(sources):
        results.append(collect_source(conn, source))
        # 마지막 소스 뒤에는 기다릴 이유가 없으므로 건너뛴다.
        if i < len(sources) - 1:
            time.sleep(random.uniform(*SOURCE_DELAY_RANGE_SECONDS))

    conn.close()
    return results


if __name__ == "__main__":
    for result in collect_all():
        print(result)
