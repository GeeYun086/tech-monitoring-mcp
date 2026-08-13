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
    """이번 주 월~일 범위. 검색엔진 쪽 dateRestrict=w1("지난 7일" 롤링 윈도우)과는
    별개로, weekly_runs.period_start/end는 사람이 보기 좋은 달력 주 단위로 기록한다
    (같은 주에 배치를 다시 돌려도 UNIQUE(period_start, period_end)로 같은 run에 묶임)."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


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
