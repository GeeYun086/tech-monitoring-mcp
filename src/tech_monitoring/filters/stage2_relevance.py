from tech_monitoring.db.connection import get_connection
from tech_monitoring.filters.embeddings import embed_texts

# 튜닝 대상 — 담당자 기준 수신 후 라벨링 세트로 확정 [확인 필요] (Day6 PoC 튜닝)
COSINE_THRESHOLD = 0.35
RRF_K = 60
CANDIDATE_TOP_N = 300


def _build_tsquery(keywords: list[str]) -> str:
    # 키워드 내부 공백은 AND(구문), 키워드 간은 OR
    terms = [" & ".join(kw.split()) for kw in keywords if kw.strip()]
    return " | ".join(f"({t})" for t in terms)


def embed_missing_topics(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, keywords FROM topics WHERE active AND embedding IS NULL")
        rows = cur.fetchall()
    if not rows:
        return 0

    texts = [f"{name}: {' '.join(keywords)}" for _, name, keywords in rows]
    vectors = embed_texts(texts)

    with conn.cursor() as cur:
        for (topic_id, _, _), vector in zip(rows, vectors):
            cur.execute("UPDATE topics SET embedding = %s WHERE id = %s", (vector, topic_id))
    return len(rows)


def embed_missing_articles(conn, batch_size: int = 500) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, summary, content FROM articles
            WHERE status = 'new' AND embedding IS NULL
            LIMIT %s
            """,
            (batch_size,),
        )
        rows = cur.fetchall()
    if not rows:
        return 0

    texts = [f"{title}\n{(content or summary or '')[:2000]}" for _, title, summary, content in rows]
    vectors = embed_texts(texts)

    with conn.cursor() as cur:
        for (article_id, *_), vector in zip(rows, vectors):
            cur.execute("UPDATE articles SET embedding = %s WHERE id = %s", (vector, article_id))
    return len(rows)


def _keyword_candidates(cur, tsquery: str) -> dict[int, int]:
    if not tsquery:
        return {}
    cur.execute(
        """
        SELECT id FROM articles
        WHERE status = 'new' AND ts @@ to_tsquery('simple', %s)
        ORDER BY ts_rank_cd(ts, to_tsquery('simple', %s)) DESC
        LIMIT %s
        """,
        (tsquery, tsquery, CANDIDATE_TOP_N),
    )
    return {row[0]: rank for rank, row in enumerate(cur.fetchall(), start=1)}


def _dense_candidates(cur, topic_embedding) -> dict[int, tuple[int, float]]:
    cur.execute(
        """
        SELECT id, 1 - (embedding <=> %s) AS similarity FROM articles
        WHERE status = 'new' AND embedding IS NOT NULL
        ORDER BY embedding <=> %s ASC
        LIMIT %s
        """,
        (topic_embedding, topic_embedding, CANDIDATE_TOP_N),
    )
    return {row[0]: (rank, row[1]) for rank, row in enumerate(cur.fetchall(), start=1)}


def apply_stage2() -> dict:
    """관련도 필터: 키워드(tsvector) OR 시맨틱(cosine >= τ). relevance_score는 RRF로 산출(설명용)."""
    conn = get_connection()
    embed_missing_topics(conn)

    embedded = 0
    while True:
        n = embed_missing_articles(conn)
        embedded += n
        if n == 0:
            break

    with conn.cursor() as cur:
        cur.execute("SELECT id, keywords, embedding FROM topics WHERE active")
        topics = cur.fetchall()

    fused_scores: dict[int, float] = {}
    matched_methods: dict[int, set[str]] = {}

    with conn.cursor() as cur:
        for _, keywords, topic_embedding in topics:
            keyword_ranks = _keyword_candidates(cur, _build_tsquery(keywords))
            dense_ranks = _dense_candidates(cur, topic_embedding) if topic_embedding is not None else {}

            candidate_ids = set(keyword_ranks) | set(dense_ranks)
            for article_id in candidate_ids:
                score = 0.0
                methods = matched_methods.setdefault(article_id, set())
                if article_id in keyword_ranks:
                    score += 1 / (RRF_K + keyword_ranks[article_id])
                    methods.add("keyword")
                if article_id in dense_ranks:
                    rank, similarity = dense_ranks[article_id]
                    score += 1 / (RRF_K + rank)
                    if similarity >= COSINE_THRESHOLD:
                        methods.add("semantic")
                fused_scores[article_id] = max(fused_scores.get(article_id, 0.0), score)

    passed_ids = [aid for aid, methods in matched_methods.items() if methods]

    with conn.cursor() as cur:
        for article_id in passed_ids:
            methods = matched_methods[article_id]
            matched_by = "hybrid" if len(methods) > 1 else next(iter(methods))
            cur.execute(
                "UPDATE articles SET relevance_score = %s, matched_by = %s WHERE id = %s",
                (fused_scores[article_id], matched_by, article_id),
            )

        cur.execute(
            """
            UPDATE articles
            SET status = 'archived',
                importance_signals = importance_signals || '{"filtered_stage": "stage2", "reason": "no_relevance_match"}'::jsonb
            WHERE status = 'new' AND NOT (id = ANY(%s))
            """,
            (passed_ids,),
        )
        archived = cur.rowcount

    conn.close()
    return {"embedded_articles": embedded, "passed": len(passed_ids), "archived": archived}


if __name__ == "__main__":
    print(apply_stage2())
