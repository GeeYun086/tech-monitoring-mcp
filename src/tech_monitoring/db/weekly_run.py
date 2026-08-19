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


def previous_week_bounds(today: date | None = None) -> tuple[date, date]:
    """직전(완료된) 달력 주. 정기 수집이 매주 월요일에 걷는 범위다.

    2026-08-19에 "이번 주"에서 여기로 옮겼다(담당자 결정). 이번 주를 걷으면
    아직 끝나지 않은 주를 긁는 셈이라, 월·화에 돌리면 이틀치뿐이라 후보가
    수십 건에 그쳤다(실측 2026-08-19: 52건). 라벨 30건을 모으기도 전에
    바닥나서 소급 수집 스크립트로 메워야 했던 게 그 때문이다. 월요일에
    직전 주를 걷으면 항상 완료된 7일치가 들어와 그 문제가 사라진다.
    """
    return week_bounds_for((today or date.today()) - timedelta(days=7))


# 최초 1회 수집 범위: 이번 주 + 지난 2주. 라벨링을 시작하려면 후보가
# 최소 수백 건은 있어야 하고(30건 이상 라벨해야 분류기 학습이 시작된다),
# 주차가 여럿이어야 relevance_model.build_groups가 "주차 단위" 평가로
# 전환돼 "지난주 라벨로 이번 주 기사를 맞히는가"를 잴 수 있다.
BOOTSTRAP_WEEKS = 3

_BOOTSTRAP_KEY = "bootstrap_completed_at"


def is_bootstrapped(conn) -> bool:
    """최초 수집을 이미 마쳤는가(008 pipeline_state)."""
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM pipeline_state WHERE key = %s", (_BOOTSTRAP_KEY,))
        return cur.fetchone() is not None


def mark_bootstrapped(conn, today: date | None = None) -> None:
    """최초 수집 완료 기록. **수집이 실제로 성공했을 때만** 부른다 —
    실패했는데 표시해버리면 3주치를 영영 못 걷고 직전 주만 걷게 된다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pipeline_state (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (_BOOTSTRAP_KEY, str(today or date.today())),
        )


def target_weeks(bootstrap: bool, today: date | None = None) -> list[tuple[date, date]]:
    """이번 실행에서 수집할 달력 주 목록(오래된 주부터).

    **언제나 완료된 주만 걷는다.** 최초에는 완료된 최근 3주, 그 뒤로는 직전
    주 하나. 진행 중인 주는 어느 경우에도 걷지 않는다 — 그 주는 다음 월요일
    실행이 온전한 7일치로 담당한다(2026-08-19 담당자 확인).

    최초에도 이번 주를 뺀 이유: 진행 중인 주를 섞으면 "8/17 주"라는 같은
    이름의 데이터가 이번엔 사흘치, 다음 주엔 7일치가 되어 주차별 비교가
    어긋난다. 라벨의 주차 그룹도 마찬가지로 반쪽짜리 주를 하나 더 만든다.

    예) 오늘이 2026-08-19(수)라면
        최초  : 7/27~8/02, 8/03~8/09, 8/10~8/16   (8/17~ 은 아직 진행 중)
        그 뒤 : 8/24(월)에 8/17~8/23
    """
    last_monday, _end = previous_week_bounds(today)
    count = BOOTSTRAP_WEEKS if bootstrap else 1
    return [week_bounds_for(last_monday - timedelta(weeks=n))
            for n in range(count - 1, -1, -1)]


def plan_collection(conn, today: date | None = None) -> dict:
    """이번 실행이 걷을 주차와, run에 기록할 기준 주를 함께 정한다.

    기준 주(run_period)는 걷는 주차 중 **가장 최근 주**다 — 화면 배너의
    "기준 기간"이 되고, 소급분이 섞이면 그 사실은 dashboard_queries.
    get_pool_span이 따로 알려준다.
    """
    bootstrap = not is_bootstrapped(conn)
    weeks = target_weeks(bootstrap, today)
    return {"weeks": weeks, "run_period": weeks[-1], "bootstrap": bootstrap}


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


def start_weekly_run(conn, today: date | None = None, period: tuple[date, date] | None = None) -> int:
    """run을 시작(또는 같은 주 재실행 시 기존 run을 'running'으로 재개)한다.

    period를 주면 그 주를 기준 주로 쓴다(plan_collection이 정한 값). 안 주면
    예전대로 이번 주 — v3 파이프라인과 수집기 단독 실행(__main__)이 아직
    그 경로를 쓴다.
    """
    period_start, period_end = period if period is not None else _current_week_bounds(today)
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
