"""검색엔진(Tavily Search API) 수집기 — 파이프라인의 유일한 수집 경로.

**2026-08-19부터 수집 단위가 "시장별 검색"에서 "공용 기사 풀"로 바뀌었다**
(db/migrations/006_tavily_shared_pool.sql 헤더에 실측 근거). 넓은 질의
(BROAD_QUERIES_KO/EN, 코드 상수)로 화이트리스트 사이트의 그 주 기사를 모아
collected_articles에 시장과 무관하게 저장하고, 시장별 선별은 사람 라벨로
학습한 분류기가 맡는다. 진입점은 collect_all -> collect_pool_for_site다.
시장별 검색어 경로(collect_for_keyword -> search_results)는 정밀도 보강용
선택지로 남겨두되 파이프라인에서는 호출하지 않는다.

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
- `start_date`/`end_date`로 정확한 날짜 범위 지정 네이티브 지원.
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
키워드 × 사이트 조합마다 개별 호출한다.

**고정 키워드 하나당 검색어가 여러 개(언어별)일 수 있다**(2026-08-13,
db/migrations/003_multi_term_keywords.sql). fixed_keywords.keyword 문자열
하나를 그대로 검색어로 썼더니 (1) 한국어 사이트도 표현이 다르면 못 찾고,
(2) 영어 사이트(TechCrunch 등)엔 한국어라 아예 안 맞아서 Tavily가 그
도메인의 무관한 인기글로 채워 넣는 문제가 실측 확인됐다("교육"으로
검색해도 TechCrunch 결과 19건이 전부 무관했음). 짧게 쪼개는 게 해법은
아니었다 — "도입"·"실적"만 남기면 오히려 더 흔한 단어라 무관한 것까지
걸린다(예: "Signal 자동 키 검증 도입"). 그래서 같은 개념의 여러 구체적인
동의어를 언어별로(search_terms_ko/en) 등록해 병렬로 검색한다 —
한국어 사이트엔 search_terms_ko 전부, 영어 사이트엔 search_terms_en
전부를 각각 개별 호출한다(둘 다 비어있으면 keyword 자체로 폴백).

호출 수: 키워드 하나당 (한국어 사이트 3개 × 한국어 검색어 N개) +
(영어 사이트 3개 × 영어 검색어 M개). N=M=5 기준 30쿼리/키워드 ×
3키워드 = 90쿼리/주 — 무료 한도(월 1,000크레딧, basic depth 1크레딧/회
≈ 387/월)에 여유가 있다.

**기간 파라미터는 time_range가 아니라 start_date/end_date를 쓴다**
(2026-08-13 수정). 원래 time_range="week"(호출 시점 기준 지난 7일 롤링
윈도우)를 썼는데, 대시보드가 보여주는 weekly_runs.period_start/end(달력
월~일)와 실제 검색 범위가 어긋나는 문제가 있었다(실행일이 수요일이면
달력 주 전체가 아니라 그 전주 목요일부터 실행일까지만 검색됨). start_date/
end_date로 이 run의 period_start/end(db/weekly_run.get_run_period)를
그대로 넘기면 실제 검색 자체가 그 달력 주로 정확히 맞춰져 배너와 항상
일치한다.

**Techmeme는 Tavily 색인 커버리지 자체가 얕다**(2026-08-13 실측 —
time_range를 한 달로 넓혀도 0건). 저희 화이트리스트 필터 문제가 아니라
Tavily가 이 사이트를 잘 못 가져오는 것으로 보인다 — 알려진 한계로 남겨둔다.

    ./.venv/Scripts/python.exe -m tech_monitoring.collectors.search_engine
"""

