"""주간 배치 실행(weekly_runs) 생명주기 관리 + 고정 키워드 조회.

collectors/search_engine.py와 이후 단계(analysis/keyword_extraction.py)가
공유하는 run_id 앵커를 여기서 관리한다. 무료 DB 티어 유지 방침(매주 전체
wipe — db/migrations/001_market_keywords_schema.sql 헤더 주석 참고)에 따라
reset_weekly_data()가 fixed_keywords만 남기고 나머지 전부를 비운다.
"""

from datetime import date, timedelta


def get_active_fixed_keywords(conn) -> list[dict]:
    """search_terms_ko/en(2026-08-13 추가)도 함께 반환한다 — 실제 검색은
    keyword 문자열 그대로가 아니라 이 언어별 동의어 목록으로 한다
    (collectors/search_engine.py, db/migrations/003_multi_term_keywords.sql
    헤더 주석 참고). 비어있으면 collect 쪽에서 keyword로 폴백한다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, keyword, search_terms_ko, search_terms_en FROM fixed_keywords "
            "WHERE active ORDER BY display_order, id"
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def week_bounds_for(day: date) -> tuple[date, date]:
    """그 날짜가 속한 달력 주(월~일). 이번 주뿐 아니라 과거 주에도 쓴다 —
    최초 라벨링용 소급 수집(scripts/backfill_past_weeks.py)이 주차별로
    Tavily 검색 범위를 잡을 때, 그리고 라벨을 기사 발행 주로 묶을 때
    (labeling.save_label) 같은 기준을 써야 한다."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def _current_week_bounds(today: date | None = None) -> tuple[date, date]:
    """이번 주 월~일 달력 주간.

    2026-08-13에 "실행일 기준 지난 7일" 롤링 윈도우로 바꾼 적이 있는데
    (Tavily time_range="week"가 그런 의미라서), 담당자가 달력 주 표시를
    원해 다시 되돌렸다. 대신 이제 collectors/search_engine.py가
    time_range 대신 이 period_start/end를 그대로 Tavily의 start_date/
    end_date로 넘겨 실제 검색 자체를 이 달력 주로 정확히 맞춘다 — 그래서
    배너 표시와 실제 수집 기사 날짜가 항상 일치한다(rolling window로
    바꿨을 때와 달리, 이번엔 "표시"가 아니라 "실제 검색 범위"를 달력 주에
    맞추는 방식이라 두 요구사항이 충돌하지 않는다)."""
    return week_bounds_for(today or date.today())


def start_weekly_run(conn, today: date | None = None) -> int:
    """이번 주 run을 시작(또는 같은 주 재실행 시 기존 run을 'running'으로 재개)한다."""
    period_start, period_end = _current_week_bounds(today)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO weekly_runs (period_start, period_end, status)
            VALUES (%s, %s, 'running')
            ON CONFLICT (period_start, period_end)
                DO UPDATE SET status = 'running', error_message = NULL
            RETURNING id
            """,
            (period_start, period_end),
        )
        return cur.fetchone()[0]


def get_run_period(conn, run_id: int) -> tuple[date, date]:
    """collectors/search_engine.py가 Tavily의 start_date/end_date를 이
    run이 잡아둔 달력 주(period_start/end)에 정확히 맞추기 위해 조회한다."""
    with conn.cursor() as cur:
        cur.execute("SELECT period_start, period_end FROM weekly_runs WHERE id = %s", (run_id,))
        return cur.fetchone()


def complete_weekly_run(conn, run_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE weekly_runs SET status = 'completed', completed_at = now() WHERE id = %s",
            (run_id,),
        )


def fail_weekly_run(conn, run_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE weekly_runs SET status = 'failed', error_message = %s, completed_at = now() WHERE id = %s",
            (error, run_id),
        )


def reset_weekly_data(conn) -> None:
    """무료 DB 티어 유지 방침 — 매주 이 함수로 전체 wipe 후 재수집.
    fixed_keywords는 weekly_runs를 참조하지 않으므로 CASCADE에 안 걸려 보존된다."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE weekly_runs RESTART IDENTITY CASCADE")
