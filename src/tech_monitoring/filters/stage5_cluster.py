import json
import re

import numpy as np

from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.filters.stage3_impact import CLUSTER_SIZE_SATURATION

# 2026-08-10 실사용 중 발견: 임베딩 유사도만으로는 서로 다른 실적발표(DoorDash·
# Duolingo·Figma 등)가 "같은 이슈"로 잘못 묶였다 — 뉴스레터·다이제스트류 글이
# "X reports Q2 revenue up N% YoY..." 같은 정형화된 문장 구조를 공유해서
# 임베딩이 가까워지기 때문이다. 라벨 없이도 확인 가능한 객관적 신호(제목에
# 같은 고유명사가 실제로 등장하는가)를 추가 게이트로 써서 이런 오탐을 줄인다.
# 영문 대문자 표기 기반이라 한글 제목에는 적용되지 않는다 — 그 경우는 지금처럼
# 코사인 유사도만으로 판단한다(엔티티를 못 뽑는다고 병합을 막지는 않음).
_DISTINCTIVE_TOKEN_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
_HEADLINE_STOPWORDS = {
    "The", "This", "That", "These", "Those", "How", "Why", "What", "New",
    "Show", "Ask", "Introducing", "Sources", "Report", "Reports", "After",
    "With", "From", "Into", "Its", "Are", "Was", "Were", "Will", "Can", "For",
    # 실적발표류 정형 문구에 공통으로 등장하는 약어 — 여러 회사 기사에 다 붙어서
    # 겹쳐도 "같은 사건"이라는 근거가 되지 못한다(실사용 중 DoorDash/Duolingo
    # 오탐에서 "YoY"가 이런 식으로 거짓 겹침을 만들었다).
    "YoY", "CEO", "CFO", "CTO", "COO", "IPO", "AI", "US", "UK", "EU", "API",
    # Stratechery류 다이제스트가 "X Earnings, Y's Z, ..." 식으로 여러 회사를
    # 한 제목에 나열하는데, "Earnings" 자체가 공통 단어라 실사용 중 IBM·
    # Netflix·Meta·Google 실적 기사가 또 이 패턴으로 잘못 묶였다.
    "Earnings",
}


# Title Case 제목(논문 제목 등)은 거의 모든 단어가 대문자로 시작해서, 이
# 규칙이 "Reasoning"·"Learning" 같은 흔한 단어까지 고유명사로 오인한다
# (실사용 중 발견: arXiv 논문 5건이 서로 다른 논문인데 이 때문에 한 클러스터로
# 잘못 묶였다). 대문자 단어 비율이 높으면 이 제목의 신호는 못 믿는 걸로 보고
# 빈 집합을 반환한다 — 그러면 cluster_articles가 이 제목엔 게이트를 안 걸고
# 기존처럼 코사인 유사도만으로 판단한다.
_TITLE_CASE_RATIO_THRESHOLD = 0.5


def _distinctive_tokens(title: str) -> set[str]:
    if not title:
        return set()
    words = title.split()
    if not words:
        return set()
    raw_matches = _DISTINCTIVE_TOKEN_RE.findall(title)
    if len(raw_matches) / len(words) > _TITLE_CASE_RATIO_THRESHOLD:
        return set()
    return {t for t in raw_matches if t not in _HEADLINE_STOPWORDS}


def _cosine_sim_matrix(vectors: np.ndarray) -> np.ndarray:
    norm = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    return norm @ norm.T


def cluster_articles(
    ids: list[int], vectors: list[list[float]], threshold: float, titles: list[str] | None = None
) -> dict[int, str]:
    """임베딩 코사인 유사도 기반 그리디 클러스터링. 동일 이슈를 다룬 기사를 cluster_id로 묶는다.

    titles를 주면 추가 게이트를 건다: 두 제목 모두에서 고유명사가 뽑히는데
    겹치는 게 하나도 없으면(예: "DoorDash..." vs "Duolingo...") 코사인이
    임계값을 넘어도 병합하지 않는다. 한쪽이라도 고유명사를 못 뽑으면(한글
    제목 등) 이 게이트를 적용하지 않고 기존처럼 코사인만으로 판단한다.
    """
    if not ids:
        return {}

    matrix = np.array([v.to_numpy() if hasattr(v, "to_numpy") else v for v in vectors], dtype=np.float32)
    sims = _cosine_sim_matrix(matrix)
    token_sets = [_distinctive_tokens(t) for t in titles] if titles else None
    cluster_of: dict[int, str] = {}
    centroids: list[tuple[str, int]] = []  # (cluster_id, representative index)

    for i, article_id in enumerate(ids):
        best_cluster, best_sim = None, -1.0
        for cluster_id, rep_idx in centroids:
            sim = sims[i, rep_idx]
            if sim < threshold or sim <= best_sim:
                continue
            if token_sets is not None and token_sets[i] and token_sets[rep_idx]:
                if not (token_sets[i] & token_sets[rep_idx]):
                    continue  # 둘 다 고유명사가 있는데 안 겹침 — 다른 사건으로 취급
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
            SELECT id, embedding, title FROM articles
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
    titles = [r[2] for r in rows]
    cluster_of = cluster_articles(ids, vectors, settings.cluster_similarity_threshold, titles)

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
