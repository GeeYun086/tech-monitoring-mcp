from datetime import datetime, timedelta, timezone

from tech_monitoring.config import settings
from scripts.dashboard_data import (
    classify_region,
    compute_breakdown_pct,
    compute_co_report_intensity,
    compute_cross_region_lag,
    compute_entity_ranking,
    compute_impact_distribution,
    compute_keyword_anomaly,
    compute_keyword_bubbles,
    compute_keyword_cloud,
    compute_keyword_gap,
    compute_keyword_lifecycle,
    compute_keyword_network,
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


def _article(source, days_ago, impact_score=0.5, title="", summary="", now=None, cluster_id=None):
    now = now or datetime(2026, 8, 11, tzinfo=timezone.utc)
    return {
        "source": source,
        "published_at": now - timedelta(days=days_ago),
        "impact_score": impact_score,
        "title": title,
        "summary": summary,
        "cluster_id": cluster_id,
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


# ---- 2026-08-11 2차 확장: 담당자 피드백 — "수집 방법 지표"가 아니라
# "산업 동향 지표"가 필요하다. 새 백엔드(감성분석·NER)가 필요한 지표는
# 미루고, 지금 데이터로 계산 가능한 7개를 추가.


def test_keyword_network_links_words_that_co_occur_in_same_article():
    articles = [
        _article("TechCrunch", 0, title="OpenAI agent platform launch"),
        _article("TechCrunch", 0, title="OpenAI agent platform update"),
        _article("The Verge", 0, title="Totally unrelated gadget review"),
    ]
    network = compute_keyword_network(articles, top_nodes=10, min_weight=2)
    edge_pairs = {(e["source"], e["target"]) for e in network["edges"]}
    assert ("OpenAI", "agent") in edge_pairs or ("agent", "OpenAI") in edge_pairs


def test_keyword_network_drops_edges_below_min_weight():
    articles = [
        _article("TechCrunch", 0, title="OpenAI agent launch"),
        _article("The Verge", 0, title="Totally different gadget story"),
    ]
    network = compute_keyword_network(articles, top_nodes=10, min_weight=2)
    assert network["edges"] == []  # 동시출현이 1번뿐이면 노이즈로 제외


# ---- 2026-08-11 3차 확장: 담당자 재지적 — "결과가 너무 일반적이다".
# 원인 진단: 단어 하나(unigram) 단위 토큰화라 "AI"·"모델" 같은 최상위
# 개념어가 항상 지배. 새 NLP 모델 없이 구(phrase) 후보 + TF-IDF로 개선.


def test_keyword_cloud_surfaces_bigram_phrases():
    """실사용 중 발견: 단어 하나 단위로만 세면 "on-device"·"AI"가 따로따로
    잡혀서 "on-device AI"라는 구체적인 표현 자체가 후보가 못 됐다."""
    articles = [_article("TechCrunch", 0, title="on-device AI adoption grows")] * 3
    words = {k["word"] for k in compute_keyword_cloud(articles, "global")}
    assert "on-device AI" in words


def test_keyword_cloud_domestic_stays_unigram_raw_frequency():
    """실사용 중 발견: 한국어는 형태소 분석이 없어 "기술을"·"모델을"처럼
    조사가 붙은 채로 별도 토큰이 된다. 구+TF-IDF를 국내에도 적용하니
    이런 조사 결합형이 TF-IDF의 중간 빈도 우대 특성과 만나 대거 상위권을
    차지해 버렸다(실측) — 그래서 국내는 의도적으로 기존 유니그램+원시
    빈도 방식을 유지한다. 바이그램이 섞이면 안 된다."""
    articles = [_article("AI타임스", 0, title="기술 발전이 빠르게 진행된다")] * 3
    words = {k["word"] for k in compute_keyword_cloud(articles, "domestic")}
    assert "기술 발전" not in words  # 바이그램은 국내엔 적용 안 됨


def test_keyword_cloud_downweights_ubiquitous_terms_via_tfidf():
    """실사용 중 발견: 처음 짠 TF-IDF 공식(count×idf)은 문서당 1회만 세는
    구조상 tf==df가 되어, count=84인 "ubiquitous"가 count=13인 "specific"
    보다 여전히 압도적으로 높은 점수를 받았다(로그 감쇠 전: 104 vs 40).
    sublinear TF(1+ln(count))를 적용해야 중간 빈도의 구체적인 단어가
    최상위 개념어보다 위로 올라온다."""
    articles = (
        [_article("TechCrunch", i, title="ubiquitous filler word appears") for i in range(20)]
        + [_article("TechCrunch", i, title="specific niche phrase repeats") for i in range(6)]
    )
    ranked = compute_keyword_cloud(articles, "global", top_n=50)
    rank_of = {r["word"]: i for i, r in enumerate(ranked)}
    assert rank_of["specific"] < rank_of["ubiquitous"]


def test_keyword_anomaly_uses_share_not_raw_count():
    """실사용 중 발견: 8월 초 새 소스 추가로 주간 수집량이 6~8배 급증했다 —
    원시 빈도로 비교하면 이 수집량 변화 자체 때문에 비중이 그대로인
    단어까지 "급상승"으로 잘못 잡힌다. 비중(share, %)이 똑같으면(20%→20%)
    급상승이 아니어야 한다."""
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    # 이번 기간: 100건 중 20건(20%) / 베이스라인: 10건 중 2건(20%, 동일 비중)
    current = [_article("TechCrunch", 0, now=now, title="specific topic here")] * 20 + \
              [_article("TechCrunch", 0, now=now, title="unrelated filler content")] * 80
    baseline = [_article("TechCrunch", 7, now=now, title="specific topic here")] * 2 + \
               [_article("TechCrunch", 7, now=now, title="unrelated filler content")] * 8
    rows = compute_keyword_anomaly(current, [baseline])
    assert "specific" not in {r["word"] for r in rows}


def test_keyword_anomaly_flags_genuine_share_increase():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    current = [_article("TechCrunch", 0, now=now, title="novelburst topic here")] * 50 + \
              [_article("TechCrunch", 0, now=now, title="filler content only")] * 50
    baseline = [_article("TechCrunch", 7, now=now, title="filler content only")] * 10
    rows = compute_keyword_anomaly(current, [baseline])
    row = next(r for r in rows if r["word"] == "novelburst")
    assert row["is_new"] is True


def test_keyword_anomaly_excludes_flat_or_declining_share():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    current = [_article("TechCrunch", 0, now=now, title="steady term")] * 3 + \
              [_article("TechCrunch", 0, now=now, title="filler")] * 7
    baseline = [_article("TechCrunch", 7, now=now, title="steady term")] * 5 + \
               [_article("TechCrunch", 7, now=now, title="filler")] * 5
    rows = compute_keyword_anomaly(current, [baseline])
    assert "steady" not in {r["word"] for r in rows}


def test_keyword_lifecycle_fills_empty_days_and_tracks_daily_counts():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    articles = [
        _article("TechCrunch", days_ago=0, now=now, title="specific recurring phrase"),
        _article("TechCrunch", days_ago=1, now=now, title="specific recurring phrase"),
    ]
    result = compute_keyword_lifecycle(articles, days=3, top_n_terms=5, now=now)
    assert len(result["series"]) == 3  # 오늘 포함 3일 전부 존재(데이터 없는 날도 0)
    by_date = {row["date"]: row for row in result["series"]}
    assert "specific" in result["terms"]
    assert by_date["2026-08-11"]["specific"] == 1
    assert by_date["2026-08-09"]["specific"] == 0  # 데이터 없는 날은 0으로 채움


def test_keyword_bubbles_marks_brand_new_words_and_counts_sources():
    current = [
        _article("TechCrunch", 0, title="novelword appears here"),
        _article("The Verge", 0, title="novelword shows up too"),
    ]
    previous = []
    bubbles = compute_keyword_bubbles(current, previous)
    row = next(b for b in bubbles if b["word"] == "novelword")
    assert row["is_new"] is True
    assert row["source_count"] == 2  # TechCrunch·The Verge 두 소스에서 나옴


def test_keyword_gap_finds_words_unique_to_one_region():
    articles = [_article("TechCrunch", 0, title="quantum computing breakthrough")] * 5 + \
               [_article("AI타임스", 0, title="양자컴퓨팅 관련 없는 국내 기사")] * 5
    gap = compute_keyword_gap(articles)
    global_words = {r["word"] for r in gap["global_only"]}
    assert "quantum" in global_words


def test_keyword_gap_ignores_korean_english_translation_pairs():
    """실사용 중 발견: 영문 제한이 없으면 "모델"(국내만)과 "model"(해외만)이
    같은 개념인데 서로 다른 문자열이라 "격차"로 잘못 잡혔다 — 이건 화제
    격차가 아니라 언어 차이일 뿐이다. 한글 토큰은 애초에 후보에서 빠져야 한다."""
    articles = [_article("TechCrunch", 0, title="the model performs well")] * 5 + \
               [_article("AI타임스", 0, title="모델 성능이 우수하다")] * 5
    gap = compute_keyword_gap(articles)
    domestic_words = {r["word"] for r in gap["domestic_only"]}
    assert "모델" not in domestic_words


def test_entity_ranking_reuses_distinctive_tokens_and_skips_korean_titles():
    articles = [
        _article("TechCrunch", 0, title="OpenAI announces new agent tools"),
        _article("TechCrunch", 0, title="OpenAI expands agent tools further"),
        _article("AI타임스", 0, title="삼성전자 AI 반도체 신제품 공개"),
    ]
    ranking = compute_entity_ranking(articles)
    entities = {r["entity"]: r["count"] for r in ranking}
    assert entities.get("OpenAI") == 2
    assert "삼성전자" not in entities  # 알려진 한계 — 영문 대문자 표기 기반이라 한글엔 미적용


def test_cross_region_lag_only_counts_domestic_following_global():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    articles = [
        _article("TechCrunch", days_ago=3, now=now, cluster_id="cluster-1"),  # 해외가 먼저
        _article("AI타임스", days_ago=1, now=now, cluster_id="cluster-1"),     # 국내가 2일 뒤 후속
        _article("OpenAI", days_ago=2, now=now, cluster_id="cluster-2"),      # 해외 단독(짝 없음)
    ]
    result = compute_cross_region_lag(articles)
    assert result["count"] == 1
    assert result["pairs"][0]["lag_hours"] == 48.0


def test_cross_region_lag_excludes_domestic_first_cases():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    articles = [
        _article("AI타임스", days_ago=3, now=now, cluster_id="cluster-1"),   # 국내가 먼저
        _article("TechCrunch", days_ago=1, now=now, cluster_id="cluster-1"),  # 해외가 뒤늦게 후속
    ]
    result = compute_cross_region_lag(articles)
    assert result["count"] == 0  # "추격 시차"의 정의(해외→국내) 밖


def test_co_report_intensity_requires_at_least_three_members():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    articles = (
        [_article("TechCrunch", days_ago=1, now=now, cluster_id="big")] * 3
        + [_article("The Verge", days_ago=2, now=now, cluster_id="small")] * 2
    )
    result = compute_co_report_intensity(articles, days=3, now=now)
    total = sum(r["big_cluster_count"] for r in result)
    assert total == 1  # "big" 클러스터가 걸린 날만 1로 잡히고 "small"은 안 잡힘


def test_co_report_intensity_fills_empty_days_with_zero():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    result = compute_co_report_intensity([], days=3, now=now)
    assert len(result) == 3
    assert all(r["big_cluster_count"] == 0 for r in result)
