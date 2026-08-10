"""모니터링 MCP의 마스터 DB 조회 계층.

MCP 프로토콜 계층(server.py)과 분리해 순수 함수로 둔다(테스트·재사용 목적).

설계서 v2.0 §6: 도구는 내부 정성 데이터(마스터 DB)만 노출한다.
DART·특허·금융 등 공개 API는 프로젝트 ②(별도 MCP)로 분리되어 여기 포함하지 않는다.
"""

import re
from datetime import datetime, timedelta, timezone

import numpy as np

from tech_monitoring.db.connection import get_connection
from tech_monitoring.filters.embeddings import embed_texts

RRF_K = 60  # Stage2와 동일한 융합 상수(하이브리드 검색 일관성)
CANDIDATE_TOP_N = 200
SUMMARY_MAX_CHARS = 400
MAX_LIMIT = 50
# 다이제스트 구간의 기사를 파급력 순으로 가져올 상한.
# 응답이 Claude 컨텍스트로 들어가므로 무한정 늘리지 않는다.
DIGEST_FETCH_LIMIT = 500
RELATED_PER_CLUSTER = 5

_DURATION_RE = re.compile(r"^(\d+)\s*([hdw])$", re.IGNORECASE)
_UNIT_HOURS = {"h": 1, "d": 24, "w": 24 * 7}

# 파급력 근거로 사용자에게 보여줄 신호만 통과시킨다(filtered_stage 등 내부 값은 제외)
_EXPOSED_SIGNALS = (
    "source_trust",
    "aggregator_signal",
    "cluster_size",
    "recency",
    "hn_points",
    "cluster_member_count",
    "rerank_score",
    # Stage2b 참고 신호 — 관련도를 자동으로 걸러내는 데는 안 쓴다(stage2b 모듈 설명 참고).
    # 절대값이 극히 작게 압축돼 있어(0.0000~0.01대) 컷오프가 아니라 상대 비교 참고용.
    "relevance_rerank_score",
)

RANKING_BASIS = (
    "impact_score = source_trust 0.35 + aggregator_signal 0.25 "
    "+ cluster_size 0.20 + recency 0.20 (주관 중요도는 산정하지 않음)"
)


def parse_since(value: str | None, now: datetime) -> datetime | None:
    """'7d'·'24h'·'2w'·'2026-08-01'·ISO8601 → tz-aware datetime. None이면 기간 제한 없음."""
    if value is None or not str(value).strip():
        return None

    text = str(value).strip()
    match = _DURATION_RE.match(text)
    if match:
        hours = int(match.group(1)) * _UNIT_HOURS[match.group(2).lower()]
        return now - timedelta(hours=hours)

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"기간 형식을 해석할 수 없다: {value!r} (예: '7d', '24h', '2026-08-01')"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def resolve_period(period: str | None, now: datetime) -> tuple[datetime, datetime, str]:
    """주간 다이제스트 대상 구간.

    기본값 'last_week' = 전주 월요일 00:00 ~ 이번 주 월요일 00:00(UTC).
    스케줄 센싱이 "매주 전주 이슈"를 보므로 이번 주 진행분이 섞이지 않게 경계를 끊는다.
    """
    text = (period or "last_week").strip().lower()
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    if text in ("last_week", "전주", "지난주"):
        return week_start - timedelta(days=7), week_start, "last_week"
    if text in ("this_week", "금주", "이번주"):
        return week_start, now, "this_week"
    if _DURATION_RE.match(text):
        return parse_since(text, now), now, text
    if ".." in text:
        start_raw, end_raw = text.split("..", 1)
        start, end = parse_since(start_raw, now), parse_since(end_raw, now)
        if start is None or end is None:
            raise ValueError(f"기간 범위가 비어 있다: {period!r} (예: '2026-08-01..2026-08-07')")
        return start, end, text

    raise ValueError(
        f"기간을 해석할 수 없다: {period!r} "
        "(지원: 'last_week', 'this_week', '7d', '2026-08-01..2026-08-07')"
    )


def _clip(text: str | None) -> str | None:
    if not text:
        return None
    stripped = " ".join(text.split())
    if len(stripped) <= SUMMARY_MAX_CHARS:
        return stripped
    return stripped[:SUMMARY_MAX_CHARS] + "…"


