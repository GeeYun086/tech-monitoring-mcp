import random
import time
from urllib.parse import urlparse

import httpx
import trafilatura

from tech_monitoring.db.connection import get_connection

# 2026-08-11: 고정 0.5초 간격은 규칙적인 요청 패턴을 만든다. 범위를 두고
# 무작위로 뽑아 간격을 흐트러뜨린다(rss.py의 SOURCE_DELAY_RANGE_SECONDS와 동일한 취지).
REQUEST_DELAY_RANGE_SECONDS = (0.3, 0.8)
# 2026-08-11 실사용 중 발견: trafilatura.fetch_url()이 내부적으로 쓰는
# 다운로드 타임아웃(설정 파일상 기본 30초)이 이 환경에선 실제로 안 걸렸다
# — use_config()로 기본 설정을 불러와봐도 섹션이 비어 있어(라이브러리
# 버전 문제로 추정) 사실상 타임아웃 없이 요청이 걸렸고, 응답 없는 서버
# 하나 때문에 파이프라인 전체가 18시간 넘게 멈췄다. try/except로 감싸도
# 예외 자체가 안 나서(그냥 계속 대기) 무용지물이었다.
# fetch_url을 안 쓰고, 이미 이 프로젝트 다른 곳(rss.py 등)에서 안정적으로
# 쓰고 있는 httpx로 직접 받아온 뒤 trafilatura.extract()에는 다운로드된
# HTML만 넘긴다 — 네트워크 요청과 본문 추출을 분리해 타임아웃을 확실히 건다.
REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = "Mozilla/5.0 (compatible; tech-monitoring-mcp/0.1; +internal use)"

# Techmeme URL은 "www.techmeme.com/YYMMDD/pXX#aYYMMDDpXX" — 하나의 날짜별
# 리버(river) 페이지를 #fragment로 가리키는 개별 헤드라인 앵커다. trafilatura는
# fragment를 못 보고 페이지 전체에서 "본문 같은 블록"을 하나 골라오는데, 그게
# 그날의 다른 헤드라인 내용을 잘못 가져온다 — 실측 결과 Techmeme 15건 전부가
# 제목과 무관한(대부분 서로 겹치는) 본문으로 오염됐었다("Nikita Bier 퇴사" 기사에
# "Meta Muse Code 출시" 본문이 들어가는 식). Techmeme summary는 해당 헤드라인만
# 담은 한 문단이라 이미 정확하므로, 본문 추출을 아예 시도하지 않고 summary로 대체한다.
#
# openai.com / mckinsey.com: 2026-08-11 실측 — 커스텀 UA든 실제 크롬 UA든 관계없이
# openai.com은 Cloudflare 관리형 챌린지(응답 헤더 cf-mitigated: challenge)로 매번 403,
# mckinsey.com은 Akamai 봇 매니저로 403 또는 응답 자체가 멈춰 타임아웃난다. 둘 다
# UA/헤더로 우회 가능한 수준이 아닌 정식 봇 차단이라 재시도해도 성공 확률이 없다.
# content가 NULL로 남아 매 파이프라인 실행마다 같은 URL을 다시 시도하며 수 분을
# 낭비했으므로(배치 200건 중 다수를 이 두 소스가 차지), Techmeme과 동일하게
# 추출 자체를 스킵하고 RSS summary로 대체한다.
SKIP_EXTRACTION_HOSTS = {
    "www.techmeme.com", "techmeme.com",
    "openai.com", "www.openai.com",
    "www.mckinsey.com", "mckinsey.com",
}


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
            resp = httpx.get(
                url, headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True,
            )
            resp.raise_for_status()
            text = trafilatura.extract(resp.text)
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

        time.sleep(random.uniform(*REQUEST_DELAY_RANGE_SECONDS))

    conn.close()
    return {"attempted": len(rows), "extracted": extracted, "failed": failed, "skipped": skipped}


if __name__ == "__main__":
    print(backfill_content())
