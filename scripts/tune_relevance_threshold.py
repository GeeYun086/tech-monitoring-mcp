"""Stage2 관련도 임계값 τ 민감도 스윕 (PoC).

실제 정밀도·재현율 측정은 라벨링 세트(중요/노이즈 몇십 건)가 있어야 가능하다 [확인 필요].
라벨이 오기 전까지는, τ를 바꿔가며 "몇 건이 통과하는가"의 변화만 관찰해 감을 잡는 용도.
담당자 라벨 수신 후에는 이 스크립트에 정밀도/재현율 계산을 추가해 그리드 튜닝하면 된다.
"""

from tech_monitoring.db.connection import get_connection

THRESHOLDS = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]


def sweep() -> list[dict]:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT embedding FROM topics WHERE active AND embedding IS NOT NULL")
        topic_embeddings = [row[0] for row in cur.fetchall()]

    results = []
    with conn.cursor() as cur:
        for threshold in THRESHOLDS:
            best_sim_exprs = " OR ".join(
                f"(1 - (embedding <=> %s)) >= %s" for _ in topic_embeddings
            )
            params = []
            for emb in topic_embeddings:
                params.extend([emb, threshold])
            cur.execute(
                f"SELECT count(*) FROM articles WHERE embedding IS NOT NULL AND ({best_sim_exprs})",
                params,
            )
            passed = cur.fetchone()[0]
            results.append({"threshold": threshold, "semantic_passed": passed})

    conn.close()
    return results


if __name__ == "__main__":
    for row in sweep():
        print(row)