def _round(value) -> float | None:
    return None if value is None else round(float(value), 4)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def to_article(row: dict, matched_by: str | None = None) -> dict:
    """DB row → MCP 응답 dict. 응답이 Claude 컨텍스트를 잡아먹지 않게 필드를 추린다."""
    signals = row.get("impact_signals") or {}
    article = {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "source": row["source"],
        "source_type": row["source_type"],
        "published_at": _iso(row.get("published_at")),
        "summary": _clip(row.get("summary")),
        "impact_score": _round(row.get("impact_score")),
        "relevance_score": _round(row.get("relevance_score")),
        "cluster_id": row.get("cluster_id"),
        "impact_signals": {
            key: _round(signals[key]) for key in _EXPOSED_SIGNALS if key in signals
        },
    }
    if matched_by:
        article["matched_by"] = matched_by
    return article


def rrf_fuse(
    keyword_ranks: dict[int, int], dense_ranks: dict[int, int]
) -> dict[int, tuple[float, str]]:
    """Reciprocal Rank Fusion. Stage2와 같은 방식으로 BM25·dense 순위를 합친다."""
    fused: dict[int, tuple[float, str]] = {}
    for article_id in set(keyword_ranks) | set(dense_ranks):
        score, methods = 0.0, []
        if article_id in keyword_ranks:
            score += 1 / (RRF_K + keyword_ranks[article_id])
            methods.append("keyword")
        if article_id in dense_ranks:
            score += 1 / (RRF_K + dense_ranks[article_id])
            methods.append("semantic")
        fused[article_id] = (score, "hybrid" if len(methods) > 1 else methods[0])
    return fused


_ARTICLE_FIELDS = """
    id, title, url, source, source_type, published_at, summary,
    relevance_score, impact_score, impact_signals, cluster_id
"""

# 필터에서 탈락한 기사(status='archived')는 조회 대상에서 제외한다.
_VISIBLE = """
    status <> 'archived'
    AND (%(since)s::timestamptz IS NULL OR published_at >= %(since)s::timestamptz)
    AND (%(until)s::timestamptz IS NULL OR published_at < %(until)s::timestamptz)
    AND coalesce(impact_score, 0) >= %(min_impact)s
"""