import fnmatch
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import (
    complete_weekly_run,
    fail_weekly_run,
    get_active_fixed_keywords,
    get_run_period,
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
    "news.hada.io/topic*",  # 2026-08-13: 위클리 다이제스트만으론 사실상 0건이라 개별 글까지 포함
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

# 사이트별 언어 — 어느 search_terms_* 목록을 쓸지 결정한다(2026-08-13 추가).
KOREAN_DOMAINS = {"itworld.co.kr", "aitimes.com", "news.hada.io"}
ENGLISH_DOMAINS = {"techcrunch.com", "askedtech.com", "techmeme.com"}

# collected_articles.source_name에 남길 표시 이름(v3 수집기의 관례와 같은 모양).
SITE_NAMES = {
    "itworld.co.kr": "ITWorld",
    "aitimes.com": "AI타임스",
    "news.hada.io": "GeekNews",
    "techcrunch.com": "TechCrunch",
    "askedtech.com": "AskedTech",
    "techmeme.com": "Techmeme",
}

# 공용 기사 풀을 만드는 넓은 질의(006 마이그레이션 헤더 참고). **사용자가
# 입력하는 값이 아니라 코드 상수다** — 시장 이름만 받고 쓰는 도구를 만들려면
# "동의어를 누가 지어내는가"라는 문제를 없애야 하고, 그 답이 이 방식이다.
#
# 이 두 개로 실측(2026-08-18, 이틀치)한 결과가 고유 58건·5개 사이트 고르게
# 분포였다. 시장 이름을 검색어로 쓴 직전 실행이 시장당 3~5건이었으니 물량이
# 10배 이상 차이 난다. Tavily는 결과가 부족하면 그 도메인 최신글로 채우므로
# 넓은 질의가 사실상 "사이트 최신글 훑기"로 동작한다.
#
# 늘리면 사이트당 20건(RESULTS_PER_SITE) 상한이 질의 수만큼 늘어나고 크레딧도
# 비례해 늘어난다(질의 1개당 사이트 6개 = 6크레딧/주).
BROAD_QUERIES_KO = ["AI", "AI 기업"]
BROAD_QUERIES_EN = ["AI", "AI startup"]


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


def _parse_published_date(value: str | None):
    """Tavily가 주는 published_date는 RFC 2822 형식 문자열
    (예: "Tue, 11 Aug 2026 16:25:20 GMT") — 2026-08-13 실제 응답으로 확인.
    공식 문서엔 이 필드 언급이 없어 형식이 사이트마다 다르거나 아예 빠질 수
    있으니, 파싱 실패 시 예외를 던지지 않고 None으로 폴백한다(정렬 참고값일
    뿐 필수 데이터가 아님)."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


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


def _fetch_site_results(keyword: str, domain: str, start_date: date, end_date: date) -> list[dict]:
    payload = {
        "query": keyword,
        "search_depth": "basic",  # 1크레딧/회(advanced는 2) — 무료 한도 절약
        "topic": "news",
        "max_results": RESULTS_PER_SITE,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
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
                _parse_published_date(item.get("published_date")),
            ),
        )
        return cur.fetchone() is not None


def broad_queries_for_domain(domain: str) -> list[str]:
    """이 사이트에 던질 넓은 질의 목록 — 사이트 언어에 맞춰 고른다.
    영어 사이트에 한국어 질의를 던지면 Tavily가 못 맞히고 그 도메인의 무관한
    인기글로 채운다(003·006 헤더의 실측 기록)."""
    return BROAD_QUERIES_KO if domain in KOREAN_DOMAINS else BROAD_QUERIES_EN


def _insert_pool_article(conn, *, run_id: int, domain: str, query: str, item: dict) -> bool:
    """공용 기사 풀에 저장. 시장(fixed_keyword_id)을 넣지 않는 게 핵심 —
    "이 기사가 어느 시장에 관련 있나"는 article_keyword_relevance가 따로
    받는다(006 헤더 참고). 컬럼 구성은 v3 수집기(rss_collector._insert_article)
    와 같게 맞춰서, 판단·라벨링·화면이 수집 방식을 구분하지 않게 한다."""
    url = item.get("url")
    if not url:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO collected_articles
                (run_id, source_name, fetch_method, title, url, source_domain, snippet, published_at)
            VALUES (%s, %s, 'search', %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, url) DO NOTHING
            RETURNING id
            """,
            (
                run_id,
                SITE_NAMES.get(domain, domain),
                item.get("title") or "(제목 없음)",
                normalize_url(url),
                _extract_domain(url),
                item.get("content"),
                _parse_published_date(item.get("published_date")),
            ),
        )
        return cur.fetchone() is not None


