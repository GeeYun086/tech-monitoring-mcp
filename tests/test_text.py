from tech_monitoring.utils.text import strip_article_boilerplate, strip_html


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


# ---- strip_article_boilerplate: 2026-08-13 ITWorld 기사에서 실제로 발견한 문제 ----

def test_strip_article_boilerplate_removes_leading_numbered_tag_list():
    """ITWorld 기사 snippet 맨 앞에 그 사이트 고정 카테고리 태그 목록이
    통째로 들어있었다 — 모든 기사에 똑같이 붙어 워드클라우드를 오염시키고
    요약도 이 목록만 잘려 나가 쓸모없게 만들었다."""
    raw = (
        "1. 안드로이드\n2. 애플\n3. 인공지능\n4. 증강 현실\n5. 클라우드 컴퓨팅\n"
        "지난 7월 발생한 오픈AI 보안 사고는 의도된 범위를 벗어나 작동하는 AI 에이전트의 위협을 드러냈다."
    )
    cleaned = strip_article_boilerplate(raw)
    assert "안드로이드" not in cleaned
    assert "클라우드 컴퓨팅" not in cleaned
    assert "오픈AI 보안 사고" in cleaned


def test_strip_article_boilerplate_removes_credit_line():
    raw = "Notschalter Killswitch\n\nCredit: luckyraccoon - shutterstock.com\n\n실제 본문 내용입니다."
    cleaned = strip_article_boilerplate(raw)
    assert "Credit:" not in cleaned
    assert "shutterstock" not in cleaned
    assert "실제 본문 내용입니다" in cleaned


def test_strip_article_boilerplate_removes_markdown_heading_markers():
    cleaned = strip_article_boilerplate("# 제목입니다\n\n## 부제목\n\n본문")
    assert "#" not in cleaned
    assert "제목입니다" in cleaned  # 텍스트 자체는 남기고 기호만 제거


def test_strip_article_boilerplate_handles_none_and_empty():
    assert strip_article_boilerplate(None) == ""
    assert strip_article_boilerplate("") == ""


def test_strip_article_boilerplate_leaves_clean_text_unchanged():
    """번호가 하나뿐이거나(연속 목록 아님) 정상적인 프로즈는 그대로 둬야 한다."""
    text = "OpenAI released a new model. It scored 1. above baseline on the benchmark."
    assert strip_article_boilerplate(text) == text
