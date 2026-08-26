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

**Techmeme**: 2026-08-13에는 색인 커버리지가 얕아 0건이었지만(time_range를
한 달로 넓혀도 0건), 넓은 질의로 바꾼 뒤 12건이 잡혔다(2026-08-19 실측) —
좁은 질의로 못 찾던 것이 원인이었던 것으로 보인다. 대신 이 사이트는 리버
페이지 앵커(techmeme.com/260818/p3)가 잡혀서 페이지 제목이 "Techmeme"뿐인
경우가 많다(42건 중 11건). 실제 헤드라인은 스니펫에 있어 derive_title이
대신 채운다.

    ./.venv/Scripts/python.exe -m tech_monitoring.collectors.search_engine
"""

import fnmatch
import re
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
    # 교육 매체 4곳(2026-08-19 추가). 일반 테크 매체만 보고 있어서 "교육"
    # 시장의 후보가 거의 안 걷혔다 — 실측: 기사 152건 중 제목에 교육이 걸린
    # 건 4건, 교육 검색어를 직접 던져도 주당 8건뿐이었다. 원인이 검색어가
    # 아니라 **창문(수집원)에 교육 매체가 없다**는 것이라, 소스를 늘린다.
    # 아래 패턴은 후보 검증(3주치 실측) 때 나온 실제 기사 URL 형태다.
    "www.edpl.co.kr/news/*",            # 교육플러스 — 3주 8건
    "edu.donga.com/news/*",             # 에듀동아 — 3주 7건
    "www.insidehighered.com/news/*",    # Inside Higher Ed — 3주 20건(상한)
    "www.insidehighered.com/opinion/*",
    "www.edweek.org/technology/*",      # EdWeek — 3주 6건
    "www.edweek.org/leadership/*",
    "www.edweek.org/teaching-learning/*",
    # 국내 IT 매체 2곳(2026-08-19 추가). 한국어 매체가 45%인데 국내 기업이
    # 언급된 기사는 7%(152건 중 10건)뿐이었다 — AI타임스·ITWorld·GeekNews가
    # 한국어지만 주로 글로벌 AI 소식을 전하는 성격이라 국내 기업·AX 시장
    # 소식이 비었다. 후보 검증에서 두 곳 다 국내 기업·AX 기사가 실제로 나왔다.
    "www.etnews.com/2*",                # 전자신문 — 기사 URL이 연도로 시작하는 숫자 ID
    "www.bloter.net/news/*",            # 블로터 — 한국 CMS 공통 형태
    # 국내 IT 매체 5곳 추가(2026-08-24, 담당자 요청 — "중요 뉴스 수집 가능한
    # 좋은 사이트" 확대). 패턴은 각 사이트 실제 기사 URL을 확인해서 뽑았다.
    # fnmatch에서 "?"는 와일드카드(임의의 문자 1개)라 물음표가 든 쿼리스트링
    # ("?no=", "?idxno=")은 리터럴로 매치되게 [?]로 이스케이프해야 한다 —
    # 안 하면 "?" 앞뒤 아무 글자에나 걸려 화이트리스트가 새는 사고가 난다.
    "www.zdnet.co.kr/view/[?]no=*",              # ZDNet Korea
    "www.ddaily.co.kr/page/view/*",              # 디지털데일리
    "byline.network/[0-9][0-9][0-9][0-9]/[0-9][0-9]/*",  # 바이라인네트워크 — /YYYY/MM/ 형태
    "www.digitaltoday.co.kr/news/articleView.html[?]idxno=*",  # 디지털투데이
    "www.techm.kr/news/articleView.html[?]idxno=*",             # 테크M
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
    # 교육 매체의 목록·저자 페이지. 한국 매체 둘은 같은 CMS라 목록 URL도 같은 모양이다.
    "www.edpl.co.kr/news/articleList.html*",
    "edu.donga.com/news/articleList.html*",
    "www.insidehighered.com/*/author/*",
    "www.edweek.org/*/author/*",
    "www.bloter.net/news/articleList.html*",
    "www.etnews.com/*/section*",
    # 새로 추가한 5곳의 목록/카테고리 페이지. include 패턴이 이미 기사 전용
    # 경로만 좁혀 잡고 있어 사실 대부분 도달 불가능하지만(예: ddaily의 목록
    # 경로 "/ai"는 애초에 include의 "/page/view/*"와 안 겹친다), 다른
    # 사이트들과 같은 이중 방어 관례를 맞춘다.
    "www.zdnet.co.kr/news/*",
    "www.zdnet.co.kr/newskey/*",
    "www.zdnet.co.kr/column/*",
    "www.zdnet.co.kr/photo/*",
    "www.ddaily.co.kr/ai*",
    "www.ddaily.co.kr/news*",
    "www.ddaily.co.kr/industry*",
    "www.ddaily.co.kr/enterprise*",
    "byline.network/category/*",
    "byline.network/special-report/*",
    "www.digitaltoday.co.kr/news/articleList.html[?]*",
    "www.techm.kr/news/articleList.html[?]*",
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
    "edpl.co.kr",
    "edu.donga.com",
    "insidehighered.com",
    "edweek.org",
    "etnews.com",
    "bloter.net",
    # 국내 IT 매체 5곳(2026-08-24 추가) — 담당자가 URL 패턴까지 확인한
    # 화이트리스트. 전부 AI 기사를 꾸준히 다루는 전문지다(위 include 패턴
    # 주석 참고).
    "zdnet.co.kr",
    "ddaily.co.kr",
    "byline.network",
    "digitaltoday.co.kr",
    "techm.kr",
]

# 사이트별 언어 — 어느 search_terms_* 목록을 쓸지 결정한다(2026-08-13 추가).
KOREAN_DOMAINS = {"itworld.co.kr", "aitimes.com", "news.hada.io", "edpl.co.kr",
                  "edu.donga.com", "etnews.com", "bloter.net",
                  "zdnet.co.kr", "ddaily.co.kr", "byline.network",
                  "digitaltoday.co.kr", "techm.kr"}
ENGLISH_DOMAINS = {"techcrunch.com", "askedtech.com", "techmeme.com",
                   "insidehighered.com", "edweek.org"}

# collected_articles.source_name에 남길 표시 이름(v3 수집기의 관례와 같은 모양).
SITE_NAMES = {
    "itworld.co.kr": "ITWorld",
    "aitimes.com": "AI타임스",
    "news.hada.io": "GeekNews",
    "techcrunch.com": "TechCrunch",
    "askedtech.com": "AskedTech",
    "techmeme.com": "Techmeme",
    "edpl.co.kr": "교육플러스",
    "edu.donga.com": "에듀동아",
    "insidehighered.com": "Inside Higher Ed",
    "edweek.org": "EdWeek",
    "etnews.com": "전자신문",
    "bloter.net": "블로터",
    "zdnet.co.kr": "ZDNet Korea",
    "ddaily.co.kr": "디지털데일리",
    "byline.network": "바이라인네트워크",
    "digitaltoday.co.kr": "디지털투데이",
    "techm.kr": "테크M",
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
BROAD_QUERIES_KO = ["AI", "인공지능"]
BROAD_QUERIES_EN = ["AI", "enterprise AI"]


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


# 제목 대신 사이트 이름만 들어오는 경우를 위한 스니펫 앞머리 정리(아래 참고).
# "Michael Veale / @michae.lv:", "@nymag.com:" 같은 출처 표기를 떼어낸다 —
# 콜론 앞이 짧고 "@"나 " / "를 포함할 때만 지운다(본문에 있는 콜론은 남긴다).
_ATTRIBUTION_RE = re.compile(r"^([^:]{0,80}):\s+")
DERIVED_TITLE_MAX = 110


def derive_title(item: dict, domain: str) -> str:
    """저장할 제목. Tavily가 제목을 못 주면 스니펫 앞머리로 대신한다.

    Techmeme에서 실측된 문제(2026-08-19): 수집된 42건 중 11건이 제목이
    "Techmeme"뿐이었다. techmeme.com/260818/p3 같은 URL은 리버 페이지의
    앵커라서 페이지 제목이 사이트 이름이고, 실제 헤드라인은 본문(스니펫)에
    들어 있다. 그대로 저장하면 라벨링 카드에 "Techmeme"만 떠서 사람이
    판단할 수가 없다 — 학습 데이터도 그만큼 버려진다.

    제목을 버리지 않고 **비어 있을 때만** 대체한다. 스니펫까지 없으면
    "(제목 없음)"으로 남긴다(조용히 행을 버리지 않는다 — 어떤 후보도 사라지면
    안 된다는 기존 원칙)."""
    site = SITE_NAMES.get(domain, domain)
    title = (item.get("title") or "").strip()

    # "Techmeme: Palona, which uses AI agents..."처럼 사이트 이름이 제목 앞에
    # 붙어 오는 경우가 있다 — 출처는 source_name에 이미 있으니 중복이다.
    for prefix in (f"{site}: ", f"{domain}: "):
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix):].strip()
            break

    if title.lower() not in {"", "(제목 없음)", site.lower(), domain.lower()}:
        return title

    snippet = " ".join((item.get("content") or "").split())
    if not snippet:
        return "(제목 없음)"

    match = _ATTRIBUTION_RE.match(snippet)
    if match and ("@" in match.group(1) or " / " in match.group(1)):
        snippet = snippet[match.end():]

    if len(snippet) <= DERIVED_TITLE_MAX:
        return snippet
    cut = snippet[:DERIVED_TITLE_MAX]
    head, _, tail = cut.rpartition(" ")
    return f"{head or cut}…"


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


def _fetch_site_results_no_news_topic(keyword: str, domain: str) -> list[dict]:
    """`topic="news"`가 이 도메인에서 안 통할 때 쓰는 대체 경로(2026-08-25 실측).

    실측: `topic="news"` + `start_date`/`end_date`로 국내 매체(대형 언론사
    포함 — 조선일보·한국경제·연합뉴스 등도 동일) 대부분이 항상 0건이었다.
    같은 도메인이라도 `topic` 없이(날짜 없이) 검색하면 실제 기사가 정상
    조회됐다(응답 시간도 즉시 0초 vs 국내 매체 쪽 4초+ — Tavily의 "news"
    색인이 국내 매체를 잘 안 다루는 것으로 보인다. 계정을 새로 발급해도
    동일해 계정 문제는 아니다).

    대가: 이 경로는 Tavily가 `published_date`를 안 준다 — 그래서 날짜
    범위 필터도 여기선 못 하고(호출부가 날짜로 좁히지 않는다), 저장 시
    발행일이 없는 채로 들어간다(_insert_pool_article 참고, "날짜 미상"
    버킷으로 화면에 보인다)."""
    payload = {
        "query": keyword,
        "search_depth": "basic",
        "max_results": RESULTS_PER_SITE,
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
    와 같게 맞춰서, 판단·라벨링·화면이 수집 방식을 구분하지 않게 한다.

    **URL 중복 외에 제목 중복도 막는다.** Techmeme은 리버 페이지 앵커가
    잡히는데 /260819/p3 과 /260819/p16 이 같은 글을 가리키는 경우가 있다
    (실측 2026-08-19: 41건 중 고유 제목 35건 = 6건이 중복). URL이 다르니
    ON CONFLICT (run_id, url)로는 안 걸러지고, 라벨링 화면에서 같은 내용을
    두 번 판단하게 된다. 정보를 버리는 게 아니라 같은 글을 한 번만 남기는
    것이라 URL 정규화와 같은 성격의 중복 제거다.

    **발행일 없는 기사도 이제(2026-08-25) 담는다** — 예전엔(2026-08-19
    결정) 아예 버렸었다. 뒤집은 이유: `topic="news"`가 국내 매체 대부분에서
    항상 0건이라(대형 언론사도 마찬가지, 위 `_fetch_site_results_no_news_topic`
    헤더 참고) 국내 수집 자체를 그 경로로 돌렸는데, 그 경로는 애초에
    `published_date`를 안 준다 — "발행일 없으면 버림" 규칙을 그대로 두면
    국내 기사가 전부(사실상 100%) 버려져 수집이 아예 안 되는 것과 같다.
    담당자 판단: "이번 주" 정확도(주차 분류·fold 분리)를 다소 잃더라도
    국내 기사가 아예 안 모이는 것보다 낫다 — 화면엔 이미 있는 "날짜 미상"
    버킷(labeling.UNDATED)으로 자연스럽게 보인다. 라벨의 주차 그룹은
    (labeling._label_period_start처럼) 수집 주로 폴백한다."""
    url = item.get("url")
    if not url:
        return False
    published_at = _parse_published_date(item.get("published_date"))
    title = derive_title(item, domain)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO collected_articles
                (run_id, source_name, fetch_method, title, url, source_domain, snippet, published_at)
            SELECT %s, %s, 'search', %s, %s, %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM collected_articles WHERE run_id = %s AND title = %s
            )
            ON CONFLICT (run_id, url) DO NOTHING
            RETURNING id
            """,
            (
                run_id,
                SITE_NAMES.get(domain, domain),
                title,
                normalize_url(url),
                _extract_domain(url),
                item.get("content"),
                published_at,
                run_id,
                title,
            ),
        )
        return cur.fetchone() is not None


def collect_pool_for_site(
    conn, run_id: int, domain: str, start_date: date, end_date: date,
    fixed_keyword: dict | None = None,
) -> dict:
    """사이트 하나에 질의를 각각 던져 공용 풀에 쌓는다.

    한 사이트가 죽어도 다른 사이트는 계속 진행한다 — 실패는 error에 담아
    정상 리턴하고, 파이프라인이 pipeline_report.stage_errors로 실패를 드러낸다
    (예외를 던지면 그 주 수집이 통째로 날아간다).

    **국내 매체는 `topic="news"` 경로를 안 쓴다(2026-08-25)** — 실측으로
    그 경로가 국내 매체 대부분에서 항상 0건임을 확인했다(대형 언론사 포함,
    새 Tavily 계정으로도 동일 — 계정 문제가 아니라 Tavily의 뉴스 색인이
    국내 매체를 잘 안 다룬다는 뜻, `_fetch_site_results_no_news_topic` 헤더
    참고). 그래서 `KOREAN_DOMAINS`는 `start_date`/`end_date` 없이 일반
    검색으로 돌리고, 그 결과는 발행일 없이 저장된다(_insert_pool_article
    참고) — start_date/end_date 인자를 그대로 받아두는 이유는 해외 매체
    호출에 여전히 쓰이기 때문이다.

    **fixed_keyword(2026-08-25, 팀별 배포 재설계)**: 이 배포의 팀(collect_all
    이 넘겨준다)이 있으면 그 팀의 언어별 검색어(`_terms_for_domain`)로
    질의하고, 없으면(테스트 등 하위 호환) 기존처럼 넓은 질의
    (`broad_queries_for_domain`)를 쓴다. 팀이 검색어를 아직 안 정했어도
    `_terms_for_domain` 자체가 넓은 질의로 폴백하므로 항상 안전하다."""
    name = SITE_NAMES.get(domain, domain)
    fetched = inserted = 0
    use_news_topic = domain not in KOREAN_DOMAINS
    queries = _terms_for_domain(fixed_keyword, domain) if fixed_keyword else broad_queries_for_domain(domain)

    for query in queries:
        try:
            if use_news_topic:
                items = _fetch_site_results(query, domain, start_date, end_date)
            else:
                items = _fetch_site_results_no_news_topic(query, domain)
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
    **넓은 질의(BROAD_QUERIES)로 폴백한다**(2026-08-25 변경 — 예전엔
    keyword 문자열 자체로 폴백했는데, "콘텐츠팀"처럼 팀 이름이 검색 의도와
    무관한 문자열이면 그대로 질의로 나가 무의미한 결과만 쌓이는 걸 실사용
    중 확인했다). 팀 이름은 표시용일 뿐 검색어가 아니라는 원칙을 여기서도
    지킨다."""
    if domain in KOREAN_DOMAINS:
        terms = fixed_keyword.get("search_terms_ko") or []
    else:
        terms = fixed_keyword.get("search_terms_en") or []
    return terms or broad_queries_for_domain(domain)


