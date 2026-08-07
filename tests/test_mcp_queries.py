from datetime import datetime, timezone

import pytest

from tech_monitoring.mcp_server.queries import (
    build_clusters,
    parse_since,
    resolve_period,
    rrf_fuse,
    to_article,
)

# 2026-08-07은 금요일
NOW = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


def test_parse_since_relative_units():
    assert parse_since("24h", NOW) == datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    assert parse_since("7d", NOW) == datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    assert parse_since("2w", NOW) == datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def test_parse_since_absolute_date_is_utc():
    assert parse_since("2026-08-01", NOW) == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_parse_since_empty_means_no_limit():
    assert parse_since(None, NOW) is None
    assert parse_since("  ", NOW) is None


def test_parse_since_rejects_garbage():
    with pytest.raises(ValueError):
        parse_since("지난달쯤", NOW)


def test_last_week_is_previous_monday_to_this_monday():
    """스케줄 센싱이 '전주'를 보므로 이번 주 진행분이 섞이면 안 된다."""
    start, end, label = resolve_period("last_week", NOW)
    assert start == datetime(2026, 7, 27, tzinfo=timezone.utc)  # 전주 월요일
    assert end == datetime(2026, 8, 3, tzinfo=timezone.utc)  # 이번 주 월요일
    assert label == "last_week"


def test_explicit_range_period():
    start, end, _ = resolve_period("2026-08-01..2026-08-07", NOW)
    assert start == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 7, tzinfo=timezone.utc)


def test_unknown_period_raises():
    with pytest.raises(ValueError):
        resolve_period("작년", NOW)


def test_rrf_marks_hybrid_match():
    fused = rrf_fuse(keyword_ranks={1: 1, 2: 5}, dense_ranks={2: 3, 3: 1})
    assert fused[1][1] == "keyword"
    assert fused[2][1] == "hybrid"
    assert fused[3][1] == "semantic"
    # 양쪽에 모두 잡힌 기사가 한쪽에만 잡힌 기사보다 높게 융합된다
    assert fused[2][0] > fused[1][0]


def test_article_exposes_impact_signals_without_internal_keys():
    row = {
        "id": 7,
        "title": "OpenAI, 기업용 에이전트 플랫폼 공개",
        "url": "https://example.com/a",
        "source": "Techmeme",
        "source_type": "aggregator",
        "published_at": datetime(2026, 8, 5, tzinfo=timezone.utc),
        "summary": "본문 요약",
        "impact_score": 0.123456,
        "relevance_score": 0.5,
        "cluster_id": "cluster-1",
        "impact_signals": {
            "source_trust": 0.9,
            "recency": 0.812345,
            "filtered_stage": "stage2",  # 내부 디버깅용 값은 노출하지 않는다
        },
    }
    article = to_article(row, matched_by="hybrid")

    assert article["impact_score"] == 0.1235
    assert article["published_at"] == "2026-08-05T00:00:00+00:00"
    assert article["matched_by"] == "hybrid"
    assert set(article["impact_signals"]) == {"source_trust", "recency"}


def test_summary_is_clipped():
    row = {
        "id": 1,
        "title": "t",
        "url": "u",
        "source": "s",
        "source_type": "rss",
        "summary": "가" * 1000,
        "impact_signals": {},
    }
    assert len(to_article(row)["summary"]) <= 401  # 말줄임표 1자 포함


def _row(article_id: int, cluster_id: str | None, impact: float) -> dict:
    return {
        "id": article_id,
        "title": f"기사 {article_id}",
        "url": f"https://example.com/{article_id}",
        "source": "Techmeme",
        "source_type": "aggregator",
        "impact_score": impact,
        "cluster_id": cluster_id,
        "impact_signals": {},
    }


def test_clusters_group_same_issue_and_keep_highest_as_lead():
    rows = [  # 파급력 내림차순으로 들어온다고 가정
        _row(1, "cluster-1", 0.9),
        _row(2, "cluster-1", 0.7),
        _row(3, "cluster-2", 0.5),
    ]
    clusters = build_clusters(rows)

    assert [c["cluster_id"] for c in clusters] == ["cluster-1", "cluster-2"]
    assert clusters[0]["size"] == 2
    assert clusters[0]["lead"]["id"] == 1
    assert [r["title"] for r in clusters[0]["related"]] == ["기사 2"]


def test_unclustered_article_becomes_single_issue():
    clusters = build_clusters([_row(9, None, 0.4)])
    assert clusters[0]["cluster_id"] == "single-9"
    assert clusters[0]["size"] == 1
