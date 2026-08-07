import calendar
import json
import re
from datetime import datetime, timedelta, timezone

import feedparser

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.text import strip_html
from tech_monitoring.utils.url_normalize import normalize_url

# 일부 사이트는 UA 없는 요청을 봇으로 간주해 403을 반환함 (리서치 문서 "크롤링 여부" 권고 반영)
USER_AGENT = "Mozilla/5.0 (compatible; tech-monitoring-mcp/0.1; +internal use)"

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


def collect_source(conn, source: dict) -> dict:
    """단일 소스를 수집해 마스터DB에 적재. last_collected_at 이후 발행분만 반영(B2 증분 수집)."""
    feed = feedparser.parse(source["feed_url"], agent=USER_AGENT)
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

    for source in sources:
        results.append(collect_source(conn, source))

    conn.close()
    return results


if __name__ == "__main__":
    for result in collect_all():
        print(result)
