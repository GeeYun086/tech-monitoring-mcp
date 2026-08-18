"""relevance_model.py 테스트.

무거운 학습을 테스트마다 돌리지 않도록, 실제 채점이 필요한 것만 tfidf로
확인한다(임베딩 방식은 모델 로드가 수십 초라 여기서 돌리지 않는다 — 두
방식의 차이는 _make_estimator 하나뿐이고 나머지 평가 경로는 공유한다).

중점은 "숫자가 몇이냐"가 아니라 **근거 없는 숫자를 내지 않는가**다:
라벨이 부족하거나 한쪽 클래스뿐이면 계산을 거부해야 하고, fold는 같은
기사가 학습·검증에 갈리지 않게 나뉘어야 한다.
"""

from datetime import date

import numpy as np

from tech_monitoring import relevance_model as rm


def _label(keyword, title, label, url, week=date(2026, 8, 10), snippet="요약"):
    return {"fixed_keyword": keyword, "title": title, "snippet": snippet,
            "label": label, "url_norm": url, "period_start": week}


def _dataset(n=60, week=date(2026, 8, 10)):
    """절반씩 두 클래스. 어휘를 완전히 갈라놔서 tfidf가 확실히 학습되게 한다."""
    rows = []
    for i in range(n):
        relevant = i % 2 == 0
        rows.append(_label(
            ["교육", "비즈니스 실적"][i % 2],
            ("기업 AI 교육 도입 확대" if relevant else "연예인 결혼 소식 화제") + f" {i}",
            "relevant" if relevant else "irrelevant",
            f"https://a.com/{i}", week,
        ))
    return rows


# --- 입력 구성 -------------------------------------------------------------

def test_build_text_puts_market_keyword_first():
    """관련도는 "기사 × 시장" 쌍의 속성이라 키워드가 입력에 들어가야 한다 —
    빠지면 같은 기사에 붙은 서로 다른 정답이 모순 데이터가 된다."""
    text = rm.build_text(_label("교육", "AI 도입", "relevant", "u1"))

    assert text.startswith("교육")
    assert "AI 도입" in text


def test_build_text_survives_missing_snippet():
    text = rm.build_text(_label("교육", "제목", "relevant", "u1", snippet=None))

    assert "제목" in text


def test_build_xy_maps_labels_to_binary():
    texts, y = rm.build_xy([
        _label("교육", "a", "relevant", "u1"),
        _label("교육", "b", "irrelevant", "u2"),
    ])

    assert len(texts) == 2
    assert list(y) == [1, 0]


# --- fold 그룹 -------------------------------------------------------------

def test_groups_by_article_when_only_one_week():
    """한 주뿐이면 주차로는 나눌 수 없으니, 같은 기사가 학습·검증에 갈리는
    누수만 막는다(실측상 여러 키워드에 같은 URL이 걸린다)."""
    labels = [_label("교육", "a", "relevant", "https://a.com/1"),
              _label("비즈니스 실적", "a", "irrelevant", "https://a.com/1")]

    groups, kind = rm.build_groups(labels)

    assert kind == "기사"
    assert list(groups) == ["https://a.com/1", "https://a.com/1"]  # 같은 fold로 묶임


def test_groups_by_week_once_two_weeks_exist():
    """주차가 쌓이면 "학습에 안 쓴 주"로 채점하는 더 엄격한 방식으로 자동 전환된다."""
    labels = [_label("교육", "a", "relevant", "u1", date(2026, 8, 10)),
              _label("교육", "b", "irrelevant", "u2", date(2026, 8, 17))]

    _groups, kind = rm.build_groups(labels)

    assert kind == "주차"


# --- 근거 없는 숫자를 내지 않는가 -------------------------------------------

def test_refuses_to_score_when_too_few_labels():
    result = rm.evaluate(_dataset(n=10), method="tfidf")

    assert result["ok"] is False
    assert "최소" in result["reason"]
    assert "metrics" not in result


def test_refuses_to_score_when_only_one_class_present():
    """전부 "도움됨"이면 배울 게 없다 — 이때 정확도 100%를 띄우면 완전한 착시다."""
    labels = [_label("교육", f"제목 {i}", "relevant", f"u{i}") for i in range(40)]

    result = rm.evaluate(labels, method="tfidf")

    assert result["ok"] is False
    assert "전부" in result["reason"]


