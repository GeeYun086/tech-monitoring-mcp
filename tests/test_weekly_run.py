"""db/weekly_run.py 테스트 — weekly_runs 생명주기·fixed_keywords 조회.
실제 DB 없이 실행된 SQL과 파라미터를 그대로 기록하는 스파이 conn/cursor를 쓴다."""

from datetime import date

from tech_monitoring.db import weekly_run as wr


class _SpyCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.executed: list[tuple[str, tuple]] = []
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []
        self.description = [type("Col", (), {"name": n})() for n in ("id", "keyword")]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=()):
        self.executed.append((query, params))

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result


class _SpyConn:
    def __init__(self, cursor: _SpyCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_get_active_fixed_keywords_queries_active_only_ordered():
    cursor = _SpyCursor(fetchall_result=[(1, "AX 시장"), (2, "생성형 AI")])
    conn = _SpyConn(cursor)

    result = wr.get_active_fixed_keywords(conn)

    assert result == [{"id": 1, "keyword": "AX 시장"}, {"id": 2, "keyword": "생성형 AI"}]
    query, params = cursor.executed[0]
    assert "WHERE active" in query
    assert "ORDER BY display_order, id" in query


def test_current_week_bounds_returns_rolling_7_days_ending_today():
    """2026-08-13 실제로 겪은 버그의 회귀 테스트 — 달력 월~일이 아니라
    "실행일 기준 지난 7일"이어야 한다(Tavily time_range="week"와 정확히
    같은 범위여야 대시보드 배너와 실제 수집 기사 날짜가 어긋나지 않는다)."""
    start, end = wr._current_week_bounds(date(2026, 8, 13))
    assert start == date(2026, 8, 7)
    assert end == date(2026, 8, 13)


def test_start_weekly_run_upserts_with_computed_bounds():
    cursor = _SpyCursor(fetchone_result=(42,))
    conn = _SpyConn(cursor)

    run_id = wr.start_weekly_run(conn, today=date(2026, 8, 13))

    assert run_id == 42
    query, params = cursor.executed[0]
    assert "INSERT INTO weekly_runs" in query
    assert "ON CONFLICT" in query
    assert params == (date(2026, 8, 7), date(2026, 8, 13))


def test_complete_weekly_run_updates_status():
    cursor = _SpyCursor()
    conn = _SpyConn(cursor)

    wr.complete_weekly_run(conn, run_id=42)

    query, params = cursor.executed[0]
    assert "SET status = 'completed'" in query
    assert params == (42,)


def test_fail_weekly_run_records_error_message():
    cursor = _SpyCursor()
    conn = _SpyConn(cursor)

    wr.fail_weekly_run(conn, run_id=42, error="boom")

    query, params = cursor.executed[0]
    assert "SET status = 'failed'" in query
    assert params == ("boom", 42)


def test_reset_weekly_data_truncates_weekly_runs_with_cascade():
    cursor = _SpyCursor()
    conn = _SpyConn(cursor)

    wr.reset_weekly_data(conn)

    query, _params = cursor.executed[0]
    assert "TRUNCATE weekly_runs" in query
    assert "CASCADE" in query
