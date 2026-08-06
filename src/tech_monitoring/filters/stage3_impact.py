"""Stage 3 · 파급력(impact) 스코어.

설계서 v2.0 §5 반영:
- 파급력 = 큐레이션 소스 중심 + 규칙 신호(source_trust·cluster_size·aggregator points) + 최신성
- **감성 분석·회사관점 LLM 중요도 판정은 미도입**
- **주관 중요도(importance)는 산정하지 않는다** — 파급력·최신성 랭킹만 제공하고 최종 판단은 사용자 몫
"""

import json
from datetime import datetime, timezone

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection

# 클러스터당 이 수 이상 매체가 동시 보도하면 파급력 신호 포화
CLUSTER_SIZE_SATURATION = 5


def _weights() -> dict[str, float]:
    return {
        "source_trust": settings.weight_source_trust,
        "aggregator_signal": settings.weight_aggregator_signal,
        "cluster_size": settings.weight_cluster_size,
        "recency": settings.weight_recency,
    }


def _recency_score(published_at, now) -> float:
    if published_at is None:
        return 0.5  # 발행일 불명 → 중립값
    hours = max((now - published_at).total_seconds() / 3600, 0)
    return 0.5 ** (hours / settings.recency_half_life_hours)


def _aggregator_signal_score(signals: dict) -> float:
    """HN points 등 실제 반향 수치. 수치가 없는 소스는 0(파급력 근거 없음)."""
    points = (signals or {}).get("hn_points")
    if points is None:
        return 0.0
    return min(float(points) / settings.hn_points_saturation, 1.0)


def compute_impact(row: dict, now: datetime) -> tuple[float, dict]:
    existing = row.get("impact_signals") or {}
    signals = {
        "source_trust": row["source_trust"] or 0.0,
        "aggregator_signal": _aggregator_signal_score(existing),
        # Stage5 클러스터링 전에는 중립값(단독 보도 = 1건)
        "cluster_size": existing.get("cluster_size", 1.0 / CLUSTER_SIZE_SATURATION),
        "recency": _recency_score(row["published_at"], now),
    }
    weights = _weights()
    score = sum(weights[k] * v for k, v in signals.items())
    return score, signals


def apply_stage3(batch_size: int = 2000) -> dict:
    conn = get_connection()
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_trust, published_at, impact_signals
            FROM articles WHERE status = 'new'
            LIMIT %s
            """,
            (batch_size,),
        )
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]

    with conn.cursor() as cur:
        for row in rows:
            score, signals = compute_impact(row, now)
            merged = {**(row["impact_signals"] or {}), **signals}
            cur.execute(
                "UPDATE articles SET impact_score = %s, impact_signals = %s::jsonb WHERE id = %s",
                (score, json.dumps(merged), row["id"]),
            )

    conn.close()
    return {"scored": len(rows)}


if __name__ == "__main__":
    print(apply_stage3())
