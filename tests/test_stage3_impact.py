from datetime import datetime, timedelta, timezone

from tech_monitoring.config import settings
from tech_monitoring.filters.stage3_impact import (
    _aggregator_signal_score,
    _recency_score,
    compute_impact,
)


def test_recency_full_score_for_now():
    now = datetime.now(timezone.utc)
    assert _recency_score(now, now) == 1.0


def test_recency_decays_over_time():
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=settings.recency_half_life_hours)
    assert 0.4 < _recency_score(old, now) < 0.6


def test_recency_neutral_when_missing():
    now = datetime.now(timezone.utc)
    assert _recency_score(None, now) == 0.5


def test_aggregator_signal_uses_velocity_not_raw_points():
    now = datetime.now(timezone.utc)
    published = now - timedelta(hours=8)  # age=8h, offset=2h → 분모 10h
    half = settings.aggregator_velocity_saturation / 2 * 10  # 시간당 half가 되는 포인트 수
    assert abs(_aggregator_signal_score({"hn_points": half}, published, now) - 0.5) < 1e-9


def test_aggregator_signal_saturates_at_high_velocity():
    now = datetime.now(timezone.utc)
    published = now - timedelta(hours=1)
    huge_points = settings.aggregator_velocity_saturation * 100
    assert _aggregator_signal_score({"hn_points": huge_points}, published, now) == 1.0


def test_aggregator_signal_zero_without_points():
    now = datetime.now(timezone.utc)
    assert _aggregator_signal_score({}, now, now) == 0.0


def test_aggregator_signal_falls_back_to_raw_score_without_published_at():
    """발행일을 몰라 나이를 계산할 수 없으면 속도 계산이 불가능하므로 원점수로 폴백."""
    now = datetime.now(timezone.utc)
    half = settings.hn_points_saturation / 2
    assert _aggregator_signal_score({"hn_points": half}, None, now) == 0.5


def test_fresh_article_no_longer_structurally_loses_to_old_accumulated_one():
    """실사용 중 발견한 편향의 회귀 테스트: 30분 전 15점(막 뜨는 중)짜리가
    72시간 전 400점(이미 다 모인)짜리에게 aggregator_signal에서 항상 밀리면
    안 된다 — 반응이 쌓일 시간 자체가 없었을 뿐 덜 중요하다는 뜻이 아니다."""
    now = datetime.now(timezone.utc)
    fresh = _aggregator_signal_score({"hn_points": 15}, now - timedelta(minutes=30), now)
    old = _aggregator_signal_score({"hn_points": 400}, now - timedelta(hours=72), now)
    assert fresh > old


def test_impact_has_no_sentiment_or_issue_type_signals():
    """v2.0에서 감성·이슈유형 신호는 제거됨 (중요도는 사용자 판단 영역)."""
    now = datetime.now(timezone.utc)
    row = {"source_trust": 0.9, "published_at": now, "impact_signals": {}}
    _, signals = compute_impact(row, now)
    assert set(signals) == {"source_trust", "aggregator_signal", "cluster_size", "recency"}


def test_impact_score_is_weighted_sum():
    now = datetime.now(timezone.utc)
    row = {"source_trust": 1.0, "published_at": now, "impact_signals": {"hn_points": 10_000, "cluster_size": 1.0}}
    score, _ = compute_impact(row, now)
    expected = (
        settings.weight_source_trust
        + settings.weight_aggregator_signal
        + settings.weight_cluster_size
        + settings.weight_recency
    )
    assert score == expected