def collect_for_keyword(
    conn, run_id: int, fixed_keyword: dict, start_date: date, end_date: date,
) -> dict:
    """고정 키워드 하나에 대해 (사이트 × 그 언어의 검색어) 조합마다 개별 호출 후
    search_results에 저장. start_date/end_date는 이 run의 달력 주(db/weekly_run.
    get_run_period)다.

    **파이프라인은 이 경로를 쓰지 않는다**(2026-08-19부터 collect_pool). 대신
    2026-08-25부터 **팀별 자체 수집**(app/streamlit_app.py "새 팀 만들기") 이
    이 경로를 쓴다 — 시장별 검색어로 정밀도를 보강하고 싶을 때를 위한 경로였는데,
    실측상 넓은 질의(BROAD_QUERIES)만으로는 시장당 3~5건이라 라벨링·학습 물량이
    안 나온다는 한계(006 헤더)가, 오히려 "이 팀만 보고 싶은 좁은 주제"에는 장점이
    된다 — 넓게 긁지 않고 그 팀 검색어에 맞는 것만 모은다.

    **사이트 목록도 팀마다 고를 수 있다**: `fixed_keyword.get("site_domains")`가
    있으면 그 사이트들만 돈다(팀이 화면에서 고른 부분집합). 없으면(기존 호출부
    호환) 전체 화이트리스트(SITE_DOMAINS)를 그대로 쓴다.

    **국내 사이트는 topic="news" 경로를 안 쓴다** — collect_pool_for_site와
    같은 이유(실측 2026-08-25: topic=news가 국내 매체 대부분에서 항상 0건,
    `_fetch_site_results_no_news_topic` 헤더 참고)로 여기서도 똑같이 우회한다.
    """
    keyword = fixed_keyword["keyword"]
    domains = fixed_keyword.get("site_domains") or SITE_DOMAINS
    fetched = inserted = 0

    for domain in domains:
        for term in _terms_for_domain(fixed_keyword, domain):
            try:
                if domain in KOREAN_DOMAINS:
                    items = _fetch_site_results_no_news_topic(term, domain)
                else:
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


