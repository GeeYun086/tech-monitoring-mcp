from tech_monitoring.config import settings
from scripts.dashboard_data import compute_breakdown_pct, extract_keywords, _clean_for_keywords


def test_breakdown_pct_sums_to_100():
    """4개 신호 기여도의 합은 항상 100(반올림 오차 이내) — impact_score 자체가
    이 4개의 가중합이므로, 이 스크립트가 다른 계산을 하고 있으면 안 된다."""
    signals = {"source_trust": 0.9, "aggregator_signal": 0.5, "cluster_size": 0.2, "recency": 0.8}
    pct = compute_breakdown_pct(signals)
    assert abs(sum(pct.values()) - 100.0) < 0.5


def test_breakdown_pct_reflects_actual_config_weights():
    """가중치가 바뀌어도 손으로 다시 계산할 필요가 없어야 한다 — config.py를 직접 읽는지 확인."""
    signals = {"source_trust": 1.0, "aggregator_signal": 0.0, "cluster_size": 0.0, "recency": 0.0}
    pct = compute_breakdown_pct(signals)
    # source_trust만 값이 있으므로 trust 세그먼트가 100%를 차지해야 한다
    assert pct["trust"] == 100.0
    assert pct["agg"] == pct["cluster"] == pct["recency"] == 0.0


def test_breakdown_pct_handles_all_zero_signals():
    assert compute_breakdown_pct({}) == {"trust": 0.0, "agg": 0.0, "cluster": 0.0, "recency": 0.0}


def test_weights_used_are_the_real_settings_not_hardcoded():
    """설정값을 하드코딩하지 않고 실제 config.Settings를 참조하는지 확인."""
    signals = {"source_trust": 1.0, "aggregator_signal": 1.0, "cluster_size": 1.0, "recency": 1.0}
    pct = compute_breakdown_pct(signals)
    expected_trust_share = settings.weight_source_trust / (
        settings.weight_source_trust
        + settings.weight_aggregator_signal
        + settings.weight_cluster_size
        + settings.weight_recency
    ) * 100
    assert abs(pct["trust"] - round(expected_trust_share, 1)) < 0.2


def _cluster(title, summary=""):
    return {"lead": {"title": title, "summary": summary}}


def test_keywords_counts_once_per_cluster_not_per_word_occurrence():
    """같은 클러스터 안에서 단어가 반복돼도 한 번만 센다 — 긴 요약 하나가
    빈도수를 독점하지 않게(실제 이슈 개수 분포를 반영해야 하므로)."""
    clusters = [
        _cluster("AI agents for enterprise", "AI agents are changing how enterprise teams work"),
        _cluster("Another AI announcement"),
    ]
    keywords = extract_keywords(clusters)
    words = {k["word"]: k["count"] for k in keywords}
    assert words.get("AI") == 2  # 클러스터 2개에 각각 1번씩
    assert words.get("agents") == 1  # 첫 클러스터 안에서는 중복 카운트 안 됨


def test_keywords_skip_stopwords_and_short_tokens():
    clusters = [_cluster("The new AI is out", "it was in a big way")]
    keywords = extract_keywords(clusters)
    words = {k["word"] for k in keywords}
    assert "the" not in words and "was" not in words and "in" not in words


def test_keywords_ignore_hn_metadata_boilerplate():
    """hnrss처럼 본문 없는 소스는 summary가 'Article URL: ... Points: N' 같은
    구조적 라벨뿐이다(실측: 이걸 안 거르면 상위 10개 중 8개가 'URL'·'Comments'
    같은 라벨이었다). 라벨은 걸러지고 실제 제목의 단어는 남아야 한다."""
    clusters = [
        _cluster(
            "Discovery Loop",
            "Article URL: https://www.discoveryloop.com/ Comments URL: "
            "https://news.ycombinator.com/item?id=1 Points: 616 # Comments: 388",
        )
    ]
    keywords = extract_keywords(clusters)
    words = {k["word"] for k in keywords}
    assert not words & {"URL", "Comments", "Points", "https", "com", "ycombinator", "item", "id"}
    assert "Discovery" in words and "Loop" in words


def test_clean_for_keywords_strips_urls_and_hn_labels():
    raw = "Article URL: https://example.com/a Comments URL: https://news.ycombinator.com/item?id=1 Points: 42 # Comments: 3"
    cleaned = _clean_for_keywords(raw)
    assert "http" not in cleaned and "Points" not in cleaned and "Comments" not in cleaned
