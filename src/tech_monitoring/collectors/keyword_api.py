import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.url_normalize import normalize_url

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or ""))


def _get_active_keywords(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT keywords FROM topics WHERE active")
        keywords: set[str] = set()
        for (row_keywords,) in cur.fetchall():
            keywords.update(row_keywords or [])
    return sorted(keywords)


def _get_source(conn, name: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, source_type, source_trust, last_collected_at FROM sources WHERE name = %s",
            (name,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [c.name for c in cur.description]
        return dict(zip(columns, row))


def _insert_article(conn, source: dict, *, title, url, url_canonical, summary, published_at) -> bool:
    with conn.cursor() as cur:
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
                title, url, url_canonical, summary, published_at,
            ),
        )
        return cur.fetchone() is not None


def collect_hn_algolia(conn, keywords: list[str]) -> dict:
    source = _get_source(conn, "Hacker News (Algolia)")
    if source is None:
        return {"source": "Hacker News (Algolia)", "fetched": 0, "inserted": 0, "error": "source not configured"}

    since = source["last_collected_at"]
    numeric_filters = None
    if since is not None:
        numeric_filters = f"created_at_i>{int(since.timestamp())}"

    fetched = inserted = 0
    for keyword in keywords:
        params = {"query": keyword, "tags": "story"}
        if numeric_filters:
            params["numericFilters"] = numeric_filters
        try:
            resp = httpx.get(HN_ALGOLIA_URL, params=params, timeout=15)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return {"source": "Hacker News (Algolia)", "fetched": fetched, "inserted": inserted, "error": str(exc)}

        for hit in resp.json().get("hits", []):
            fetched += 1
            object_id = hit["objectID"]
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
            published_at = datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc)
            if _insert_article(
                conn, source,
                title=hit.get("title") or "(제목 없음)",
                url=url,
                url_canonical=normalize_url(url),
                summary=hit.get("story_text"),
                published_at=published_at,
            ):
                inserted += 1

    with conn.cursor() as cur:
        cur.execute("UPDATE sources SET last_collected_at = now() WHERE id = %s", (source["id"],))

    return {"source": "Hacker News (Algolia)", "fetched": fetched, "inserted": inserted, "error": None}


def collect_naver_news(conn, keywords: list[str]) -> dict:
    if not settings.naver_client_id or not settings.naver_client_secret:
        return {"source": "Naver News", "fetched": 0, "inserted": 0, "error": "NAVER_CLIENT_ID/SECRET 미설정, skip"}

    source = _get_source(conn, "Naver News")
    if source is None:
        return {"source": "Naver News", "fetched": 0, "inserted": 0, "error": "source not configured"}

    headers = {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }
    since = source["last_collected_at"]

    fetched = inserted = 0
    for keyword in keywords:
        try:
            resp = httpx.get(
                NAVER_NEWS_URL,
                params={"query": keyword, "display": 100, "sort": "date"},
                headers=headers, timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return {"source": "Naver News", "fetched": fetched, "inserted": inserted, "error": str(exc)}

        for item in resp.json().get("items", []):
            fetched += 1
            published_at = parsedate_to_datetime(item["pubDate"])
            if since is not None and published_at <= since:
                continue
            url = item.get("originallink") or item["link"]
            if _insert_article(
                conn, source,
                title=_strip_tags(item["title"]),
                url=url,
                url_canonical=normalize_url(url),
                summary=_strip_tags(item.get("description", "")),
                published_at=published_at,
            ):
                inserted += 1

    with conn.cursor() as cur:
        cur.execute("UPDATE sources SET last_collected_at = now() WHERE id = %s", (source["id"],))

    return {"source": "Naver News", "fetched": fetched, "inserted": inserted, "error": None}


def collect_all() -> list[dict]:
    conn = get_connection()
    keywords = _get_active_keywords(conn)
    results = [collect_hn_algolia(conn, keywords), collect_naver_news(conn, keywords)]
    conn.close()
    return results


if __name__ == "__main__":
    for result in collect_all():
        print(result)
