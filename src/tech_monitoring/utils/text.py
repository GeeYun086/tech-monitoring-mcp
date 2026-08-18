import html
import re

# 태그명이 문자(또는 종료 태그의 "/")로 시작해야 매칭 — "<[^>]+>"만 쓰면
# "app<->bridge" 같은 프로즈 속 화살표까지 태그로 오인해 지워버린다(실사용 중 발견).
_TAG_RE = re.compile(r"</?[a-zA-Z!][^>]*>")


def strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or ""))


# 2026-08-13 실사용 확인 — ITWorld 기사의 search_results.snippet 맨 앞에
# 그 사이트 고정 카테고리 태그 목록("1. 안드로이드\n2. 애플\n...")이 통째로
# 들어있었다. 이걸 안 걸러내면 (1) 워드클라우드가 실제 기사 내용이 아니라
# 모든 기사에 똑같이 붙는 이 태그로 도배되고, (2) 화면 요약도 이 태그
# 목록이 잘려 나가 쓸모없어진다.
_LEADING_NUMBERED_LIST_RE = re.compile(r"(?:^\s*\d{1,3}\.\s+\S.*(?:\n|$)){2,}", re.MULTILINE)
_CREDIT_LINE_RE = re.compile(r"^\s*Credit:.*$", re.MULTILINE)
_MARKDOWN_HEADING_MARKER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def strip_article_boilerplate(text: str) -> str:
    """검색결과 snippet(사이트 원문을 그대로 긁어온 것)에 흔한 비-본문
    요소를 제거한다. trafilatura급 본문 추출은 아니고, 실사용 중 확인된
    패턴만 제거한다:
    (1) 2줄 이상 연속된 번호 매김 목록(사이트 고정 카테고리·태그 목록),
    (2) 이미지 출처 표기("Credit: ..."),
    (3) 마크다운 제목 기호(#) — 제목은 이미 title 컬럼에 따로 있어 중복.
    """
    if not text:
        return text or ""
    text = _LEADING_NUMBERED_LIST_RE.sub("", text)
    text = _CREDIT_LINE_RE.sub("", text)
    text = _MARKDOWN_HEADING_MARKER_RE.sub("", text)
    return text
