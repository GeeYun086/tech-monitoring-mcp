"""Streamlit 대시보드(app/streamlit_app.py)가 쓰는 DB 조회 계층.

원칙(v1 ax-dashboard 스킬에서 이어받음 — .claude/skills/ax-dashboard/SKILL.md,
현재는 삭제됨): **계산은 여기서 끝내고, UI는 그대로 보여주기만 한다.** 랭킹·
필터링 로직을 화면 코드에 흩어두면 재사용도 안 되고 검증도 어렵다 — 이 모듈은
Streamlit에 의존하지 않아 DB 없이도(fake conn으로) 그대로 테스트 가능하다.

utils.text.strip_article_boilerplate: 화면 요약도 analysis/keyword_extraction.py
와 같은 정리를 거친다 — 안 그러면 ITWorld류 사이트의 고정 태그 목록이 그대로
잘려서 요약으로 나가버린다(2026-08-13 실사용 확인).

**요약 길이(2026-08-13 추가)**: search_results.snippet엔 Tavily의 content
(여러 문단짜리 발췌)가 그대로 들어있다 — 실사용 확인 결과 화면에 문단째로
쏟아져 나와 장황했다. get_search_results(_for_variants)가 반환 직전에
truncate_summary()로 짧게 잘라 UI가 항상 짧은 요약만 받게 한다(UI 쪽에서
매번 자르는 코드를 반복하지 않도록).

**번역은 안 한다** — 영문 기사의 title/snippet은 원문 그대로 나간다. 한글
번역은 LLM(또는 별도 번역 API)이 필요한데, 지금은 Gemini 크레딧이 막혀있어
붙이지 않았다(analysis/keyword_merge.py의 프리페이드 크레딧 이슈 참고).
크레딧 복구 후 붙일지, 별도 번역 서비스를 쓸지는 아직 미정 — 담당자 확인 필요.
"""

from tech_monitoring.utils.text import strip_article_boilerplate

_SUMMARY_MAX_CHARS = 150


