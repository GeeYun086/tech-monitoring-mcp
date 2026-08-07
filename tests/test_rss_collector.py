from tech_monitoring.utils.text import strip_html


def test_strip_html_removes_tags_and_unescapes_entities():
    """hnrss·Techmeme류 description이 그대로 summary에 들어가던 버그의 회귀 테스트.

    collectors/rss.py는 keyword_api.py와 달리 strip_html()을 쓰지 않아
    <p><a href="...">...</a></p> 같은 태그가 그대로 저장됐다(실제 DB에서
    41건 발견, MCP 검색 응답에 그대로 노출됨).
    """
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
