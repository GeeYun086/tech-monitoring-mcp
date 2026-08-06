import calendar
from datetime import datetime, timezone

import feedparser

from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.url_normalize import normalize_url

# 일부 사이트는 UA 없는 요청을 봇으로 간주해 403을 반환함 (리서치 문서 "크롤링 여부" 권고 반영)
USER_AGENT = "Mozilla/5.0 (compatible; tech-monitoring-mcp/0.1; +internal use)"


def _entry_published_at(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


def collect_source(conn, source: dict) -> dict:
    """단일 소스를 수집해 마스터DB에 적재. last_collected_at 이후 발행분만 반영(B2 증분 수집)."""
    feed = feedparser.parse(source["feed_url"], agent=USER_AGENT)
    if feed.bozo and not feed.entries:
        return {"source": source["name"], "fetched": 0, "inserted": 0, "error": str(feed.bozo_exception)}

    since = source["last_collected_at"]
    inserted = 0
    fetched = 0

    with conn.cursor() as cur:
        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            fetched += 1

            published_at = _entry_published_at(entry)
            if since is not None and published_at is not None and published_at <= since:
                continue

            title = entry.get("title", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            url_canonical = normalize_url(link)

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
                    source["id"], source["name"], source["source_type"], source["source_trust"],
                    title, link, url_canonical, summary, published_at,
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
