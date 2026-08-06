import json
from datetime import datetime, timezone

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection


def _weights() -> dict[str, float]:
    # 가중치 wi — 담당자 "중요도·파급력" 기준 수신 후 확정 [확인 필요]. settings.weight_* 로 파라미터화.
    return {
        "source_trust": settings.weight_source_trust,
        "aggregator_signal": settings.weight_aggregator_signal,
        "cluster_size": settings.weight_cluster_size,
        "recency": settings.weight_recency,
        "issue_type": settings.weight_issue_type,
        "sentiment": settings.weight_sentiment,
    }


# 파급력 큰 사건 신호(간단 키워드 휴리스틱) — 실사용 전 담당자 기준으로 보강 [확인 필요]
ISSUE_TYPE_KEYWORDS = (
    "acquisition", "acquire", "merger", "bankruptcy", "lawsuit", "regulation", "ban",
    "layoff", "funding", "ipo", "launch", "recall",
    "인수", "합병", "상장", "파산", "소송", "규제", "구조조정", "투자", "유치", "출시", "리콜",
)
NEGATIVE_TONE_KEYWORDS = (
    "lawsuit", "scandal", "decline", "layoff", "crash", "fraud", "breach", "recall", "ban",
    "소송", "논란", "하락", "해고", "붕괴", "사기", "유출", "리콜", "규제",
)


def _issue_type_score(text: str) -> float:
    lowered = text.lower()
    return 1.0 if any(kw in lowered for kw in ISSUE_TYPE_KEYWORDS) else 0.0


def _sentiment_score(text: str) -> float:
    lowered = text.lower()
    return 1.0 if any(kw in lowered for kw in NEGATIVE_TONE_KEYWORDS) else 0.0


def _recency_score(published_at, now) -> float:
    if published_at is None:
        return 0.5  # 발행일 불명 → 중립값
    hours = max((now - published_at).total_seconds() / 3600, 0)
    return 0.5 ** (hours / settings.recency_half_life_hours)


def _aggregator_signal_score(source_type: str) -> float:
    # HN points 등 실제 참여 신호는 아직 수집기에 저장하지 않음 — source_type 기반 근사치 [확인 필요]
    return 1.0 if source_type == "aggregator" else 0.0


def compute_importance(row: dict, now: datetime) -> tuple[float, dict]:
    text = f"{row['title']} {row.get('summary') or ''} {row.get('content') or ''}"
    signals = {
        "source_trust": row["source_trust"] or 0.0,
        "aggregator_signal": _aggregator_signal_score(row["source_type"]),
        "cluster_size": (row.get("importance_signals") or {}).get("cluster_size", 1.0 / 5),  # Stage5 이전 중립값
        "recency": _recency_score(row["published_at"], now),
        "issue_type": _issue_type_score(text),
        "sentiment": _sentiment_score(text),
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
            SELECT id, title, summary, content, source_type, source_trust, published_at, importance_signals
            FROM articles WHERE status = 'new'
            LIMIT %s
            """,
            (batch_size,),
        )
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]

    with conn.cursor() as cur:
        for row in rows:
            score, signals = compute_importance(row, now)
            merged_signals = {**(row["importance_signals"] or {}), **signals}
            cur.execute(
                "UPDATE articles SET importance_score = %s, importance_signals = %s::jsonb WHERE id = %s",
                (score, json.dumps(merged_signals), row["id"]),
            )

    conn.close()
    return {"scored": len(rows)}


if __name__ == "__main__":
    print(apply_stage3())
