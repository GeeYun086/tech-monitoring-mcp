from datetime import datetime, timedelta, timezone

from tech_monitoring.config import settings
from scripts.dashboard_data import (
    classify_region,
    compute_breakdown_pct,
    compute_impact_distribution,
    compute_keyword_cloud,
    compute_source_distribution,
    compute_volume_trend,
    extract_keywords,
    _clean_for_keywords,
)


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


# ---- 2026-08-11 확장: 국내/해외 트렌드 집계 ----


def test_classify_region_uses_registered_domestic_source_list():
    """언어 자동 감지가 아니라 sources 테이블에 실제 등록한 목록 기반이어야 한다."""
    assert classify_region("AI타임스") == "domestic"
    assert classify_region("GeekNews Weekly") == "domestic"
    assert classify_region("TechCrunch") == "global"
    assert classify_region("어쩌다 이름이 비슷한 소스") == "global"  # 등록 안 된 이름은 기본 해외


def _article(source, days_ago, impact_score=0.5, title="", summary="", now=None):
    now = now or datetime(2026, 8, 11, tzinfo=timezone.utc)
    return {
        "source": source,
        "published_at": now - timedelta(days=days_ago),
        "impact_score": impact_score,
        "title": title,
        "summary": summary,
    }


def test_volume_trend_fills_empty_days_with_zero():
    """데이터가 없는 날을 건너뛰면 추이선이 끊기거나 실제보다 활발해 보이는
    착시가 생긴다 — 없는 날도 0으로 채워야 한다."""
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    articles = [_article("TechCrunch", days_ago=0, now=now)]
    trend = compute_volume_trend(articles, days=3, now=now)

    assert len(trend) == 3  # 오늘 포함 3일 전부 존재
    by_date = {row["date"]: row for row in trend}
    assert by_date["2026-08-11"]["global"] == 1
    assert by_date["2026-08-10"]["global"] == 0
    assert by_date["2026-08-09"]["global"] == 0


def test_volume_trend_splits_domestic_and_global():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    articles = [
        _article("AI타임스", days_ago=0, now=now),
        _article("AI타임스", days_ago=0, now=now),
        _article("TechCrunch", days_ago=0, now=now),
    ]
    trend = compute_volume_trend(articles, days=1, now=now)
    assert trend[0]["domestic"] == 2
    assert trend[0]["global"] == 1


def test_source_distribution_sorted_descending_with_region():
    articles = [
        _article("TechCrunch", 0), _article("TechCrunch", 1), _article("AI타임스", 0),
    ]
    dist = compute_source_distribution(articles)
    assert dist[0] == {"source": "TechCrunch", "region": "global", "count": 2}
    assert dist[1] == {"source": "AI타임스", "region": "domestic", "count": 1}


def test_impact_distribution_buckets_by_tenths():
    articles = [
        _article("TechCrunch", 0, impact_score=0.45),
        _article("TechCrunch", 0, impact_score=0.41),
        _article("AI타임스", 0, impact_score=0.72),
    ]
    dist = compute_impact_distribution(articles)
    by_bucket = {row["bucket_start"]: row for row in dist}
    assert by_bucket[0.4]["global"] == 2  # 0.41·0.45 모두 [0.4, 0.5) 구간
    assert by_bucket[0.7]["domestic"] == 1


def test_impact_distribution_covers_full_range_even_when_empty():
    dist = compute_impact_distribution([])
    assert len(dist) == 10  # 0.0~0.9까지 10개 구간, 데이터 없어도 전부 존재
    assert all(row["domestic"] == 0 and row["global"] == 0 for row in dist)


def test_keywords_skip_common_pronouns_and_prepositions():
    """실사용 중 발견(2026-08-11): 클러스터 대표 요약(짧음)만 보던 이전
    버전에서는 안 드러났는데, 전체 기사 본문으로 돌리니 'we'·'they'·
    'their'·'through' 같은 대명사·전치사가 키워드 상위권에 섞여 나왔다."""
    articles = [_article(
        "TechCrunch", 0,
        title="We think their models will change how they work",
        summary="Through this approach, we make it easier across the board",
    )]
    words = {k["word"] for k in compute_keyword_cloud(articles, "global")}
    assert not words & {"we", "We", "they", "their", "through", "across"}


def test_keyword_cloud_filters_by_region():
    articles = [
        _article("TechCrunch", 0, title="OpenAI launches new agent platform"),
        _article("AI타임스", 0, title="삼성전자 AI 반도체 신제품 공개"),
    ]
    global_kw = {k["word"] for k in compute_keyword_cloud(articles, "global")}
    domestic_kw = {k["word"] for k in compute_keyword_cloud(articles, "domestic")}
    assert "OpenAI" in global_kw and "삼성전자" not in global_kw
    assert "삼성전자" in domestic_kw and "OpenAI" not in domestic_kw
