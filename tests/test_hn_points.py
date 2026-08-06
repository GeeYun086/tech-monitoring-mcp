from tech_monitoring.collectors.rss import parse_hn_points

HNRSS_DESCRIPTION = (
    "<p>Article URL: <a href='https://example.com/a'>https://example.com/a</a></p>\n"
    "<p>Comments URL: <a href='https://news.ycombinator.com/item?id=1'>...</a></p>\n"
    "<p>Points: 190</p>\n"
    "<p># Comments: 211</p>"
)


def test_parses_points_from_hnrss_description():
    assert parse_hn_points(HNRSS_DESCRIPTION) == 190


def test_returns_none_for_non_hn_summary():
    assert parse_hn_points("<p>A normal article summary with no score.</p>") is None


def test_returns_none_for_empty():
    assert parse_hn_points(None) is None
