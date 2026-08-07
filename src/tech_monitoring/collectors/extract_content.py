import time
from urllib.parse import urlparse

import trafilatura

from tech_monitoring.db.connection import get_connection

REQUEST_DELAY_SECONDS = 0.5

# Techmeme URL은 "www.techmeme.com/YYMMDD/pXX#aYYMMDDpXX" — 하나의 날짜별
# 리버(river) 페이지를 #fragment로 가리키는 개별 헤드라인 앵커다. trafilatura는
# fragment를 못 보고 페이지 전체에서 "본문 같은 블록"을 하나 골라오는데, 그게
# 그날의 다른 헤드라인 내용을 잘못 가져온다 — 실측 결과 Techmeme 15건 전부가
# 제목과 무관한(대부분 서로 겹치는) 본문으로 오염됐었다("Nikita Bier 퇴사" 기사에
# "Meta Muse Code 출시" 본문이 들어가는 식). Techmeme summary는 해당 헤드라인만
# 담은 한 문단이라 이미 정확하므로, 본문 추출을 아예 시도하지 않고 summary로 대체한다.
SKIP_EXTRACTION_HOSTS = {"www.techmeme.com", "techmeme.com"}


def _should_skip(url: str) -> bool:
    return urlparse(url).hostname in SKIP_EXTRACTION_HOSTS


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
            (batch_size * 2,),  # 스킵 대상이 섞여도 batch_size만큼은 실제로 시도하도록 여유를 둔다
        )
        candidates = cur.fetchall()

    skipped = 0
    rows = []
    for article_id, url in candidates:
        if _should_skip(url):
            skipped += 1
            continue
        rows.append((article_id, url))
        if len(rows) >= batch_size:
            break

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
    return {"attempted": len(rows), "extracted": extracted, "failed": failed, "skipped": skipped}


if __name__ == "__main__":
    print(backfill_content())
