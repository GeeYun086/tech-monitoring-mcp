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


def test_strips_multiple_tags_and_entity_types_in_one_string():
    """v1 hnrss·Techmeme류 description 회귀 케이스(구 tests/test_rss_collector.py에서 이관) —
    태그 여러 종류(<a>·<br/>·<span>)와 엔티티 여러 종류가 한 문자열에 섞여 있어도 전부 제거돼야 한다."""
    raw = (
        '<p><a href="https://example.com">Sean O\'Kane</a> / TechCrunch:<br />\n'
        "<span>Nikita Bier steps down</span>&nbsp;&mdash;&nbsp;"
        "continues as an adviser&hellip;</p>"
    )
    cleaned = strip_html(raw)

    assert "<" not in cleaned and ">" not in cleaned
    assert "&nbsp;" not in cleaned and "&mdash;" not in cleaned and "&hellip;" not in cleaned
    assert "Nikita Bier steps down" in cleaned
    assert "continues as an adviser" in cleaned
