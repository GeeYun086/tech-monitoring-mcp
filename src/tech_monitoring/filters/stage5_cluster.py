import json

import numpy as np

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.filters.stage3_impact import CLUSTER_SIZE_SATURATION


def _cosine_sim_matrix(vectors: np.ndarray) -> np.ndarray:
    norm = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    return norm @ norm.T


def cluster_articles(ids: list[int], vectors: list[list[float]], threshold: float) -> dict[int, str]:
    """임베딩 코사인 유사도 기반 그리디 클러스터링. 동일 이슈를 다룬 기사를 cluster_id로 묶는다."""
    if not ids:
        return {}

    matrix = np.array([v.to_numpy() if hasattr(v, "to_numpy") else v for v in vectors], dtype=np.float32)
    sims = _cosine_sim_matrix(matrix)
    cluster_of: dict[int, str] = {}
    centroids: list[tuple[str, int]] = []  # (cluster_id, representative index)

    for i, article_id in enumerate(ids):
        best_cluster, best_sim = None, -1.0
        for cluster_id, rep_idx in centroids:
            sim = sims[i, rep_idx]
            if sim >= threshold and sim > best_sim:
                best_cluster, best_sim = cluster_id, sim
        if best_cluster is None:
            best_cluster = f"cluster-{len(centroids) + 1}"
            centroids.append((best_cluster, i))
        cluster_of[article_id] = best_cluster

    return cluster_of


def apply_stage5() -> dict:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, embedding FROM articles
            WHERE status = 'new' AND embedding IS NOT NULL AND cluster_id IS NULL
            ORDER BY published_at NULLS LAST
            """
        )
        rows = cur.fetchall()

    if not rows:
        conn.close()
        return {"clustered_articles": 0, "clusters": 0}

    ids = [r[0] for r in rows]
    vectors = [r[1] for r in rows]
    cluster_of = cluster_articles(ids, vectors, settings.cluster_similarity_threshold)

    cluster_sizes: dict[str, int] = {}
    for cluster_id in cluster_of.values():
        cluster_sizes[cluster_id] = cluster_sizes.get(cluster_id, 0) + 1

    with conn.cursor() as cur:
        for article_id, cluster_id in cluster_of.items():
            size = cluster_sizes[cluster_id]
            cur.execute(
                """
                UPDATE articles
                SET cluster_id = %s,
                    impact_signals = impact_signals || %s::jsonb
                WHERE id = %s
                """,
                (
                    cluster_id,
                    json.dumps({
                        "cluster_member_count": size,
                        "cluster_size": min(size / CLUSTER_SIZE_SATURATION, 1.0),
                    }),
                    article_id,
                ),
            )

    conn.close()
    return {"clustered_articles": len(cluster_of), "clusters": len(cluster_sizes)}


if __name__ == "__main__":
    print(apply_stage5())
