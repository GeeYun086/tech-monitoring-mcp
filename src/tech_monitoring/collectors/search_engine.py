"""검색엔진(Tavily Search API) 수집기 — v2 파이프라인의 유일한 수집 경로.

배경: 원래 Google Custom Search JSON API로 설계했으나 2026-08-13 실제 연동
중 발견 — Google이 **2025년 중반 이후 생성된 신규 계정에는 이 API 접근을
아예 막아뒀다**("Custom Search JSON API is closed to new customers",
2027-01-01 서비스 종료 예정, 공식 문서·Google Developer 포럼에서 확인).
API 활성화·결제 계정 연결을 다 해도 안 되는 정책 자체의 문제라 설정으로
우회 불가 — Tavily Search API로 교체했다.

Tavily가 오히려 더 잘 맞는 부분:
- `include_domains`/`exclude_domains`를 API 파라미터로 직접 받아서(최대
  300/150개) Google처럼 별도 "검색엔진(cx)"을 웹 UI에서 미리 만들어둘
  필요가 없다.
- `time_range="week"`로 기간 제한 네이티브 지원(dateRestrict=w1과 동일 효과).
- `topic="news"`로 이슈·뉴스 중심 결과에 더 특화.
- 무료 티어 월 1,000크레딧, 신용카드 등록 불필요(Google 결제 계정 연결
  문제를 아예 겪지 않음).

**화이트리스트는 이중으로 강제한다.** Tavily의 include_domains/exclude_domains가
경로 패턴(`linkedin.com/in` 같은)과 와일드카드를 지원한다고는 문서에 있지만
정확한 매칭 알고리즘까지는 공개돼 있지 않다. 그래서:
1. Tavily에는 **도메인 단위**로만 검색 범위를 좁혀 달라고 요청한다(효율 목적 —
   전혀 무관한 도메인까지 뒤지지 않게).
2. 응답으로 받은 URL은 **우리 코드가** SITE_INCLUDE_PATTERNS/SITE_EXCLUDE_PATTERNS
   (담당자가 실제 검증한 패턴, fnmatch)로 최종 판정한다. 신뢰할 수 있는 건
   이쪽이지 Tavily의 경로 매칭 정확도가 아니다.

Tavily는 페이지네이션이 없고 한 호출당 max_results가 최대 20건이다. 화이트
리스트 사이트 전체를 한 번에 묶어 검색하면 결과가 특정 사이트로 쏠릴 위험이
있다(예: 20건 전부 techcrunch.com이고 techmeme.com은 0건). 그래서 고정
키워드 × 사이트 조합마다 개별 호출한다(3 키워드 × 6 사이트 = 18쿼리/주 —
무료 한도(월 1,000크레딧, basic depth 1크레딧/회)에 여유가 크다).

    ./.venv/Scripts/python.exe -m tech_monitoring.collectors.search_engine
"""

import fnmatch
from urllib.parse import urlsplit

import httpx

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import (
    complete_weekly_run,
    fail_weekly_run,
    get_active_fixed_keywords,
    start_weekly_run,
)
from tech_monitoring.utils.url_normalize import normalize_url

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
RESULTS_PER_SITE = 20  # Tavily 1회 호출 최대치(고정 — 페이지네이션 자체가 없음)

# 담당자가 2026-08-13 직접 검증한 화이트리스트. include에 매칭되고
# exclude 어디에도 안 걸려야 최종 통과(is_allowed_url).
SITE_INCLUDE_PATTERNS = [
    "www.itworld.co.kr/article/*",
    "www.aitimes.com/*",
    "news.hada.io/weekly/*",
    "*.techcrunch.com/*",
    "askedtech.com/knowledge-archive/*",
    "www.techmeme.com/*",
]

SITE_EXCLUDE_PATTERNS = [
    "www.itworld.co.kr/",
    "www.aitimes.com/",
    "www.itworld.co.kr/reviews/*",
    "www.itworld.co.kr/how-to/*",
    "www.itworld.co.kr/newsletters/*",
    "www.aitimes.com/news/articleList.html*",
    "www.techmeme.com/river*",
    "www.techmeme.com/lb*",
    "www.techmeme.com/about*",
    "www.techmeme.com/events*",
    "www.techmeme.com/miniriver*",
    "*.techcrunch.com/podcast/*",
    "*.techcrunch.com/author/*",
    "*.techcrunch.com/category/*",
    "*.techcrunch.com/tag/*",
]

# 위 include 패턴에서 순수 도메인만 뽑아 Tavily include_domains(검색 범위
# 축소용)로 쓴다. 고정 키워드 × 이 목록 하나하나에 대해 개별 호출한다.
SITE_DOMAINS = [
    "itworld.co.kr",
    "aitimes.com",
    "news.hada.io",
    "techcrunch.com",
    "askedtech.com",
    "techmeme.com",
]


def _url_path_for_matching(url: str) -> str:
    """fnmatch 패턴이 "www.itworld.co.kr/article/*"처럼 스킴 없는 host+path
    형태라, URL에서 스킴만 떼고 나머지(호스트+경로+쿼리)를 그대로 비교한다."""
    return url.split("://", 1)[-1]


def _expand_pattern(pattern: str) -> list[str]:
    """"*.techcrunch.com/*" 같은 패턴은 Google Programmable Search Engine의
    사이트 제한 문법에서 "서브도메인 전부 + 도메인 자체"를 뜻한다(실제로
    담당자의 기존 검색엔진에서 techcrunch.com 루트 기사도 정상 수집됐던
    것으로 확인). 반면 fnmatch의 순수 셸 글롭 의미로는 "*."가 리터럴 "."을
    포함해서 서브도메인이 없는 "techcrunch.com" 자체는 매칭에서 빠진다
    (테스트로 실제 발견한 버그). 그래서 "*." 로 시작하는 패턴은 그 접두어를
    뗀 버전("techcrunch.com/*")도 함께 후보로 넣어 두 형태 다 매칭되게 한다."""
    if pattern.startswith("*."):
        return [pattern, pattern[2:]]
    return [pattern]


