from tech_monitoring.utils.text import strip_html


def test_strips_real_tags_and_unescapes_entities():
    raw = '<p><a href="https://x.com">A</a> &mdash; B&nbsp;C&hellip;</p>'
    assert strip_html(raw) == "A — B\xa0C…"


def test_does_not_mangle_prose_arrows():
    """실사용 중 발견: '<[^>]+>' 정규식은 "app<->bridge" 같은 프로즈 속 화살표까지
    태그로 오인해 지워버렸다. 태그명이 문자로 시작할 때만 매칭해야 한다."""
    assert strip_html("app<->bridge<->worker") == "app<->bridge<->worker"
    assert strip_html("a < b > c") == "a < b > c"


def test_handles_none_and_empty():
    assert strip_html(None) == ""
    assert strip_html("") == ""
