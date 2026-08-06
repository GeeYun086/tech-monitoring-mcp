from datetime import datetime, timedelta, timezone

from tech_monitoring.filters.stage3_importance import (
    _issue_type_score,
    _recency_score,
    _sentiment_score,
)


def test_issue_type_detects_acquisition():
    assert _issue_type_score("Company A announces acquisition of Company B") == 1.0


def test_issue_type_zero_for_plain_text():
    assert _issue_type_score("A quiet day in the market") == 0.0


def test_sentiment_detects_negative_tone():
    assert _sentiment_score("Startup hit with a major lawsuit and scandal") == 1.0


def test_recency_full_score_for_now():
    now = datetime.now(timezone.utc)
    assert _recency_score(now, now) == 1.0


def test_recency_decays_over_time():
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=72)
    assert 0.4 < _recency_score(old, now) < 0.6


def test_recency_neutral_when_missing():
    now = datetime.now(timezone.utc)
    assert _recency_score(None, now) == 0.5
