"""Stage 2b · 관련도 재점수(리랭커) — 참고 신호. 자동 제외에는 쓰지 않는다.

배경: Stage2의 코사인 유사도는 관련/무관 기사가 0.33~0.51 범위에서 매끈히
이어져 있어 안전하게 자를 지점이 없었다(README "Phase 3 조사 결과" 참고).
bge-reranker-v2-m3(질의-문서를 함께 보는 cross-encoder)로 다시 점수를
매기면 순위 자체는 눈에 띄게 나아진다 — 예: "Changes at Google DeepMind..."가
1위로, "Nikita Bier steps down..."(무관)이 최하위로 내려간다.

**그런데도 이 점수로 기사를 archived 처리하지는 않는다:**
1. 절대 점수가 0.0000~0.01 범위로 극히 작게 압축돼 있어 안전한 컷오프
   지점을 잡을 라벨 근거가 없다.
2. 실제로 "Prime Agent"(AI 에이전트 연구), "Muse Code"(Meta AI 코딩 도구)처럼
   명백히 AX 관련인 기사도 최하위권에 깔리는 오탐(false negative)을 확인했다.
3. 담당자 방침(8/7 대화) — 키워드·라벨 세트로 관련도를 좁히지 말고 AX 시장을
   넓게 보고 싶다. 자동 제외를 늘리는 방향은 이 방침과 맞지 않는다.

그래서 이 점수는 `impact_signals.relevance_rerank_score`에 참고용으로만
남긴다. 정렬·시각화(Phase 4)에서 "관련성 신뢰도가 낮은 편" 같은 보조
표시에는 쓸 수 있지만, 무엇을 보여줄지 최종 판단은 여전히 사용자 몫이다.
"""

import json

from tech_monitoring.db.connection import get_connection
from tech_monitoring.filters.stage4_rerank import get_reranker

# "AX 시장 관련도"를 묻는 질의 — Stage4의 DEFAULT_QUERY(파급력)와는 다른 축이다.
RELEVANCE_QUERY = "AI 도입과 활용에 관한 기업 시장 동향 뉴스"


def score_relevance(rows: list[dict], query: str = RELEVANCE_QUERY) -> list[float]:
    """rows: {"title", "summary", "content"} 딕셔너리 목록. 순수 함수 — 모델 테스트용 분리."""
    if not rows:
        return []
    model = get_reranker()
    pairs = [
        (query, f"{r['title']}\n{(r.get('content') or r.get('summary') or '')[:1000]}")
        for r in rows
    ]
    return [float(s) for s in model.predict(pairs)]


def apply_stage2b(batch_size: int = 300) -> dict:
    """status='new'인 기사에 관련성 참고 점수를 매긴다. 아무것도 archived하지 않는다."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, summary, content, impact_signals FROM articles
            WHERE status = 'new'
              AND NOT (impact_signals ? 'relevance_rerank_score')
            LIMIT %s
            """,
            (batch_size,),
        )
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]

    if not rows:
        conn.close()
        return {"scored": 0}

    scores = score_relevance(rows)

    with conn.cursor() as cur:
        for row, score in zip(rows, scores):
            merged = {**(row["impact_signals"] or {}), "relevance_rerank_score": score}
            cur.execute(
                "UPDATE articles SET impact_signals = %s::jsonb WHERE id = %s",
                (json.dumps(merged), row["id"]),
            )

    conn.close()
    return {"scored": len(rows)}


if __name__ == "__main__":
    print(apply_stage2b())
