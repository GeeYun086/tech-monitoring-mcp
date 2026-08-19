"""db/weekly_run.py 테스트 — weekly_runs 생명주기·fixed_keywords 조회.
실제 DB 없이 실행된 SQL과 파라미터를 그대로 기록하는 스파이 conn/cursor를 쓴다."""

from datetime import date

from tech_monitoring.db import weekly_run as wr


class _SpyCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self.executed: list[tuple[str, tuple]] = []
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []
        self.description = [
            type("Col", (), {"name": n})() for n in ("id", "keyword", "search_terms_ko", "search_terms_en")
        ]

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
    cursor = _SpyCursor(fetchall_result=[
        (1, "AX 시장", ["AX", "AI 전환"], ["AI transformation"]),
        (2, "생성형 AI", [], []),
    ])
    conn = _SpyConn(cursor)

    result = wr.get_active_fixed_keywords(conn)

    assert result == [
        {"id": 1, "keyword": "AX 시장", "search_terms_ko": ["AX", "AI 전환"], "search_terms_en": ["AI transformation"]},
        {"id": 2, "keyword": "생성형 AI", "search_terms_ko": [], "search_terms_en": []},
    ]
    query, params = cursor.executed[0]
    assert "search_terms_ko" in query and "search_terms_en" in query
    assert "WHERE active" in query
    assert "ORDER BY display_order, id" in query


def test_current_week_bounds_returns_monday_to_sunday():
    # 2026-08-13은 목요일. Tavily 검색 자체를 이 달력 주(start_date/end_date)로
    # 정확히 맞추므로(collectors/search_engine.py), 롤링 윈도우로 되돌아갈
    # 필요 없이 배너 표시와 실제 검색 범위가 일치한다.
    start, end = wr._current_week_bounds(date(2026, 8, 13))
    assert start == date(2026, 8, 10)
    assert end == date(2026, 8, 16)
    assert start.weekday() == 0
    assert end.weekday() == 6


def test_start_weekly_run_upserts_with_computed_bounds():
    cursor = _SpyCursor(fetchone_result=(42,))
    conn = _SpyConn(cursor)

    run_id = wr.start_weekly_run(conn, today=date(2026, 8, 13))

    assert run_id == 42
    query, params = cursor.executed[0]
    assert "INSERT INTO weekly_runs" in query
    assert "ON CONFLICT" in query
    assert params == (date(2026, 8, 10), date(2026, 8, 16))


def test_get_run_period_returns_bounds():
    cursor = _SpyCursor(fetchone_result=(date(2026, 8, 10), date(2026, 8, 16)))
    conn = _SpyConn(cursor)

    result = wr.get_run_period(conn, run_id=42)

    assert result == (date(2026, 8, 10), date(2026, 8, 16))
    query, params = cursor.executed[0]
    assert "FROM weekly_runs" in query
    assert params == (42,)


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


# ---- week_bounds_for: 과거 주에도 같은 기준(소급 수집·라벨 주차 그룹) ----

def test_week_bounds_for_returns_monday_to_sunday():
    assert wr.week_bounds_for(date(2026, 8, 19)) == (date(2026, 8, 17), date(2026, 8, 23))


def test_week_bounds_for_is_stable_within_the_same_week():
    assert wr.week_bounds_for(date(2026, 8, 17)) == wr.week_bounds_for(date(2026, 8, 23))


# ---- 수집 주기: 최초 3주치 → 이후 직전 주만 (2026-08-19 담당자 결정) ----

def test_previous_week_bounds_returns_the_completed_week():
    """월요일에 돌리면 방금 끝난 주를 걷어야 한다 — 진행 중인 주를 걷으면
    월·화엔 이틀치뿐이라 라벨링 후보가 수십 건에 그친다(실측 52건)."""
    # 2026-08-24는 월요일
    assert wr.previous_week_bounds(date(2026, 8, 24)) == (date(2026, 8, 17), date(2026, 8, 23))


def test_previous_week_is_stable_regardless_of_weekday_run():
    """월요일에 못 돌려도 그 주 안에 돌리면 같은 주를 걷는다(수동 실행 대비)."""
    assert wr.previous_week_bounds(date(2026, 8, 26)) == wr.previous_week_bounds(date(2026, 8, 24))


def test_bootstrap_collects_three_weeks_including_this_one():
    weeks = wr.target_weeks(bootstrap=True, today=date(2026, 8, 19))

    assert weeks == [
        (date(2026, 8, 3), date(2026, 8, 9)),
        (date(2026, 8, 10), date(2026, 8, 16)),
        (date(2026, 8, 17), date(2026, 8, 23)),   # 이번 주 포함
    ]


def test_regular_run_collects_only_the_previous_week():
    assert wr.target_weeks(bootstrap=False, today=date(2026, 8, 24)) == [
        (date(2026, 8, 17), date(2026, 8, 23)),
    ]


def test_run_period_is_the_most_recent_collected_week():
    """화면 배너의 "기준 기간" — 소급분이 섞여도 기준은 가장 최근 주다."""
    conn = _SpyConn(_SpyCursor(fetchone_result=None))   # 최초 기록 없음
    plan = wr.plan_collection(conn, today=date(2026, 8, 19))

    assert plan["bootstrap"] is True
    assert plan["run_period"] == (date(2026, 8, 17), date(2026, 8, 23))
    assert len(plan["weeks"]) == wr.BOOTSTRAP_WEEKS


def test_plan_switches_to_single_week_once_bootstrapped():
    """핵심 — 최초 수집을 마쳤다고 기록되면 그 뒤로는 직전 주만 걷는다
    (매 실행마다 3주치를 다시 긁어 크레딧을 반복 소모하면 안 된다)."""
    conn = _SpyConn(_SpyCursor(fetchone_result=("2026-08-19",)))   # 최초 수집 기록 있음

    plan = wr.plan_collection(conn, today=date(2026, 8, 24))

    assert plan["bootstrap"] is False
    assert plan["weeks"] == [(date(2026, 8, 17), date(2026, 8, 23))]


def test_start_weekly_run_uses_the_given_period():
    """정기 수집은 직전 주를 기준 주로 기록해야 한다 — 안 그러면 배너 기간과
    실제 수집 범위가 어긋난다."""
    cursor = _SpyCursor(fetchone_result=(1,))
    conn = _SpyConn(cursor)

    wr.start_weekly_run(conn, period=(date(2026, 8, 17), date(2026, 8, 23)))

    _query, params = cursor.executed[-1]
    assert params[:2] == (date(2026, 8, 17), date(2026, 8, 23))