def collect_all(run_id: int, weeks: list[tuple[date, date]] | None = None) -> list[dict]:
    """공용 기사 풀을 만든다 — 이 배포의 팀(활성 고정 키워드) 검색어·사이트로
    수집(2026-08-25 재설계, 006 헤더의 예전 설계에서 전환).

    **한 배포 = 팀 하나다**: 활성 고정 키워드 중 첫 번째를 "이 배포의 팀"으로
    삼아, 그 팀이 설정한 언어별 검색어(비어있으면 넓은 질의로 폴백,
    `_terms_for_domain` 참고)와 사이트 목록(비어있으면 전체 화이트리스트로
    폴백)으로만 수집한다. 예전엔(006) 고정 키워드를 아예 안 보고 항상 전체
    화이트리스트에 넓은 질의("AI")만 던졌는데, 팀마다 레포를 통째로 포크해
    따로 배포하는 모델로 바뀌면서(README "다른 팀이 독립적으로 배포하기")
    "이 배포가 어느 팀인지" 자체를 고정 키워드로 표현하는 게 자연스러워졌다.
    활성 키워드가 하나도 없으면(배포 직후 팀 설정 전) 수집해도 볼 화면이
    없어 그대로 알린다.

    weeks를 주면 그 달력 주들을 **주차별로 따로** 호출한다(최초 3주치 수집).
    한 번에 3주 범위로 요청하지 않는 이유: Tavily는 페이지네이션이 없고 한
    호출당 최대 RESULTS_PER_SITE건이라, 넓게 잡으면 3주치가 20건으로 눌린다.
    주차별로 나누면 주당 20건씩 확보된다. 안 주면 run의 기준 주 하나만 걷는다.
    """
    if not settings.tavily_api_key:
        return [{"source": None, "fetched": 0, "inserted": 0, "error": "TAVILY_API_KEY 미설정 — .env 확인"}]

    conn = get_connection()
    try:
        active = get_active_fixed_keywords(conn)
        if not active:
            return [{
                "source": None, "fetched": 0, "inserted": 0,
                "error": "fixed_keywords에 활성 키워드 없음 — 배포 시 팀 설정을 먼저 하세요"
                         "(scripts/manage_fixed_keywords.py add)",
            }]
        team = active[0]
        domains = team.get("site_domains") or SITE_DOMAINS
        if weeks is None:
            weeks = [get_run_period(conn, run_id)]
        return [
            collect_pool_for_site(conn, run_id, domain, start_date, end_date, fixed_keyword=team)
            for start_date, end_date in weeks
            for domain in domains
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
