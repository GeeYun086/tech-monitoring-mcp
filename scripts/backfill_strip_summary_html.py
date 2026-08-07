"""기존 적재분 summary의 HTML 태그·엔티티 제거 (일회성 백필, 재실행 안전).

collectors/rss.py가 strip_html()을 안 쓰고 있었던 버그(keyword_api.py에는
이미 있었음) 때문에, Techmeme·hnrss처럼 <description>에 HTML을 실어 보내는
피드의 summary가 그대로 저장돼 있었다. 수집기는 고쳤지만 이미 쌓인 행은
그대로 두면 검색 응답에 계속 지저분하게 노출된다.

최초 버전은 태그 유무(`summary ~ '<[^>]+>'`)로 대상을 골랐는데, "<p>Koray
[&#8230;]" 처럼 태그 없이 엔티티만 남은 행(21건)을 놓쳤다. 엔티티 표기가
`&amp;` `&#8230;` `&#x2014;` 등 다양해 SQL 정규식으로 전부 나열하는 대신,
**모든 행에 strip_html()을 직접 적용해 결과가 달라진 행만** 갱신한다 —
"뭐가 지저분한가"를 미리 정의할 필요가 없어 이런 사각지대가 다시 생기지 않는다.

content가 NULL인 행은 Stage2가 summary를 임베딩 텍스트로 썼으므로,
정제된 텍스트로 다시 임베딩되도록 embedding도 같이 초기화한다.

    ./.venv/Scripts/python.exe scripts/backfill_strip_summary_html.py
"""

from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.text import strip_html


def backfill() -> dict:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, summary, content FROM articles WHERE summary IS NOT NULL")
        rows = cur.fetchall()

        cleaned = 0
        re_embed = 0
        for article_id, summary, content in rows:
            fixed = strip_html(summary)
            if fixed == summary:
                continue

            cur.execute("UPDATE articles SET summary = %s WHERE id = %s", (fixed, article_id))
            cleaned += 1
            if content is None:
                # summary가 임베딩 텍스트로 쓰였을 대상 — 정제된 텍스트로 재임베딩되게 초기화
                cur.execute("UPDATE articles SET embedding = NULL WHERE id = %s", (article_id,))
                re_embed += 1

    conn.close()
    return {"checked": len(rows), "cleaned": cleaned, "queued_for_re_embed": re_embed}


if __name__ == "__main__":
    print(backfill())