def _rows_to_dicts(cur) -> list[dict]:
    columns = [c.name for c in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _keyword_ranks(cur, query: str, params: dict) -> dict[int, int]:
    cur.execute(
        f"""
        SELECT id FROM articles
        WHERE {_VISIBLE} AND ts @@ plainto_tsquery('simple', %(query)s)
        ORDER BY ts_rank_cd(ts, plainto_tsquery('simple', %(query)s)) DESC
        LIMIT %(top_n)s
        """,
        {**params, "query": query, "top_n": CANDIDATE_TOP_N},
    )
    return {row[0]: rank for rank, row in enumerate(cur.fetchall(), start=1)}


def _dense_ranks(cur, query: str, params: dict) -> dict[int, int]:
    vector = np.array(embed_texts([query])[0], dtype=np.float32)
    cur.execute(
        f"""
        SELECT id FROM articles
        WHERE {_VISIBLE} AND embedding IS NOT NULL
        ORDER BY embedding <=> %(qvec)s::vector ASC
        LIMIT %(top_n)s
        """,
        {**params, "qvec": vector, "top_n": CANDIDATE_TOP_N},
    )
    return {row[0]: rank for rank, row in enumerate(cur.fetchall(), start=1)}


def _fetch_by_ids(cur, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    cur.execute(f"SELECT {_ARTICLE_FIELDS} FROM articles WHERE id = ANY(%s)", (ids,))
    return {row["id"]: row for row in _rows_to_dicts(cur)}


def search_news(
    query: str = "",
    since: str | None = None,
    until: str | None = None,
    min_impact: float = 0.0,
    limit: int = 20,
) -> dict:
    """하이브리드(BM25 + BGE-M3) 검색. query가 비면 파급력 순으로 반환한다."""
    now = datetime.now(timezone.utc)
    params = {
        "since": parse_since(since, now),
        "until": parse_since(until, now),
        "min_impact": float(min_impact),
    }
    limit = max(1, min(int(limit), MAX_LIMIT))
    query = (query or "").strip()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if not query:
                # 질의어 없이 "최근 파급력 큰 이슈"를 묻는 흐름 지원
                cur.execute(
                    f"""
                    SELECT {_ARTICLE_FIELDS} FROM articles
                    WHERE {_VISIBLE}
                    ORDER BY impact_score DESC NULLS LAST, published_at DESC NULLS LAST
                    LIMIT %(limit)s
                    """,
                    {**params, "limit": limit},
                )
                articles = [to_article(row, "impact_rank") for row in _rows_to_dicts(cur)]
            else:
                fused = rrf_fuse(
                    _keyword_ranks(cur, query, params), _dense_ranks(cur, query, params)
                )
                ranked = sorted(fused.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
                rows = _fetch_by_ids(cur, [article_id for article_id, _ in ranked])
                articles = [
                    to_article(rows[article_id], matched_by)
                    for article_id, (_, matched_by) in ranked
                    if article_id in rows
                ]
    finally:
        conn.close()

    return {
        "query": query,
        "since": _iso(params["since"]),
        "until": _iso(params["until"]),
        "min_impact": params["min_impact"],
        "count": len(articles),
        "ranked_by": RANKING_BASIS,
        "articles": articles,
    }


def get_weekly_digest(
    period: str = "last_week", limit: int = 10, min_impact: float = 0.0
) -> dict:
    """구간 내 기사를 이슈(cluster) 단위로 묶어 파급력 상위 이슈를 반환한다."""
    now = datetime.now(timezone.utc)
    start, end, label = resolve_period(period, now)
    limit = max(1, min(int(limit), MAX_LIMIT))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_ARTICLE_FIELDS} FROM articles
                WHERE {_VISIBLE}
                ORDER BY impact_score DESC NULLS LAST, published_at DESC NULLS LAST
                LIMIT %(fetch_limit)s
                """,
                {
                    "since": start,
                    "until": end,
                    "min_impact": float(min_impact),
                    "fetch_limit": DIGEST_FETCH_LIMIT,
                },
            )
            rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    clusters = diversify_by_day(build_clusters(rows))
    return {
        "period": {"label": label, "start": _iso(start), "end": _iso(end)},
        "total_articles": len(rows),
        "total_clusters": len(clusters),
        "truncated": len(rows) >= DIGEST_FETCH_LIMIT,
        "ranked_by": RANKING_BASIS,
        "clusters": clusters[:limit],
    }


def build_clusters(rows: list[dict]) -> list[dict]:
    """파급력 내림차순 row들을 cluster_id로 묶는다.

    rows가 이미 정렬되어 있으므로 각 클러스터의 첫 기사가 대표(lead)가 된다.
    cluster_id가 없는 기사(클러스터링 전)는 단독 이슈로 취급한다.
    """
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = row.get("cluster_id") or f"single-{row['id']}"
        grouped.setdefault(key, []).append(row)

    clusters = []
    for cluster_id, members in grouped.items():
        lead, rest = members[0], members[1:]
        clusters.append(
            {
                "cluster_id": cluster_id,
                "size": len(members),
                "lead": to_article(lead),
                "related": [
                    {
                        "title": row["title"],
                        "url": row["url"],
                        "source": row["source"],
                        "impact_score": _round(row.get("impact_score")),
                    }
                    for row in rest[:RELATED_PER_CLUSTER]
                ],
            }
        )
    return clusters


def diversify_by_day(clusters: list[dict]) -> list[dict]:
    """파급력 순으로 정렬된 클러스터를 발행일별로 라운드로빈해 날짜 쏠림을 없앤다.

    2026-08-10 실사용 중 발견: aggregator_signal이 속도(시간당 반응) 기반이라
    나이에 훨씬 민감하게 떨어진다 — recency 반감기를 아무리 느긋하게 잡아도
    이걸 못 이겨서, 1주일치를 봐야 하는 주간 다이제스트가 그 주 마지막 하루로
    통째로 쏠렸다(15건 중 14건이 같은 날). impact_score 자체는 실시간 검색에도
    쓰이므로 더 손대지 않고, "1주일을 고르게 보여준다"는 다이제스트 고유의
    요구사항은 여기서 후처리로 해결한다.

    각 날짜 내부의 파급력 순서는 그대로 유지하고, 날짜 간에는 번갈아가며 채운다
    (첫 라운드에 가장 높은 날짜부터 하나씩 → 그 날짜의 다음 것 → ...).
    """
    by_day: dict[str, list[dict]] = {}
    day_order: list[str] = []
    for cluster in clusters:
        day = (cluster["lead"].get("published_at") or "")[:10]
        if day not in by_day:
            by_day[day] = []
            day_order.append(day)
        by_day[day].append(cluster)

    result = []
    while len(result) < len(clusters):
        progressed = False
        for day in day_order:
            if by_day[day]:
                result.append(by_day[day].pop(0))
                progressed = True
        if not progressed:
            break
    return result
