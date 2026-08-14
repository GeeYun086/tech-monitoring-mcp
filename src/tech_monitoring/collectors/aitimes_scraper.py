"""v3: AI타임스 AI산업 섹션 전용 스크래퍼.

2026-08-13 실사용 조사 결과(README "v2 vs v3 비교 실험" 참고) 두 가지를
확인했다:
1. v1이 쓰던 RSS 주소(aitimes.com/rss/allArticle.xml)는 죽었고, 실제 주소는
   cdn.aitimes.com/rss/gn_rss_allArticle.xml로 이전됨.
2. 그 RSS조차 섹션 필터가 안 된다(쿼리 파라미터를 붙여도 무시하고 "전체기사"
   그대로 반환) — "여수 해양레저관광도시 계획" 같은 AX 무관 지역뉴스까지
   섞여서 담당자가 원한 "AI산업/AI기업 탭만" 요구사항을 RSS로는 못 채운다.

그래서 GeekNews Weekly와 같은 방식(목록 페이지 직접 파싱)을 쓴다. AI산업
섹션(sc_section_code=S1N3) 목록엔 그 하위분류(AI 기업·산업일반·메타버스 등)
태그가 붙은 항목이 이미 섞여 나온다는 걸 실측으로 확인했다(첫 페이지에
"AI 기업" 태그 항목 포함, 2026-08-13) — 그래서 sc_sub_section_code=S2N51
(AI기업)을 따로 또 긁을 필요가 없다.

파싱 대상 HTML 구조(실측, view_type=sm — 제목형. 사이트 개편 시 깨질 수 있음):
    <li class="altlist-text-item">
      <div class="altlist-text-group">
        <H2 class="altlist-subject"><a href="{url}" target="_top">{title}</a></H2>
      </div>
      <div class="altlist-info">
        <div class="altlist-info-item">{하위 카테고리}</div>
        <div class="altlist-info-item">{기자명}</div>
        <div class="altlist-info-item">{MM-DD HH:MM}</div>
      </div>
    </li>

한계(코드로 우회하지 않고 명시): 이 목록 페이지는 최신 ~22건만 보여준다.
"더보기" 버튼은 JS(articlePaging.evtMoreList)로 추가 페이지를 가져오는데
그 엔드포인트가 공개 문서화돼 있지 않다 — &page=2를 직접 붙여도 1페이지와
동일한 결과가 와서(실측 확인) 진짜 페이지네이션이 아니다. 리버스엔지니어링
해서 우회하는 대신(Google Custom Search 우회 스크래핑을 배제한 것과 같은
이유 — collectors/search_engine.py 모듈 docstring 참고) 첫 페이지만
가져온다. AI산업 섹션이 주당 22건보다 많이 발행하면 오래된 기사를 놓친다
— 완전한 커버리지가 필요하면 이 수집기를 매주보다 자주(예: 매일) 돌릴 것.

    ./.venv/Scripts/python.exe -m tech_monitoring.collectors.aitimes_scraper
"""

import html
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.url_normalize import normalize_url

SOURCE_NAME = "AI타임스"
# AI산업(하위: AI 기업·산업일반·메타버스 등 포함, 2026-08-13 실측 확인).
LIST_URL = "https://www.aitimes.com/news/articleList.html?sc_section_code=S1N3&view_type=sm"
USER_AGENT = "Mozilla/5.0 (compatible; tech-monitoring-mcp/0.1; +internal use)"
KST = timezone(timedelta(hours=9))

_ITEM_RE = re.compile(
    r'<li class="altlist-text-item">.*?'
    r'<a href="([^"]+)" target="_top">\s*(.*?)\s*</a>.*?'
    r'<div class="altlist-info-item">([^<]*)</div>\s*'
    r'<div class="altlist-info-item">([^<]*)</div>\s*'
    r'<div class="altlist-info-item">([^<]*)</div>.*?'
    r'</li>',
    re.DOTALL,
)


def _parse_kr_datetime(text: str, reference_date: date) -> datetime | None:
    """목록 페이지의 "MM-DD HH:MM" 형식(연도 없음)을 파싱한다. 연도는
    명시되지 않으므로 reference_date(보통 오늘) 기준으로 유추 —
    reference_date보다 30일 넘게 미래로 계산되면 작년 것으로 본다(연말/
    연초 경계에서 12월 기사를 1월에 수집하는 경우 대응)."""
    match = re.match(r"(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", (text or "").strip())
    if not match:
        return None
    month, day, hour, minute = (int(g) for g in match.groups())
    year = reference_date.year
    try:
        candidate = datetime(year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None
    if (candidate.date() - reference_date).days > 30:
        candidate = candidate.replace(year=year - 1)
    return candidate


def parse_article_list(list_html: str, reference_date: date | None = None) -> list[dict]:
    """목록 페이지 HTML에서 (title, url, category, published_at) 목록을 뽑는다.
    category는 요약 텍스트가 없는 이 목록 형식에서 유일하게 얻을 수 있는
    짧은 문맥이라 snippet 대용으로 쓴다(예: "AI 기업")."""
    reference_date = reference_date or date.today()
    items = []
    for url, title, category, _reporter, dt_text in _ITEM_RE.findall(list_html):
        items.append({
            "url": url,
            "title": html.unescape(title).strip(),
            "snippet": category.strip() or None,
            "published_at": _parse_kr_datetime(dt_text, reference_date),
        })
    return items


def fetch_article_list() -> list[dict]:
    resp = httpx.get(LIST_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return parse_article_list(resp.text)


def _extract_domain(url: str) -> str:
    return urlsplit(url).netloc


def _insert_article(conn, *, run_id: int, item: dict) -> bool:
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
                _extract_domain(url), item.get("snippet"), item.get("published_at"),
            ),
        )
        return cur.fetchone() is not None


def collect_aitimes(conn, run_id: int) -> dict:
    try:
        items = fetch_article_list()
    except httpx.HTTPError as exc:
        return {"source": SOURCE_NAME, "fetched": 0, "inserted": 0, "error": str(exc)}

    inserted = sum(_insert_article(conn, run_id=run_id, item=item) for item in items)
    return {"source": SOURCE_NAME, "fetched": len(items), "inserted": inserted, "error": None}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from tech_monitoring.db.weekly_run import start_weekly_run

    _conn = get_connection()
    try:
        _run_id = start_weekly_run(_conn)
        print(collect_aitimes(_conn, _run_id))
    finally:
        _conn.close()
