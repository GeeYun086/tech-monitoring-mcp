import json
from functools import lru_cache

from tech_monitoring.db.connection import get_connection

MODEL_NAME = "BAAI/bge-reranker-v2-m3"

# 리랭커 쿼리 — 실제 "회사 관점" 기준은 담당자 확인 후 대체 [확인 필요]
DEFAULT_QUERY = "회사 관점에서 중요하고 파급력이 큰 산업/경쟁사 동향 뉴스"


@lru_cache(maxsize=1)
def get_reranker():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(MODEL_NAME, max_length=512)


def rerank_top_candidates(top_n: int = 30, query: str = DEFAULT_QUERY) -> dict:
    """중요도 상위 후보만 리랭커로 재정렬 (비싼 단계는 소수에만 적용)."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, summary, content, importance_signals FROM articles
            WHERE status = 'new'
            ORDER BY importance_score DESC NULLS LAST
            LIMIT %s
            """,
            (top_n,),
        )
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]

    if not rows:
        conn.close()
        return {"reranked": 0}

    model = get_reranker()
    pairs = [(query, f"{r['title']}\n{(r['content'] or r['summary'] or '')[:1000]}") for r in rows]
    scores = model.predict(pairs)

    with conn.cursor() as cur:
        for row, score in zip(rows, scores):
            merged = {**(row["importance_signals"] or {}), "rerank_score": float(score)}
            cur.execute(
                "UPDATE articles SET importance_signals = %s::jsonb WHERE id = %s",
                (json.dumps(merged), row["id"]),
            )

    conn.close()
    return {"reranked": len(rows)}


if __name__ == "__main__":
    print(rerank_top_candidates())
