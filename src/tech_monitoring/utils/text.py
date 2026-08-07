import html
import re

# 태그명이 문자(또는 종료 태그의 "/")로 시작해야 매칭 — "<[^>]+>"만 쓰면
# "app<->bridge" 같은 프로즈 속 화살표까지 태그로 오인해 지워버린다(실사용 중 발견).
_TAG_RE = re.compile(r"</?[a-zA-Z!][^>]*>")


def strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or ""))