def truncate_summary(text: str | None, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    """긴 발췌문을 화면용 짧은 요약으로 자른다. 개행·중복 공백을 하나로
    정리하고, 단어 중간에서 뚝 끊기지 않게 마지막 공백 기준으로 자른 뒤
    말줄임표를 붙인다. Tavily 응답에 섞여 나오는 "[...]" 청크 구분자도
    제거한다(실제 응답에서 확인 — 문단과 문단 사이를 이 표시로 잇는다)."""
    if not text:
        return ""
    text = strip_article_boilerplate(text)
    cleaned = " ".join(text.replace("[...]", " ").split())
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rsplit(" ", 1)[0] + "…"


def _apply_summary_truncation(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["snippet"] = truncate_summary(row.get("snippet"))
    return rows


def get_latest_run(conn) -> dict | None:
    """가장 최근 weekly_run. 매주 전체 wipe 방침이라 사실상 이번 주 run이 유일하다."""
    with conn.cursor() as cur:
        cur.execute(
            # error_message도 함께 — 실패한 단계 이름이 화면에 드러나야
            # "결과가 왜 비었지?"를 로그 뒤지지 않고 알 수 있다(2026-08-18).
            "SELECT id, period_start, period_end, status, completed_at, error_message "
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


def get_search_results(conn, run_id: int, fixed_keyword_id: int, limit: int | None = None) -> list[dict]:
    """키워드 선택 없이 기본으로 보여줄 이번 주 기사 — 최신순(published_at) 정렬.

    2026-08-13 이전엔 rank(검색엔진 내 순위) 기준으로 정렬했는데, rank는
    사이트마다 1부터 따로 매겨지는 값이다(collectors/search_engine.py가
    사이트별로 개별 호출) — 여러 사이트를 rank로 한데 정렬하면 결과 수가
    많은 사이트(TechCrunch)가 사실상 상위를 독점해서 AITimes 등 다른 사이트
    기사가 화면에 전혀 안 보이는 문제가 실사용 중 확인됐다. published_at
    기준 최신순이 사이트 간에 공정하다.

    limit=None(기본값)은 이번 주 수집분 전체를 반환한다 — top20으로
    자르지 않는다는 원래 설계 원칙(README 참고) + 담당자가 나중에 라벨링
    작업에 쓸 수 있게 넉넉히 보고 싶다고 확인(2026-08-13)."""
    query = (
        "SELECT title, url, snippet, source_domain, published_at, rank "
        "FROM search_results WHERE run_id = %s AND fixed_keyword_id = %s "
        "ORDER BY published_at DESC NULLS LAST"
    )
    params: list = [run_id, fixed_keyword_id]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [c.name for c in cur.description]
        return _apply_summary_truncation([dict(zip(columns, row)) for row in cur.fetchall()])


def get_pool_articles(
    conn, run_id: int, fixed_keyword_id: int | None = None, limit: int | None = None,
) -> list[dict]:
    """이번 주 공용 기사 풀(006부터 화면이 보여줄 목록).

    풀 자체는 시장과 무관하다 — 시장 탭들은 **같은 목록을 각자의 순서로**
    본다. fixed_keyword_id를 주면 그 시장의 분류기 점수(007)를 붙여 점수
    내림차순으로 정렬하고, 점수가 없는 기사(판단 전이거나 Gemini 판단)는
    NULLS LAST로 밀어낸 뒤 최신순으로 잇는다.

    **잘라내지 않는다.** 점수가 낮아도 목록에 남는다 — 분류기가 틀려도 기사가
    사라지면 안 되고("Gemini가 막히면 그 주 0건"과 같은 사고), 라벨링에는
    도움 안 되는 기사도 필요하다. 순위만 바꾸는 게 이 설계의 핵심이다."""
    if fixed_keyword_id is None:
        query = (
            "SELECT title, url, snippet, source_domain, published_at, source_name, "
            "NULL::real AS score "
            "FROM collected_articles WHERE run_id = %s "
            "ORDER BY published_at DESC NULLS LAST"
        )
        params: list = [run_id]
    else:
        query = (
            "SELECT ca.title, ca.url, ca.snippet, ca.source_domain, ca.published_at, "
            "ca.source_name, r.score "
            "FROM collected_articles ca "
            "LEFT JOIN article_keyword_relevance r "
            "  ON r.article_id = ca.id AND r.fixed_keyword_id = %s "
            "WHERE ca.run_id = %s "
            "ORDER BY r.score DESC NULLS LAST, ca.published_at DESC NULLS LAST"
        )
        params = [fixed_keyword_id, run_id]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [c.name for c in cur.description]
        return _apply_summary_truncation([dict(zip(columns, row)) for row in cur.fetchall()])


def get_pool_articles_for_variants(
    conn, run_id: int, variant_phrases: list[str], limit: int | None = None,
) -> list[dict]:
    """공용 풀에서 특정 키워드(동의어 그룹) 언급 기사만 — 문자열 포함 검사다.

    이건 **관련도 판단이 아니라 보조 필터**다. "에이전트 도입"처럼 기사에
    그대로 안 쓰이는 표현으로는 거의 걸리지 않으므로, 시장별 선별은 분류기가
    맡고 이 필터는 사용자가 특정 키워드를 눌러 좁혀볼 때만 쓴다."""
    if not variant_phrases:
        return []
    patterns = [f"%{v}%" for v in variant_phrases]
    query = (
        "SELECT title, url, snippet, source_domain, published_at, source_name "
        "FROM collected_articles WHERE run_id = %s "
        "AND (title ILIKE ANY(%s) OR snippet ILIKE ANY(%s)) "
        "ORDER BY published_at DESC NULLS LAST"
    )
    params: list = [run_id, patterns, patterns]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [c.name for c in cur.description]
        return _apply_summary_truncation([dict(zip(columns, row)) for row in cur.fetchall()])


def get_search_results_for_variants(
    conn, run_id: int, fixed_keyword_id: int, variant_phrases: list[str], limit: int | None = None,
) -> list[dict]:
    """특정 키워드(동의어 그룹) 선택 시 — 제목·스니펫에 변형 표기 중 하나라도
    포함된 기사만. "사건 단위"가 아니라 "키워드 언급 기사 전체"라는 v2의
    더 넓은 범주를 그대로 반영한다(README 참고). get_search_results와 같은
    이유로 published_at 최신순, limit=None 기본값."""
    if not variant_phrases:
        return []
    patterns = [f"%{v}%" for v in variant_phrases]
    query = (
        "SELECT title, url, snippet, source_domain, published_at, rank "
        "FROM search_results WHERE run_id = %s AND fixed_keyword_id = %s "
        "AND (title ILIKE ANY(%s) OR snippet ILIKE ANY(%s)) "
        "ORDER BY published_at DESC NULLS LAST"
    )
    params: list = [run_id, fixed_keyword_id, patterns, patterns]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [c.name for c in cur.description]
        return _apply_summary_truncation([dict(zip(columns, row)) for row in cur.fetchall()])
