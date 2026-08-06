import time

import trafilatura

from tech_monitoring.db.connection import get_connection

REQUEST_DELAY_SECONDS = 0.5


def backfill_content(batch_size: int = 200) -> dict:
    """본문이 비어 있는 기사에 대해 trafilatura로 본문을 추출해 채운다 (LLM 추출 폐기, I3 대응)."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, url FROM articles WHERE content IS NULL ORDER BY id LIMIT %s",
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
                cur.execute("UPDATE articles SET content = %s WHERE id = %s", (text, article_id))
                extracted += 1
            else:
                failed += 1

        time.sleep(REQUEST_DELAY_SECONDS)

    conn.close()
    return {"attempted": len(rows), "extracted": extracted, "failed": failed}


if __name__ == "__main__":
    print(backfill_content())
