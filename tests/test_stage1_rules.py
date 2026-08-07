from tech_monitoring.filters.stage1_rules import _fails_stage1


def test_empty_title_fails():
    assert _fails_stage1("", "summary text here", None, "https://example.com/a") == "empty_title"


def test_blocked_extension_fails():
    reason = _fails_stage1("Some Title", "a reasonably long summary text", None, "https://example.com/report.pdf?x=1")
    assert reason == "blocked_extension"


def test_too_short_fails():
    assert _fails_stage1("Hi", None, None, "https://example.com/a") == "too_short"


def test_valid_article_passes():
    summary = "A" * 60
    assert _fails_stage1("A real title", summary, None, "https://example.com/article") is None
