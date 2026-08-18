"""주간 배치 실행(weekly_runs) 생명주기 관리 + 고정 키워드 조회.

collectors/search_engine.py와 이후 단계(analysis/keyword_extraction.py)가
공유하는 run_id 앵커를 여기서 관리한다. 무료 DB 티어 유지 방침(매주 전체
wipe — db/migrations/001_market_keywords_schema.sql 헤더 주석 참고)에 따라
reset_weekly_data()가 fixed_keywords만 남기고 나머지 전부를 비운다.
"""

from datetime import date, timedelta


def get_active_fixed_keywords(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, keyword FROM fixed_keywords WHERE active ORDER BY display_order, id"
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _current_week_bounds(today: date | None = None) -> tuple[date, date]:
    """실행일 기준 "지난 7일" 롤링 윈도우 — collectors/search_engine.py가
    Tavily에 실제로 보내는 time_range="week"(호출 시점 기준 지난 7일)와
    정확히 같은 범위여야 한다.

    2026-08-13에 겪은 버그: 원래 이 함수가 "이번 주 월~일" 달력 주간을
    돌려줬는데, Tavily의 time_range="week"는 달력 주가 아니라 "실행 시점
    기준 지난 7일"이라 둘이 다른 기간을 가리켰다(실행일이 수요일이면
    달력 주는 그 주 월~일 전체를 말하지만, 실제 검색은 그 전주 목요일부터
    실행일까지만 훑음). 그 결과 대시보드 배너("기준 기간: 8/10~8/16")와
    실제 수집된 기사 날짜(8/6~8/13)가 어긋나 보였다. 이제는 항상 실행일을
    끝점으로 하는 지난 7일로 맞춰서 배너와 실제 검색 범위가 항상 일치한다."""
    today = today or date.today()
    return today - timedelta(days=6), today


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
