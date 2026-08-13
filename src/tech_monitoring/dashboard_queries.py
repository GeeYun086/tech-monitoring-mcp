"""Streamlit 대시보드(app/streamlit_app.py)가 쓰는 DB 조회 계층.

원칙(v1 ax-dashboard 스킬에서 이어받음 — .claude/skills/ax-dashboard/SKILL.md,
현재는 삭제됨): **계산은 여기서 끝내고, UI는 그대로 보여주기만 한다.** 랭킹·
필터링 로직을 화면 코드에 흩어두면 재사용도 안 되고 검증도 어렵다 — 이 모듈은
Streamlit에 의존하지 않아 DB 없이도(fake conn으로) 그대로 테스트 가능하다.
"""


def get_latest_run(conn) -> dict | None:
    """가장 최근 weekly_run. 매주 전체 wipe 방침이라 사실상 이번 주 run이 유일하다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, period_start, period_end, status, completed_at "
            "FROM weekly_runs ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [c.name for c in cur.description]
        return dict(zip(columns, row))


def get_fixed_keywords(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, keyword FROM fixed_keywords WHERE active ORDER BY display_order, id"
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_market_keywords(conn, run_id: int, fixed_keyword_id: int, limit: int = 30) -> list[dict]:
    """"이번 주 주요 키워드" — 언급량(doc_count) 내림차순. Gemini 동의어 병합이
    끝난 최종 목록이라 canonical_phrase/variant_phrases를 그대로 쓰면 된다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_phrase, variant_phrases, doc_count, tfidf_score
            FROM market_keywords
            WHERE run_id = %s AND fixed_keyword_id = %s
            ORDER BY doc_count DESC, tfidf_score DESC NULLS LAST
            LIMIT %s
            """,
            (run_id, fixed_keyword_id, limit),
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_search_results(conn, run_id: int, fixed_keyword_id: int, limit: int = 20) -> list[dict]:
    """키워드 선택 없이 기본으로 보여줄 이번 주 기사 — 검색엔진 원 순위(rank) 기준."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT title, url, snippet, source_domain, published_at, rank
            FROM search_results
            WHERE run_id = %s AND fixed_keyword_id = %s
            ORDER BY rank ASC
            LIMIT %s
            """,
            (run_id, fixed_keyword_id, limit),
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_search_results_for_variants(
    conn, run_id: int, fixed_keyword_id: int, variant_phrases: list[str], limit: int = 20,
) -> list[dict]:
    """특정 키워드(동의어 그룹) 선택 시 — 제목·스니펫에 변형 표기 중 하나라도
    포함된 기사만. "사건 단위"가 아니라 "키워드 언급 기사 전체"라는 v2의
    더 넓은 범주를 그대로 반영한다(README 참고)."""
    if not variant_phrases:
        return []
    patterns = [f"%{v}%" for v in variant_phrases]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT title, url, snippet, source_domain, published_at, rank
            FROM search_results
            WHERE run_id = %s AND fixed_keyword_id = %s
              AND (title ILIKE ANY(%s) OR snippet ILIKE ANY(%s))
            ORDER BY rank ASC
            LIMIT %s
            """,
            (run_id, fixed_keyword_id, patterns, patterns, limit),
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