def collect_pool_for_site(
    conn, run_id: int, domain: str, start_date: date, end_date: date,
) -> dict:
    """사이트 하나에 넓은 질의를 각각 던져 공용 풀에 쌓는다.

    한 사이트가 죽어도 다른 사이트는 계속 진행한다 — 실패는 error에 담아
    정상 리턴하고, 파이프라인이 pipeline_report.stage_errors로 실패를 드러낸다
    (예외를 던지면 그 주 수집이 통째로 날아간다)."""
    name = SITE_NAMES.get(domain, domain)
    fetched = inserted = 0

    for query in broad_queries_for_domain(domain):
        try:
            items = _fetch_site_results(query, domain, start_date, end_date)
        except httpx.HTTPError as exc:
            return {"source": name, "fetched": fetched, "inserted": inserted, "error": str(exc)}

        for item in items:
            fetched += 1
            url = item.get("url")
            if not url or not is_allowed_url(url):
                continue  # Tavily가 도메인은 맞혀도 경로까지는 우리가 다시 검증
            if _insert_pool_article(conn, run_id=run_id, domain=domain, query=query, item=item):
                inserted += 1

    return {"source": name, "fetched": fetched, "inserted": inserted, "error": None}


def _terms_for_domain(fixed_keyword: dict, domain: str) -> list[str]:
    """도메인의 언어에 맞는 검색어 목록. 둘 다 비어있으면(아직 등록 안 함)
    keyword 자체로 폴백해 기존 동작을 유지한다."""
    if domain in KOREAN_DOMAINS:
        terms = fixed_keyword.get("search_terms_ko") or []
    else:
        terms = fixed_keyword.get("search_terms_en") or []
    return terms or [fixed_keyword["keyword"]]


def collect_for_keyword(
    conn, run_id: int, fixed_keyword: dict, start_date: date, end_date: date,
) -> dict:
    """고정 키워드 하나에 대해 (화이트리스트 사이트 × 그 언어의 검색어) 조합마다
    개별 호출 후 search_results에 저장. start_date/end_date는 이 run의 달력 주
    (db/weekly_run.get_run_period)다.

    **파이프라인은 이 경로를 쓰지 않는다**(2026-08-19부터 collect_pool). 시장별
    검색어로 정밀도를 보강하고 싶을 때를 위한 선택적 경로로 남겨둔다 — 실측상
    이 방식만으로는 시장당 3~5건이라 라벨링·학습 물량이 안 나온다(006 헤더).
    """
    keyword = fixed_keyword["keyword"]
    fetched = inserted = 0

    for domain in SITE_DOMAINS:
        for term in _terms_for_domain(fixed_keyword, domain):
            try:
                items = _fetch_site_results(term, domain, start_date, end_date)
            except httpx.HTTPError as exc:
                return {"fixed_keyword": keyword, "fetched": fetched, "inserted": inserted, "error": str(exc)}

            for rank, item in enumerate(items, start=1):
                fetched += 1
                url = item.get("url")
                if not url or not is_allowed_url(url):
                    continue  # Tavily가 도메인은 맞혀도 경로까지는 우리가 다시 검증
                if _insert_result(
                    conn, run_id=run_id, fixed_keyword_id=fixed_keyword["id"],
                    query=term, rank=rank, item=item,
                ):
                    inserted += 1

    return {"fixed_keyword": keyword, "fetched": fetched, "inserted": inserted, "error": None}


def collect_all(run_id: int) -> list[dict]:
    """이번 주 공용 기사 풀을 만든다 — 화이트리스트 사이트마다 넓은 질의로
    수집(006 헤더 참고).

    고정 키워드를 보지 않는다: 수집은 시장과 무관하고, 시장별 선별은 나중에
    분류기가 한다. 그래서 시장을 추가해도 재수집이 필요 없고 크레딧이 시장
    수와 무관하다. 다만 활성 키워드가 하나도 없으면 수집해도 볼 화면이 없어
    그대로 알린다(라벨링·판단이 전부 시장 단위라서)."""
    if not settings.tavily_api_key:
        return [{"source": None, "fetched": 0, "inserted": 0, "error": "TAVILY_API_KEY 미설정 — .env 확인"}]

    conn = get_connection()
    try:
        if not get_active_fixed_keywords(conn):
            return [{"source": None, "fetched": 0, "inserted": 0, "error": "fixed_keywords에 활성 키워드 없음"}]
        start_date, end_date = get_run_period(conn, run_id)
        return [
            collect_pool_for_site(conn, run_id, domain, start_date, end_date)
            for domain in SITE_DOMAINS
        ]
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
        fail_weekly_run(_conn, _run_id, "; ".join(f"{r['source']}: {r['error']}" for r in _errors))
    else:
        complete_weekly_run(_conn, _run_id)
    _conn.close()
