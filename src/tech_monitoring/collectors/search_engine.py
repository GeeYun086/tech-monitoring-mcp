"""검색엔진(Google Custom Search JSON API) 수집기 — v2 파이프라인의 유일한 수집 경로.

배경: v1(RSS 애그리게이터 + 임베딩 기반 관련도 필터링, db/migrations_v1_archive/)을
폐기하고, 사용자가 직접 구성한 커스텀 검색엔진(주요 사이트만 화이트리스트로 등록)
으로 노이즈를 원천 차단하는 방식으로 전환했다(2026-08-13 피벗). 그래서 이 모듈엔
관련도 판별 로직이 없다 — 큐레이션된 사이트 목록 자체가 관련도를 보장한다는 게
피벗의 핵심 전제다.

고정 키워드(fixed_keywords)마다 dateRestrict=w1(Custom Search API 공식 파라미터 —
비공식 스크래핑 아님, "지난 1주일")로 검색해 top20 같은 인위적 컷 없이 넓게(기본
50건, search_results_per_keyword로 조정) 가져온다. "주요 이슈"를 여기서 추리지
않는 이유: 그건 다음 단계(analysis/keyword_extraction.py — TF-IDF로 후보를 정확히
세고 Gemini가 동의어만 병합)의 몫이라, 이 단계는 최대한 넓게 모으기만 한다.

무료 한도 참고: Custom Search JSON API는 100쿼리/일. 고정 키워드 3개 ×
목표 50건(=5페이지) = 15쿼리/주 수준이라 여유가 크다.

    ./.venv/Scripts/python.exe -m tech_monitoring.collectors.search_engine
"""

import time

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

SEARCH_URL = "https://www.googleapis.com/customsearch/v1"
RESULTS_PER_PAGE = 10  # Custom Search API 제약(num 최대값), 고정
MAX_START_INDEX = 91  # API 제약: start는 1~91까지만 허용(최대 100건 = 10페이지)
# 공식 API라 pytrends급 백오프는 불필요하지만, 짧은 시간에 페이지를 몰아서
# 때리지 않기 위한 최소한의 예의상 간격.
_PAGE_INTERVAL_SECONDS = 0.2


def search_once(query: str, num: int = 10) -> list[dict]:
    """대시보드 검색창(사용자 직접 검색) 전용 — search_results 테이블에 저장하지
    않고 요청마다 라이브로 호출한다(무료 한도 절약). 주간 배치(_fetch_page)와
    달리 dateRestrict를 안 건다 — 사용자가 명시적으로 검색한 것이므로 "이번
    주"로 좁힐 이유가 없고, 큐레이션 검색엔진 전체 인덱스에서 찾는 게 맞다.
    자격증명 미설정·네트워크 오류는 빈 목록으로 조용히 처리한다(대시보드가
    죽으면 안 됨 — 화면에 "결과 없음"으로만 보인다)."""
    if not settings.google_search_api_key or not settings.google_search_cx:
        return []
    params = {
        "key": settings.google_search_api_key,
        "cx": settings.google_search_cx,
        "q": query,
        "num": min(max(num, 1), RESULTS_PER_PAGE),
    }
    try:
        resp = httpx.get(SEARCH_URL, params=params, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError:
        return []
    return resp.json().get("items", [])


def _fetch_page(keyword: str, start: int) -> dict:
    params = {
        "key": settings.google_search_api_key,
        "cx": settings.google_search_cx,
        "q": keyword,
        "dateRestrict": settings.google_search_date_restrict,
        "num": RESULTS_PER_PAGE,
        "start": start,
    }
    resp = httpx.get(SEARCH_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _extract_published_at(item: dict) -> str | None:
    """검색결과 pagemap 메타데이터에서 발행 시각을 최대한 뽑아본다. 화이트리스트
    사이트 상당수가 article:published_time 등 메타태그를 제공하지만 없는 경우도
    흔하다 — 정렬 참고용일 뿐 필수 정보가 아니므로 실패해도 조용히 None을 반환하고
    파이프라인을 막지 않는다."""
    pagemap = item.get("pagemap") or {}
    for meta in pagemap.get("metatags", []):
        for key in ("article:published_time", "og:updated_time", "date"):
            if meta.get(key):
                return meta[key]
    return None


def _insert_result(conn, *, run_id: int, fixed_keyword_id: int, query: str, rank: int, item: dict) -> bool:
    url = item.get("link")
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
                item.get("displayLink"),
                item.get("snippet"),
                _extract_published_at(item),
            ),
        )
        return cur.fetchone() is not None


def collect_for_keyword(conn, run_id: int, fixed_keyword: dict, target_count: int | None = None) -> dict:
    """고정 키워드 하나에 대해 dateRestrict=w1 검색결과를 페이지네이션하며 저장."""
    target_count = target_count or settings.search_results_per_keyword
    keyword = fixed_keyword["keyword"]
    fetched = inserted = 0
    start = 1

    while fetched < target_count and start <= MAX_START_INDEX:
        try:
            data = _fetch_page(keyword, start)
        except httpx.HTTPError as exc:
            return {"fixed_keyword": keyword, "fetched": fetched, "inserted": inserted, "error": str(exc)}

        items = data.get("items", [])
        if not items:
            break  # dateRestrict=w1 범위 안에서 결과 소진 — 에러 아니라 정상 종료

        for offset, item in enumerate(items):
            fetched += 1
            if _insert_result(
                conn, run_id=run_id, fixed_keyword_id=fixed_keyword["id"],
                query=keyword, rank=start + offset, item=item,
            ):
                inserted += 1

        start += RESULTS_PER_PAGE
        if start <= MAX_START_INDEX:
            time.sleep(_PAGE_INTERVAL_SECONDS)

    return {"fixed_keyword": keyword, "fetched": fetched, "inserted": inserted, "error": None}


def collect_all(run_id: int) -> list[dict]:
    if not settings.google_search_api_key or not settings.google_search_cx:
        return [{
            "fixed_keyword": None, "fetched": 0, "inserted": 0,
            "error": "GOOGLE_SEARCH_API_KEY/GOOGLE_SEARCH_CX 미설정 — .env 확인",
        }]

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
