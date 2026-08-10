from tech_monitoring.filters.stage5_cluster import _distinctive_tokens, cluster_articles


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


def test_different_companies_with_similar_boilerplate_do_not_merge():
    """실사용 중 발견(2026-08-10): "X reports Q2 revenue up N% YoY..." 같은
    정형화된 문장 구조 때문에 서로 다른 회사 실적발표가 임베딩만으로는
    같은 이슈로 잘못 묶였다. 제목에 겹치는 고유명사가 없으면 코사인이
    임계값을 넘어도 병합하지 않아야 한다."""
    ids = [1, 2]
    vectors = [[1.0, 0.0], [0.99, 0.01]]  # 문장 구조가 비슷해 코사인은 높음
    titles = [
        "DoorDash reports Q2 marketplace gross order value up 36% YoY",
        "Duolingo reports Q2 revenue up 18% YoY, paid subscribers up",
    ]
    clusters = cluster_articles(ids, vectors, threshold=0.9, titles=titles)
    assert clusters[1] != clusters[2]


def test_same_entity_still_merges_even_with_titles_gate():
    """같은 사건을 다른 매체가 보도한 정상 케이스는 여전히 병합돼야 한다."""
    ids = [1, 2]
    vectors = [[1.0, 0.0], [0.99, 0.01]]
    titles = [
        "Changes at Google DeepMind: Demis Hassabis from CEO to Chair",
        "Sources: Demis Hassabis had been drifting away from Google DeepMind CEO role",
    ]
    clusters = cluster_articles(ids, vectors, threshold=0.9, titles=titles)
    assert clusters[1] == clusters[2]


def test_titles_gate_skipped_when_no_extractable_tokens():
    """한글 제목 등 고유명사를 못 뽑는 경우엔 게이트를 걸지 않고 기존처럼
    코사인만으로 판단한다 — 못 뽑는다고 병합을 막으면 안 된다."""
    ids = [1, 2]
    vectors = [[1.0, 0.0], [0.99, 0.01]]
    titles = ["전사 AI 전환 추진 'AX센터' 신설", "AI 전환 위한 조직 신설 발표"]
    clusters = cluster_articles(ids, vectors, threshold=0.9, titles=titles)
    assert clusters[1] == clusters[2]


def test_distinctive_tokens_excludes_common_headline_stopwords():
    # 실제 뉴스 헤드라인처럼 고유명사만 대문자인 문장 케이스(Title Case 아님)
    tokens = _distinctive_tokens("The new OpenAI report shows how Sources are used internally")
    assert "OpenAI" in tokens
    assert "The" not in tokens and "Sources" not in tokens


def test_title_case_titles_are_treated_as_unreliable():
    """실사용 중 발견: arXiv 논문 제목처럼 Title Case로 쓰인 제목은 거의 모든
    단어가 대문자라 "Reasoning"·"Learning" 같은 흔한 단어까지 고유명사로
    오인돼, 서로 다른 논문 5건이 한 클러스터로 잘못 묶였다. 대문자 비율이
    높으면 빈 집합을 반환해 이 제목엔 게이트를 걸지 않아야 한다(대신 기존
    코사인 유사도 판단으로 폴백 — 이 케이스의 정밀한 구분은 여전히 한계로
    남는다는 점은 README에 기록)."""
    tokens = _distinctive_tokens("Efficient and Inspectable Latent Reasoning for LLM Scaling")
    assert tokens == set()
