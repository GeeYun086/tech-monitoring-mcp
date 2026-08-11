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


def _count_keywords(texts: list[str], top_n: int) -> list[dict]:
    """텍스트 목록에서 빈도 상위 단어를 뽑는 공통 로직. 텍스트 하나(기사 한 건
    또는 클러스터 대표 기사 한 건) 안에서 같은 단어가 여러 번 나와도 한 번만
    센다 — 긴 글 하나가 빈도수를 독점하지 않게."""
    counter: Counter[str] = Counter()
    for text in texts:
        cleaned = _clean_for_keywords(text)
        seen = set()
        for match in _WORD_RE.findall(cleaned):
            lower = match.lower()
            if lower in _STOPWORDS or match in _KOREAN_STOPWORDS or len(lower) < 2 or lower in seen:
                continue
            seen.add(lower)
            counter[match] += 1
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


def fetch_all_recent(days: int = 14) -> list[dict]:
    """대시보드 집계용 — MCP 도구(queries.py)의 응답 크기 제한과 무관하게
    최근 N일간 필터를 통과한 전체 기사를 직접 조회한다. 이 함수의 결과를
    그대로 Claude에게 보여주지 않는다 — 아래 compute_* 함수들이 집계한
    요약만 build()가 반환한다."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, published_at, title, summary, impact_score
                FROM articles
                WHERE status <> 'archived' AND published_at >= now() - make_interval(days => %s)
                ORDER BY published_at DESC
                """,
                (days,),
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
    texts = [
        f"{a.get('title') or ''} {a.get('summary') or ''}"
        for a in articles
        if classify_region(a["source"]) == region
    ]
    return _count_keywords(texts, top_n)


def build(period: str = "last_week", limit: int = 20, min_impact: float = 0.0, trend_days: int = 14) -> dict:
    digest = queries.get_weekly_digest(period=period, limit=limit, min_impact=min_impact)

    for cluster in digest["clusters"]:
        signals = cluster["lead"].get("impact_signals") or {}
        cluster["lead"]["impact_breakdown_pct"] = compute_breakdown_pct(signals)

    digest["keywords"] = extract_keywords(digest["clusters"])

    all_recent = fetch_all_recent(days=trend_days)
    digest["trends"] = {
        "window_days": trend_days,
        "total_domestic": sum(1 for a in all_recent if classify_region(a["source"]) == "domestic"),
        "total_global": sum(1 for a in all_recent if classify_region(a["source"]) == "global"),
        "volume_trend": compute_volume_trend(all_recent, trend_days),
        "source_distribution": compute_source_distribution(all_recent),
        "impact_distribution": compute_impact_distribution(all_recent),
        "keywords_domestic": compute_keyword_cloud(all_recent, "domestic"),
        "keywords_global": compute_keyword_cloud(all_recent, "global"),
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
    args = parser.parse_args()

    print(json.dumps(
        build(args.period, args.limit, args.min_impact, args.trend_days),
        ensure_ascii=False, indent=2,
    ))