def test_refuses_to_score_when_only_one_group():
    """같은 기사만 40건이면 나눌 그룹이 하나뿐이라 검증이 성립하지 않는다."""
    labels = [_label("교육", f"제목 {i}", "relevant" if i % 2 else "irrelevant", "https://a.com/same")
              for i in range(40)]

    result = rm.evaluate(labels, method="tfidf")

    assert result["ok"] is False
    assert "하나뿐" in result["reason"]


def test_distribution_is_reported_even_when_scoring_is_refused():
    """분포는 라벨링 중에도 봐야 하는 값이라, 채점을 거부해도 함께 돌려준다."""
    result = rm.evaluate(_dataset(n=10), method="tfidf")

    assert result["distribution"]["total"] == 10


# --- 채점 -----------------------------------------------------------------

def test_evaluate_returns_full_metric_set():
    result = rm.evaluate(_dataset(), method="tfidf")

    assert result["ok"] is True
    for key in ("precision", "recall", "f1", "accuracy", "auc", "precision_at_10", "ndcg_at_10"):
        assert 0.0 <= result["metrics"][key] <= 1.0
    assert result["cv"]["group_kind"] == "기사"


def test_majority_accuracy_exposes_skewed_labels():
    """핵심 — 90:10으로 쏠리면 "무조건 도움됨"인 분류기도 0.9다. 그 기준선을
    같이 내야 분류기 정확도를 제대로 읽을 수 있다."""
    labels = ([_label("교육", f"관련 {i}", "relevant", f"r{i}") for i in range(90)]
              + [_label("교육", f"무관 {i}", "irrelevant", f"n{i}") for i in range(10)])

    distribution = rm.class_distribution(labels)

    assert distribution["majority_accuracy"] == 0.9
    assert distribution["positive_rate"] == 0.9


def test_evaluate_all_ranks_winner_first():
    results = rm.evaluate_all(_dataset(), k=5)

    assert [r["method"] for r in results] and set(r["method"] for r in results) == set(rm.METHODS)
    scores = [r.get("metrics", {}).get("f1", -1) for r in results]
    assert scores == sorted(scores, reverse=True)


# --- 순위 지표 -------------------------------------------------------------

def test_precision_at_k_counts_hits_in_top_k():
    assert rm.precision_at_k(np.array([1, 1, 0, 1]), k=2) == 1.0
    assert rm.precision_at_k(np.array([0, 0, 1, 1]), k=2) == 0.0


def test_ndcg_rewards_putting_relevant_items_higher():
    good = rm.ndcg_at_k(np.array([1, 1, 0, 0]), k=4)
    bad = rm.ndcg_at_k(np.array([0, 0, 1, 1]), k=4)

    assert good == 1.0
    assert bad < good


def test_ranking_metrics_are_computed_per_market():
    """시장이 다른 기사끼리 한 줄로 세우면 실사용과 안 맞는다 — 시장별로
    순위를 매기므로, 각 시장 안에서 정답이 위에 오면 만점이어야 한다."""
    labels = [_label("교육", "a", "relevant", "u1"), _label("교육", "b", "irrelevant", "u2"),
              _label("비즈니스 실적", "c", "relevant", "u3"), _label("비즈니스 실적", "d", "irrelevant", "u4")]
    y = np.array([1, 0, 1, 0])
    scores = np.array([0.9, 0.1, 0.9, 0.1])   # 두 시장 모두 정답이 1위

    result = rm.ranking_metrics(labels, y, scores, k=2)

    assert result["ndcg_at_2"] == 1.0


# --- 저장/불러오기 ---------------------------------------------------------

def test_trained_model_saves_loads_and_predicts(tmp_path):
    labels = _dataset()
    estimator = rm.train_final_model(labels, "tfidf")
    path = rm.save_model(estimator, "tfidf", {"f1": 1.0}, path=tmp_path / "m.joblib")

    bundle = rm.load_model(path)
    assert bundle["method"] == "tfidf"

    probabilities = rm.predict_proba(bundle, [
        _label("교육", "기업 AI 교육 도입 확대", "relevant", "u1"),
        _label("교육", "연예인 결혼 소식 화제", "irrelevant", "u2"),
    ])
    # 학습한 어휘 방향대로 확률이 갈려야 한다.
    assert probabilities[0] > probabilities[1]


def test_load_model_returns_none_when_not_trained_yet(tmp_path):
    """아직 학습한 적 없는 상태를 호출부가 구분할 수 있어야 한다
    (파이프라인이 모델 없이 조용히 오작동하면 안 된다)."""
    assert rm.load_model(tmp_path / "없는파일.joblib") is None
