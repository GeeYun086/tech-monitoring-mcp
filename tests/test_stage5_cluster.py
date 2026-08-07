from tech_monitoring.filters.stage5_cluster import cluster_articles


def test_similar_vectors_join_same_cluster():
    ids = [1, 2, 3]
    vectors = [
        [1.0, 0.0, 0.0],
        [0.99, 0.01, 0.0],  # 1과 매우 유사
        [0.0, 0.0, 1.0],    # 전혀 다른 방향
    ]
    clusters = cluster_articles(ids, vectors, threshold=0.9)
    assert clusters[1] == clusters[2]
    assert clusters[1] != clusters[3]


def test_empty_input_returns_empty():
    assert cluster_articles([], [], threshold=0.9) == {}