def _matches_any(url: str, patterns: list[str]) -> bool:
    target = _url_path_for_matching(url)
    expanded = [p for pattern in patterns for p in _expand_pattern(pattern)]
    return any(fnmatch.fnmatch(target, p) for p in expanded)


def is_allowed_url(url: str) -> bool:
    """담당자가 검증한 화이트리스트 최종 판정 — include 중 하나라도 맞고
    exclude 어느 것에도 안 걸려야 통과. Tavily가 좁혀준 결과라도 이 검사를
    반드시 통과해야 저장된다(모듈 docstring의 "이중 강제" 참고)."""
    return _matches_any(url, SITE_INCLUDE_PATTERNS) and not _matches_any(url, SITE_EXCLUDE_PATTERNS)


def _extract_domain(url: str) -> str:
    return urlsplit(url).netloc


def _tavily_request(payload: dict) -> dict:
    resp = httpx.post(
        TAVILY_SEARCH_URL,
        json=payload,
        headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def search_once(query: str, num: int = 10) -> list[dict]:
    """대시보드 검색창(사용자 직접 검색) 전용 — search_results 테이블에 저장하지
    않고 요청마다 라이브로 호출한다. 화이트리스트 도메인 전체(SITE_DOMAINS)로
    검색하되, 기간 제한은 없다(사용자가 명시적으로 검색한 것이므로 "이번 주"로
    좁힐 이유가 없음). 자격증명 미설정·네트워크 오류는 빈 목록으로 조용히
    처리한다(대시보드가 죽으면 안 됨 — 화면에 "결과 없음"으로만 보인다)."""
    if not settings.tavily_api_key:
        return []
    payload = {
        "query": query,
        "max_results": min(max(num, 1), RESULTS_PER_SITE),
        "include_domains": SITE_DOMAINS,
    }
    try:
        data = _tavily_request(payload)
    except httpx.HTTPError:
        return []
    return [r for r in data.get("results", []) if r.get("url") and is_allowed_url(r["url"])]


def _fetch_site_results(keyword: str, domain: str) -> list[dict]:
    payload = {
        "query": keyword,
        "search_depth": "basic",  # 1크레딧/회(advanced는 2) — 무료 한도 절약
        "topic": "news",
        "max_results": RESULTS_PER_SITE,
        "time_range": settings.tavily_time_range,
        "include_domains": [domain],
    }
    return _tavily_request(payload).get("results", [])


def _insert_result(conn, *, run_id: int, fixed_keyword_id: int, query: str, rank: int, item: dict) -> bool:
    url = item.get("url")
    if not url:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO search_results
                (run_id, fixed_keyword_id, query, rank, title, url, source_domain, snippet, published_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, fixed_keyword_id, url) DO NOTHING
            RETURNING id
            """,
            (
                run_id, fixed_keyword_id, query, rank,
                item.get("title") or "(제목 없음)",
                normalize_url(url),
                _extract_domain(url),
                item.get("content"),
                None,  # Tavily 응답엔 발행일 필드가 없음(공식 문서 확인) — 정렬 참고값 없어도 지장 없음
            ),
        )
        return cur.fetchone() is not None


def collect_for_keyword(conn, run_id: int, fixed_keyword: dict) -> dict:
    """고정 키워드 하나에 대해 화이트리스트 사이트마다 개별 호출 후 저장."""
    keyword = fixed_keyword["keyword"]
    fetched = inserted = 0

    for domain in SITE_DOMAINS:
        try:
            items = _fetch_site_results(keyword, domain)
        except httpx.HTTPError as exc:
            return {"fixed_keyword": keyword, "fetched": fetched, "inserted": inserted, "error": str(exc)}

        for rank, item in enumerate(items, start=1):
            fetched += 1
            url = item.get("url")
            if not url or not is_allowed_url(url):
                continue  # Tavily가 도메인은 맞혀도 경로까지는 우리가 다시 검증
            if _insert_result(
                conn, run_id=run_id, fixed_keyword_id=fixed_keyword["id"],
                query=keyword, rank=rank, item=item,
            ):
                inserted += 1

    return {"fixed_keyword": keyword, "fetched": fetched, "inserted": inserted, "error": None}


def collect_all(run_id: int) -> list[dict]:
    if not settings.tavily_api_key:
        return [{"fixed_keyword": None, "fetched": 0, "inserted": 0, "error": "TAVILY_API_KEY 미설정 — .env 확인"}]

    conn = get_connection()
    try:
        keywords = get_active_fixed_keywords(conn)
        if not keywords:
            return [{"fixed_keyword": None, "fetched": 0, "inserted": 0, "error": "fixed_keywords에 활성 키워드 없음"}]
        return [collect_for_keyword(conn, run_id, kw) for kw in keywords]
    finally:
        conn.close()


if __name__ == "__main__":
    _conn = get_connection()
    _run_id = start_weekly_run(_conn)
    _conn.close()

    _results = collect_all(_run_id)
    for _result in _results:
        print(_result)

    _conn = get_connection()
    _errors = [r for r in _results if r["error"]]
    if _errors:
        fail_weekly_run(_conn, _run_id, "; ".join(f"{r['fixed_keyword']}: {r['error']}" for r in _errors))
    else:
        complete_weekly_run(_conn, _run_id)
    _conn.close()
