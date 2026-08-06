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


def test_aggregator_signal_uses_real_points():
    half = settings.hn_points_saturation / 2
    assert _aggregator_signal_score({"hn_points": half}) == 0.5


def test_aggregator_signal_saturates():
    over = settings.hn_points_saturation * 3
    assert _aggregator_signal_score({"hn_points": over}) == 1.0


def test_aggregator_signal_zero_without_points():
    assert _aggregator_signal_score({}) == 0.0


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
