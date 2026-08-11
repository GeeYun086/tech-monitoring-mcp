"""ax-dashboard 스킬용 데이터 준비 — 숫자 계산은 전부 여기서 끝낸다.

배경: 첫 데모(2026-08-07)를 만들 때 impact_signals의 가중치×값을 손으로
계산해서 HTML에 박아 넣었다. 데이터가 바뀔 때마다 다시 계산해야 하고
실수하기 쉬워 재사용이 안 됐다(.claude/skills/ax-dashboard/SKILL.md 참고).
이 스크립트가 계산을 전담하고, Claude는 번역·레이아웃 판단만 한다.

2026-08-11 확장: 담당자 요청 — "파급력 상위 기사 나열"만으로는 트렌드나
산업 동향을 한눈에 보기 어렵다. get_weekly_digest()의 이슈 목록(파급력
상위 N건)에 더해, **필터를 통과한 전체 데이터셋**(상위 N건으로 자르지
않음)을 직접 조회해 집계 지표를 계산한다:
  - volume_trend: 날짜별 국내/해외 수집량 추이
  - source_distribution: 소스별 건수(국내/해외 구분)
  - impact_distribution: 파급력 점수 분포(0.1 단위 구간)
  - keywords_domestic / keywords_global: 국내/해외 각각의 키워드 빈도

2026-08-11 2차 확장: 위 지표는 "수집 방법"에 대한 지표지 "산업 동향"
지표가 아니라는 담당자 피드백. 새 백엔드 파이프라인(감성분석·기술/지역
태깅)이 필요한 지표는 별도 작업으로 미루고, 지금 데이터(제목·요약·소스·
발행시각·클러스터)만으로 계산 가능한 것을 추가했다:
  - keyword_network: 키워드 동시출현 네트워크(노드+엣지)
  - rising_keywords: 직전 기간 대비 급상승 키워드(절대 증가폭 기준)
  - keyword_bubbles: 키워드 기반 "기술 부상도" 근사 버블차트(증가율×언급량×소스수)
  - keyword_gap: 국내/해외 비중 격차 — 한쪽에만 있는 화제
  - entity_ranking: 제목 고유명사(기업·인물명) 언급 랭킹 — 영문 제목 기준 한계 있음
  - cross_region_lag: 국내-해외 동일 클러스터 최초 보도 시차
  - co_report_intensity: 날짜별 동시보도(3건+) 클러스터 발생 빈도

cross_region_lag·co_report_intensity는 cluster_id가 배치마다 재사용되던
충돌 버그(fix/cluster-id-global-uniqueness에서 수정)가 고쳐져 있어야
의미 있는 값이 나온다.

이 집계 함수들은 MCP 도구(queries.py)를 거치지 않고 DB를 직접 조회한다 —
MCP 응답은 Claude 컨텍스트에 그대로 들어가므로 크기를 계속 제한해왔지만
(MAX_LIMIT=50 등), 이 스크립트의 출력은 원본 행이 아니라 이미 집계된
요약이라 전체 데이터셋을 계산에 써도 컨텍스트가 커지지 않는다.

국내/해외 분류는 추측이 아니라 sources 테이블에 실제로 등록한 소스
목록을 그대로 반영한다(DOMESTIC_SOURCES) — 새 소스를 추가하면 이 목록도
같이 갱신해야 한다.

    ./.venv/Scripts/python.exe scripts/dashboard_data.py --period last_week --limit 20
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.filters.stage5_cluster import _distinctive_tokens
from tech_monitoring.mcp_server import queries

# sources 테이블에 실제 등록된 국내 소스 이름 그대로(2026-08-11 기준).
# 새 국내 소스를 추가하면 여기도 같이 갱신해야 한다 — 언어 자동 감지가
# 아니라 "우리가 국내 매체로 등록한 것"이라는 명시적 목록이다.
DOMESTIC_SOURCES = frozenset({
    "전자신문", "바이라인네트워크", "AI타임스", "flex blog AX 허브",
    "GeekNews", "GeekNews Weekly", "ZDNet Korea",
})

# 한국어는 조사·어미가 규칙 기반으로 안 걸러져서(형태소 분석기 없음)
# 영어 불용어보다 더 대략적이다 — 뉴스 기사에 흔한 보도체 표현만 최소한으로 거른다.
_KOREAN_STOPWORDS = {
    "이번", "관련", "위해", "통해", "대한", "있다", "했다", "한다", "된다",
    "것으로", "밝혔다", "전했다", "지난", "올해", "대해", "이라고", "라며",
    "따르면", "이날", "가운데", "예정이다", "있는", "하는", "된", "등",
    # 2026-08-11 확장: 급상승 키워드·버블차트·워드클라우드를 전체 데이터셋으로
    # 돌려보니 "위한"·"실제"·"직접"·"새로운"·"기존"·"특히" 같은 순수 관형사·
    # 부사가 마치 기술 트렌드인 것처럼 상위권을 차지했다 — 내용 있는 명사가
    # 아니라 문장을 꾸미는 기능어일 뿐이다(위 영어 _STOPWORDS와 같은 성격).
    "위한", "실제", "직접", "새로운", "기존", "특히", "최근", "넘어",
    "아니라", "주요", "기반", "것이", "같은",
    # 형태소 분석 없이 정규식 토큰화만 해서 "AI"에 조사가 붙은 채로 별도
    # 단어처럼 집계되는 문제(README/SKILL.md에 기록된 알려진 한계)가
    # 특히 자주 나오는 조합 몇 개는 직접 걸러낸다 — 일반적인 조사 분리
    # 해법은 아니고(형태소 분석기가 필요), "AI" 특정 사례만 임시 대응.
    "AI가", "AI는", "AI를", "AI도", "AI와", "AI의", "AI에",
}

# 흔한 영어 기능어만 거른다 — 도메인 키워드를 임의로 편집하지 않기 위해
# 최소한으로 유지한다(이 목록이 길어지면 사실상 키워드 필터가 되어버린다).
# 여기 있는 건 전부 대명사·전치사·조동사 같은 순수 문법 기능어다 — 특정
# 주제를 배제하는 게 아니라 "내용이 없는 단어"만 거르는 것이라 관련도
# 필터와는 성격이 다르다.
# 2026-08-11 확장: 전체 데이터셋(142건)으로 돌려보니 "we"·"they"·"their"·
# "through" 같은 대명사·전치사가 상위권에 섞여 나왔다 — 클러스터 대표
# 요약(짧음)만 보던 이전 버전에서는 안 드러났던 문제.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for",
    "with", "at", "by", "from", "up", "about", "into", "over", "after",
    "is", "are", "was", "were", "be", "been", "being", "as", "it", "its",
    "this", "that", "these", "those", "has", "have", "had", "will", "would",
    "can", "could", "not", "no", "than", "then", "so", "if", "how", "what",
    "who", "which", "new", "says", "said", "more", "out", "now", "just",
    "we", "they", "their", "them", "our", "your", "you", "he", "she", "his",
    "her", "there", "here", "when", "where", "why", "all", "also", "some",
    "any", "each", "other", "such", "most", "many", "much", "through",
    "across", "first", "one", "two", "three", "get", "gets", "getting",
    "make", "makes", "making", "like", "still", "even", "while", "using",
    "use", "used",
}
_WORD_RE = re.compile(r"[A-Za-z가-힣][A-Za-z가-힣'\-]{1,}")
_URL_RE = re.compile(r"https?://\S+")
# hnrss처럼 본문이 없는 소스는 summary가 "Article URL: ... Comments URL: ...
# Points: N # Comments: M" 같은 구조적 메타데이터뿐이다(rss.py의 parse_hn_points가
# 파싱해 impact_signals.hn_points로 이미 뽑아간 바로 그 텍스트). 이건 기사 내용이
# 아니라 라벨이므로 키워드 후보에서 제외한다 — 안 그러면 "URL"·"Comments"·
# "Points" 같은 게 실제 주제어 자리를 차지한다(실측: 상위 10개 중 8개가 이거였음).
_HN_METADATA_RE = re.compile(
    r"Article URL:|Comments URL:|Points:\s*\d+|#\s*Comments:\s*\d+", re.IGNORECASE
)


def _clean_for_keywords(text: str) -> str:
    text = _URL_RE.sub(" ", text)
    text = _HN_METADATA_RE.sub(" ", text)
    return text


def classify_region(source: str) -> str:
    """소스 이름 → 'domestic'(국내) 또는 'global'(해외). 언어 자동 감지가
    아니라 DOMESTIC_SOURCES에 실제로 등록한 소스 목록 기반이다."""
    return "domestic" if source in DOMESTIC_SOURCES else "global"


def _tokens(text: str) -> set[str]:
    """텍스트 한 건에서 불용어·URL·HN 메타데이터를 걸러낸 토큰 집합(중복 제거,
    대소문자 다른 변형은 첫 등장 표기만 남김). _count_keywords와 아래 새 집계
    함수들(동시출현·급상승·버블·갭·엔티티)이 전부 이 함수 하나를 공유한다 —
    "무엇을 단어로 볼지"에 대한 판단이 여러 곳에 흩어지면 필터 기준이
    은근슬쩍 갈라진다."""
    cleaned = _clean_for_keywords(text)
    result: set[str] = set()
    seen_lower: set[str] = set()
    for raw_match in _WORD_RE.findall(cleaned):
        # _WORD_RE가 아포스트로피·하이픈을 단어 중간 문자로 허용해서, "AI's"
        # 뒤에 온점·따옴표가 곧장 붙으면 "AI'"처럼 꼬리에 구두점만 남은 조각이
        # 매치될 수 있다(실사용 중 발견: 국내/해외 갭 분석에 "AI'"가 그대로
        # 노출됐다). 앞뒤 아포스트로피·하이픈은 잘라내고 판단한다.
        match = raw_match.strip("'-")
        if not match:
            continue
        lower = match.lower()
        if lower in _STOPWORDS or match in _KOREAN_STOPWORDS or len(lower) < 2 or lower in seen_lower:
            continue
        seen_lower.add(lower)
        result.add(match)
    return result


def _article_text(article: dict) -> str:
    return f"{article.get('title') or ''} {article.get('summary') or ''}"


def _count_keywords(texts: list[str], top_n: int) -> list[dict]:
    """텍스트 목록에서 빈도 상위 단어를 뽑는다. 텍스트 하나(기사 한 건 또는
    클러스터 대표 기사 한 건) 안에서 같은 단어가 여러 번 나와도 한 번만
    센다 — 긴 글 하나가 빈도수를 독점하지 않게."""
    counter: Counter[str] = Counter()
    for text in texts:
        counter.update(_tokens(text))
    return [{"word": word, "count": count} for word, count in counter.most_common(top_n)]


def _weights() -> dict[str, float]:
    return {
        "trust": settings.weight_source_trust,
        "agg": settings.weight_aggregator_signal,
        "cluster": settings.weight_cluster_size,
        "recency": settings.weight_recency,
    }


def compute_breakdown_pct(signals: dict) -> dict[str, float]:
    """impact_signals → 4개 신호의 가중 기여도 백분율. 합이 100에 가깝다
    (반올림 오차 제외 — impact_score 자체가 이 4개의 가중합이므로)."""
    w = _weights()
    raw = {
        "trust": (signals.get("source_trust") or 0.0) * w["trust"],
        "agg": (signals.get("aggregator_signal") or 0.0) * w["agg"],
        "cluster": (signals.get("cluster_size") or 0.0) * w["cluster"],
        "recency": (signals.get("recency") or 0.0) * w["recency"],
    }
    total = sum(raw.values())
    if total <= 0:
        return {k: 0.0 for k in raw}
    return {k: round(v / total * 100, 1) for k, v in raw.items()}


def extract_keywords(clusters: list[dict], top_n: int = 15) -> list[dict]:
    """대표 기사(lead)의 제목·요약에서 빈도 상위 단어 추출. 클러스터 하나당
    한 번만 세어(같은 이슈의 반복 기사에 중복 가중되지 않게) 실제 이슈 분포를 반영한다."""
    texts = []
    for cluster in clusters:
        lead = cluster.get("lead") or {}
        texts.append(f"{lead.get('title') or ''} {lead.get('summary') or ''}")
    return _count_keywords(texts, top_n)


def fetch_all_recent(days: int = 14, offset_days: int = 0) -> list[dict]:
    """대시보드 집계용 — MCP 도구(queries.py)의 응답 크기 제한과 무관하게
    필터를 통과한 전체 기사를 직접 조회한다. 이 함수의 결과를 그대로
    Claude에게 보여주지 않는다 — 아래 compute_* 함수들이 집계한 요약만
    build()가 반환한다.

    offset_days>0이면 "지금부터 days+offset_days일 전 ~ offset_days일 전"
    구간을 가져온다 — 예: days=7, offset_days=7 → 급상승 키워드 비교용
    "전기(前期)" 데이터(지지난 7일). cluster_id도 함께 뽑는다 — 국내-해외
    보도 시차·동시보도 강도 집계에 필요하다(cluster_id 배치 충돌 버그를
    고친 fix/cluster-id-global-uniqueness가 먼저 적용돼 있어야 이 값들이
    의미를 가진다)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, published_at, title, summary, impact_score, cluster_id
                FROM articles
                WHERE status <> 'archived'
                  AND published_at >= now() - make_interval(days => %s)
                  AND published_at <  now() - make_interval(days => %s)
                ORDER BY published_at DESC
                """,
                (days + offset_days, offset_days),
            )
            columns = [c.name for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def compute_volume_trend(articles: list[dict], days: int, now: datetime | None = None) -> list[dict]:
    """날짜별 국내/해외 수집량. 데이터가 없는 날도 0으로 채워 넣어 추이선이
    끊기지 않게 한다(빈 날을 건너뛰면 실제보다 활발해 보이는 착시가 생긴다)."""
    now = now or datetime.now(timezone.utc)
    counts: dict[str, dict[str, int]] = {}
    today = now.date()
    for offset in range(days):
        day = (today - timedelta(days=offset)).isoformat()
        counts[day] = {"domestic": 0, "global": 0}

    for article in articles:
        published = article.get("published_at")
        if published is None:
            continue
        day = published.date().isoformat()
        if day not in counts:
            continue  # 집계 기간(days) 밖 — fetch_all_recent가 이미 이 범위로 걸렀지만 방어적으로
        counts[day][classify_region(article["source"])] += 1

    return [
        {"date": day, "domestic": v["domestic"], "global": v["global"]}
        for day, v in sorted(counts.items())
    ]


def compute_source_distribution(articles: list[dict]) -> list[dict]:
    """소스별 건수(내림차순) + 국내/해외 구분."""
    counter = Counter(a["source"] for a in articles)
    return sorted(
        (
            {"source": source, "region": classify_region(source), "count": count}
            for source, count in counter.items()
        ),
        key=lambda row: -row["count"],
    )


# impact_score 실측 분포(0.3~0.8 대역에 몰림)에 맞춰 구간을 임의로 고르지
# 않는다 — 0.0~1.0을 0.1 단위로 고정 분할해 "어디에 몰려있는지"를 있는
# 그대로 보여준다.
_IMPACT_BUCKET_WIDTH = 0.1


def compute_impact_distribution(articles: list[dict]) -> list[dict]:
    """파급력 점수 분포를 0.1 단위 구간으로 국내/해외 나눠 집계."""
    buckets: dict[float, dict[str, int]] = {}
    n_buckets = round(1.0 / _IMPACT_BUCKET_WIDTH)
    for i in range(n_buckets):
        buckets[round(i * _IMPACT_BUCKET_WIDTH, 1)] = {"domestic": 0, "global": 0}

    for article in articles:
        score = article.get("impact_score")
        if score is None:
            continue
        bucket = min(round((score // _IMPACT_BUCKET_WIDTH) * _IMPACT_BUCKET_WIDTH, 1), 0.9)
        buckets[bucket][classify_region(article["source"])] += 1

    return [
        {"bucket_start": b, "domestic": v["domestic"], "global": v["global"]}
        for b, v in sorted(buckets.items())
    ]


def compute_keyword_cloud(articles: list[dict], region: str, top_n: int = 20) -> list[dict]:
    """지정한 지역(국내/해외)의 전체 기사 제목·요약에서 빈도 상위 단어를 뽑는다.
    관련도 필터 기준이 아니라 "무슨 단어가 자주 나오는지" 참고 표시용이다."""
    texts = [_article_text(a) for a in articles if classify_region(a["source"]) == region]
    return _count_keywords(texts, top_n)


# ---- 2026-08-11 2차 확장: 담당자 피드백 — 기존 지표(추이·분포·워드클라우드)는
# "수집 방법"에 대한 지표지 "산업 동향" 지표가 아니다. 새 백엔드 파이프라인
# (감성분석·기술/지역 태깅)이 필요한 지표는 별도 작업으로 미루고, 지금 데이터
# (제목·요약·소스·발행시각·클러스터)만으로 만들 수 있는 것부터 붙인다.


def compute_keyword_network(articles: list[dict], top_nodes: int = 24, min_weight: int = 2) -> dict:
    """키워드 동시출현 네트워크 — 기사 한 건 안에서 함께 등장한 두 키워드를
    엣지로 잇는다. 빈도 상위 top_nodes개만 노드로 남긴다(전부 그리면 읽을 수
    없는 그래프가 된다). min_weight 미만(우연한 1회 동시출현)은 노이즈로
    보고 제외 — 기술 융합·화제 간 연관성을 보여주기 위한 것이지 모든 동시
    등장을 다 그리려는 게 아니다."""
    token_sets = [_tokens(_article_text(a)) for a in articles]
    freq: Counter[str] = Counter()
    for tokens in token_sets:
        freq.update(tokens)
    top_words = {w for w, _ in freq.most_common(top_nodes)}

    edge_counts: Counter[tuple[str, str]] = Counter()
    for tokens in token_sets:
        present = sorted(tokens & top_words)
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                edge_counts[(present[i], present[j])] += 1

    nodes = sorted(({"word": w, "count": freq[w]} for w in top_words), key=lambda n: -n["count"])
    edges = [
        {"source": a, "target": b, "weight": w}
        for (a, b), w in edge_counts.most_common()
        if w >= min_weight
    ]
    return {"nodes": nodes, "edges": edges}


def compute_rising_keywords(current: list[dict], previous: list[dict], top_n: int = 15) -> list[dict]:
    """이번 기간 대비 직전 기간 키워드 빈도 증가폭(delta) 상위 — "이주의
    급상승 키워드". 증가율(%)이 아니라 절대 증가폭을 1차 정렬 기준으로
    쓴다 — 1건→3건처럼 표본이 작은 단어가 "200% 증가"로 과장되는 걸
    막기 위해서다(비율은 극단값에 취약하다)."""
    now_counter: Counter[str] = Counter()
    for a in current:
        now_counter.update(_tokens(_article_text(a)))
    prev_counter: Counter[str] = Counter()
    for a in previous:
        prev_counter.update(_tokens(_article_text(a)))

    rows = []
    for word, count_now in now_counter.items():
        count_prev = prev_counter.get(word, 0)
        delta = count_now - count_prev
        if delta <= 0:
            continue
        rows.append({
            "word": word, "count_now": count_now, "count_prev": count_prev,
            "delta": delta, "is_new": count_prev == 0,
        })
    rows.sort(key=lambda r: (-r["delta"], -r["count_now"]))
    return rows[:top_n]


def compute_keyword_bubbles(current: list[dict], previous: list[dict], top_n: int = 20) -> list[dict]:
    """버블차트용 근사 지표 — X: 전기 대비 증가율, Y: 이번 기간 언급량,
    size: 이 키워드를 다룬 서로 다른 소스 수. **진짜 "기술 부상도"가
    아니다** — 기사별 기술/기업 분류(NER)가 없어 키워드 빈도를 대신
    쓴 프록시다. 일반 단어("AI" 등)도 그대로 섞여 나온다는 한계를
    대시보드에도 명시해야 한다."""
    now_counter: Counter[str] = Counter()
    prev_counter: Counter[str] = Counter()
    word_sources: dict[str, set[str]] = {}
    for a in current:
        tokens = _tokens(_article_text(a))
        now_counter.update(tokens)
        for token in tokens:
            word_sources.setdefault(token, set()).add(a["source"])
    for a in previous:
        prev_counter.update(_tokens(_article_text(a)))

    rows = []
    for word, count_now in now_counter.most_common(top_n * 2):
        count_prev = prev_counter.get(word, 0)
        growth_rate = (count_now - count_prev) / count_prev if count_prev > 0 else float(count_now)
        rows.append({
            "word": word,
            "mention_count": count_now,
            "growth_rate": round(growth_rate, 2),
            "source_count": len(word_sources.get(word, ())),
            "is_new": count_prev == 0,
        })
    rows.sort(key=lambda r: -r["mention_count"])
    return rows[:top_n]


def compute_keyword_gap(articles: list[dict], top_n: int = 12) -> dict:
    """국내/해외 각각의 "전체 기사 대비 비중(share, %)"을 비교해 한쪽에서만
    두드러지는 키워드를 뽑는다. count 원값이 아니라 지역별 총 기사 수로
    정규화한 비중을 쓴다 — 두 지역의 표본 크기(기사 수)가 다르기 때문에
    원값을 그대로 비교하면 표본이 큰 쪽이 항상 이긴다.

    영문(ASCII) 토큰만 비교 대상으로 삼는다 — 실사용 중 발견: 제한을 안
    걸면 "모델"(국내만) vs "model"(해외만)처럼 같은 개념의 번역어가 서로
    다른 문자열이라 "격차"로 잘못 잡힌다("인공지능"·"모델"·"데이터" 등이
    domestic_only 상위를 그대로 차지했었다). 이건 진짜 화제 격차가 아니라
    "국내는 한국어로 쓴다"는 언어 차이일 뿐이다. AI 업계 기사는 영문
    기술용어·고유명사를 번역하지 않고 그대로 쓰는 경우가 많아(예: 국내
    기사에도 "LLM"·"GPT" 그대로 등장), 영문 토큰으로 좁히면 두 지역
    코퍼스에서 실제로 비교 가능한 대상만 남는다 — 그만큼 domestic_only는
    비어 있거나 짧을 수 있다(그 자체로 "국내 매체가 아직 다루지 않는
    해외발 화제가 많다"는 정직한 결과다)."""
    dom_articles = [a for a in articles if classify_region(a["source"]) == "domestic"]
    glo_articles = [a for a in articles if classify_region(a["source"]) == "global"]
    dom_total = len(dom_articles) or 1
    glo_total = len(glo_articles) or 1

    dom_counter: Counter[str] = Counter()
    for a in dom_articles:
        dom_counter.update(w for w in _tokens(_article_text(a)) if w.isascii())
    glo_counter: Counter[str] = Counter()
    for a in glo_articles:
        glo_counter.update(w for w in _tokens(_article_text(a)) if w.isascii())

    candidates = {w for w, _ in glo_counter.most_common(40)} | {w for w, _ in dom_counter.most_common(40)}
    rows = []
    for word in candidates:
        dom_share = round(dom_counter.get(word, 0) / dom_total * 100, 2)
        glo_share = round(glo_counter.get(word, 0) / glo_total * 100, 2)
        rows.append({"word": word, "domestic_share": dom_share, "global_share": glo_share})

    global_only = sorted(
        (r for r in rows if r["domestic_share"] == 0 and r["global_share"] > 0),
        key=lambda r: -r["global_share"],
    )[:top_n]
    domestic_only = sorted(
        (r for r in rows if r["global_share"] == 0 and r["domestic_share"] > 0),
        key=lambda r: -r["domestic_share"],
    )[:top_n]
    return {"global_only": global_only, "domestic_only": domestic_only}


def compute_entity_ranking(articles: list[dict], top_n: int = 15) -> list[dict]:
    """제목에서 고유명사(기업·인물명 등)만 추출한 언급 빈도 랭킹 — 일반
    명사가 섞인 키워드 클라우드와 달리 "누가 화제의 중심인가"를 보여준다.
    stage5_cluster._distinctive_tokens()를 그대로 재사용한다(클러스터링
    오탐 방지용으로 이미 검증된 "제목에서 진짜 고유명사만 뽑는" 로직).
    한계: 영문 대문자 표기 기반이라 한글 제목에는 적용되지 않는다 — 이
    랭킹은 사실상 "영문 제목 기준" 인물·기업 랭킹이고, 국내 기사의 기업명
    (삼성전자·네이버 등)은 잡히지 않는다. 이 한계는 대시보드에 그대로
    명시한다(고유명사 사전을 새로 만드는 건 관련도 필터와 같은 이유로
    보류 — 새 기업이 계속 등장하는데 고정 목록은 유지가 안 된다)."""
    counter: Counter[str] = Counter()
    for a in articles:
        counter.update(_distinctive_tokens(a.get("title") or ""))
    return [{"entity": word, "count": count} for word, count in counter.most_common(top_n)]


def compute_cross_region_lag(articles: list[dict]) -> dict:
    """같은 클러스터(cluster_id) 안에 해외·국내 기사가 함께 있으면, 해외
    최초 보도 시점부터 국내 최초 보도 시점까지 걸린 시간을 계산한다 —
    "국내 시장이 해외 트렌드를 얼마나 빨리 따라잡는가" 지표.
    cluster_id가 배치(파이프라인 실행)마다 재사용되던 충돌 버그를 먼저
    고쳐야(fix/cluster-id-global-uniqueness) 이 값이 의미를 가진다 —
    고치기 전엔 무관한 배치의 클러스터가 같은 이름으로 섞여 시차가
    엉뚱하게 나왔다. 국내가 해외보다 먼저 보도한 경우(음수 시차)는
    "추격 시차"의 정의 밖이라 집계에서 제외한다(별도 사례로 다룰 만하지만
    지금은 표본이 적어 노이즈와 구분이 어렵다)."""
    by_cluster: dict[str, list[dict]] = {}
    for a in articles:
        cluster_id = a.get("cluster_id")
        if cluster_id:
            by_cluster.setdefault(cluster_id, []).append(a)

    pairs = []
    for cluster_id, members in by_cluster.items():
        dom = [m for m in members if classify_region(m["source"]) == "domestic" and m.get("published_at")]
        glo = [m for m in members if classify_region(m["source"]) == "global" and m.get("published_at")]
        if not dom or not glo:
            continue
        first_glo = min(glo, key=lambda m: m["published_at"])
        first_dom = min(dom, key=lambda m: m["published_at"])
        lag_hours = (first_dom["published_at"] - first_glo["published_at"]).total_seconds() / 3600
        if lag_hours < 0:
            continue
        pairs.append({
            "cluster_id": cluster_id,
            "global_source": first_glo["source"], "global_title": first_glo.get("title"),
            "global_published_at": first_glo["published_at"].isoformat(),
            "domestic_source": first_dom["source"], "domestic_title": first_dom.get("title"),
            "domestic_published_at": first_dom["published_at"].isoformat(),
            "lag_hours": round(lag_hours, 1),
        })

    pairs.sort(key=lambda p: p["lag_hours"])
    lag_values = [p["lag_hours"] for p in pairs]
    avg_lag = round(sum(lag_values) / len(lag_values), 1) if lag_values else None
    return {"count": len(pairs), "avg_lag_hours": avg_lag, "pairs": pairs[:10]}


_CO_REPORT_MIN_SIZE = 3  # queries.py의 "N개 매체 동시보도" 배지(build_clusters)와는
# 별개 기준이다 — 대시보드 전용 집계이므로 여기서 독립적으로 "동시보도로 볼
# 만한 규모"를 정의한다.


def compute_co_report_intensity(articles: list[dict], days: int, now: datetime | None = None) -> list[dict]:
    """날짜별로 "3건 이상 매체가 동시보도한 클러스터"가 며칠에 몇 건 있었는지
    집계 — 진짜 큰 이슈가 얼마나 자주 터지는가의 추이. cluster_id별 크기는
    이 조회 윈도우(days) 안에서 관측된 멤버 수로 계산한다(윈도우 밖에 더
    있는 멤버는 못 본다 — 실제 클러스터 크기보다 작게 잡힐 수 있는 근사치).
    데이터 없는 날도 0으로 채운다(compute_volume_trend와 같은 이유 —
    빈 날을 건너뛰면 착시가 생긴다)."""
    now = now or datetime.now(timezone.utc)
    cluster_sizes: Counter[str] = Counter()
    for a in articles:
        if a.get("cluster_id"):
            cluster_sizes[a["cluster_id"]] += 1
    big_clusters = {cid for cid, size in cluster_sizes.items() if size >= _CO_REPORT_MIN_SIZE}

    counts: dict[str, int] = {}
    today = now.date()
    for offset in range(days):
        counts[(today - timedelta(days=offset)).isoformat()] = 0

    seen_per_day: dict[str, set[str]] = {}
    for a in articles:
        cluster_id = a.get("cluster_id")
        published = a.get("published_at")
        if cluster_id not in big_clusters or published is None:
            continue
        day = published.date().isoformat()
        if day not in counts:
            continue
        seen_per_day.setdefault(day, set()).add(cluster_id)

    return [
        {"date": day, "big_cluster_count": len(seen_per_day.get(day, ()))}
        for day in sorted(counts)
    ]


def build(
    period: str = "last_week",
    limit: int = 20,
    min_impact: float = 0.0,
    trend_days: int = 14,
    compare_days: int = 7,
) -> dict:
    digest = queries.get_weekly_digest(period=period, limit=limit, min_impact=min_impact)

    for cluster in digest["clusters"]:
        signals = cluster["lead"].get("impact_signals") or {}
        cluster["lead"]["impact_breakdown_pct"] = compute_breakdown_pct(signals)

    digest["keywords"] = extract_keywords(digest["clusters"])

    all_recent = fetch_all_recent(days=trend_days)
    # 급상승 키워드·버블차트는 "이번 기간 vs 바로 직전 같은 길이의 기간"을
    # 비교한다 — trend_days(추이 그래프용, 기본 14일)와 별개로 compare_days
    # (기본 7일)를 쓴다. 두 창은 겹치지 않는다(offset_days로 뒤로 민다).
    current_period = fetch_all_recent(days=compare_days)
    previous_period = fetch_all_recent(days=compare_days, offset_days=compare_days)

    digest["trends"] = {
        "window_days": trend_days,
        "compare_days": compare_days,
        "total_domestic": sum(1 for a in all_recent if classify_region(a["source"]) == "domestic"),
        "total_global": sum(1 for a in all_recent if classify_region(a["source"]) == "global"),
        "volume_trend": compute_volume_trend(all_recent, trend_days),
        "source_distribution": compute_source_distribution(all_recent),
        "impact_distribution": compute_impact_distribution(all_recent),
        "keywords_domestic": compute_keyword_cloud(all_recent, "domestic"),
        "keywords_global": compute_keyword_cloud(all_recent, "global"),
        "keyword_network": compute_keyword_network(all_recent),
        "rising_keywords": compute_rising_keywords(current_period, previous_period),
        "keyword_bubbles": compute_keyword_bubbles(current_period, previous_period),
        "keyword_gap": compute_keyword_gap(all_recent),
        "entity_ranking": compute_entity_ranking(all_recent),
        "cross_region_lag": compute_cross_region_lag(all_recent),
        "co_report_intensity": compute_co_report_intensity(all_recent, trend_days),
    }
    return digest


if __name__ == "__main__":
    # Windows 콘솔 코드페이지(cp949 등)로 리다이렉트하면 em dash 같은 문자에서
    # UnicodeEncodeError가 난다. 어떤 환경에서 실행되든 안전하게 UTF-8로 낸다.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", default="last_week")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-impact", type=float, default=0.0, dest="min_impact")
    parser.add_argument("--trend-days", type=int, default=14, dest="trend_days")
    parser.add_argument("--compare-days", type=int, default=7, dest="compare_days")
    args = parser.parse_args()

    print(json.dumps(
        build(args.period, args.limit, args.min_impact, args.trend_days, args.compare_days),
        ensure_ascii=False, indent=2,
    ))
