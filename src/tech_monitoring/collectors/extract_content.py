import time

import trafilatura

from tech_monitoring.db.connection import get_connection

REQUEST_DELAY_SECONDS = 0.5


def backfill_content(batch_size: int = 200) -> dict:
    """본문이 비어 있는 기사에 대해 trafilatura로 본문을 추출해 채운다 (LLM 추출 폐기, I3 대응).

    Phase 5 튜닝 중 발견: status='new'(현재 필터를 통과해 실제로 쓰이는 기사)가
    아니라 id 오름차순(=오래된 순, 대부분 archived)을 먼저 채우고 있었다.
    archived 기사의 본문은 어디에도 노출되지 않으므로 그 요청은 낭비이고,
    정작 관련도·클러스터링에 본문이 필요한 신규 기사는 배치가 계속 밀려
    "Points: N" 같은 메타데이터만으로 임베딩되는 상태가 오래 지속됐다
    (search_news 응답 검증 중 실측 — 205건 중 192건 content NULL).
    """
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, url FROM articles WHERE content IS NULL
            ORDER BY (status = 'new') DESC, id
            LIMIT %s
            """,
            (batch_size,),
        )
        rows = cur.fetchall()

    extracted = failed = 0
    for article_id, url in rows:
        text = None
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(downloaded)
        except Exception:
            text = None

        with conn.cursor() as cur:
            if text:
                # embedding이 이미 있었다면 요약(메타데이터)만 보고 계산된 값이므로,
                # Stage2가 이번 본문으로 다시 임베딩하도록 초기화한다.
                cur.execute(
                    "UPDATE articles SET content = %s, embedding = NULL WHERE id = %s",
                    (text, article_id),
                )
                extracted += 1
            else:
                failed += 1

        time.sleep(REQUEST_DELAY_SECONDS)

    conn.close()
    return {"attempted": len(rows), "extracted": extracted, "failed": failed}


if __name__ == "__main__":
    print(backfill_content())
