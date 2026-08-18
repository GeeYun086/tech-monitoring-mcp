"""최초 라벨링용 소급 수집 — 지난 몇 주 기사를 이번 주 run에 함께 담는다.

왜 필요한가: 파이프라인은 이번 주(달력 월~일)만 수집한다. 주 초반에 처음
돌리면 이틀치라 후보가 수십 건뿐이고(실측 2026-08-19: 52건), 라벨 30건을
모으기도 전에 바닥난다. 라벨이 없으면 분류기가 없고, 분류기가 없으면 화면
순서가 최신순에 머문다 — 시작 자체가 막히는 지점이다.

**Tavily만 소급이 가능하다**(start_date/end_date를 API 파라미터로 받는다).
v3 수집기는 원리적으로 안 된다 — RSS는 과거를 요청하는 파라미터가 없고,
AI타임스는 목록 페이지네이션이 막혀 있다(collectors/aitimes_scraper.py 주석).
그래서 이 스크립트는 검색엔진 경로 전용이다.

**주차별로 따로 호출한다.** 3주를 한 번에(start=3주 전, end=이번 주 일요일)
요청하면 호출 한 번으로 끝나 크레딧은 3분의 1이지만, Tavily는 페이지네이션이
없고 한 호출당 최대 20건(RESULTS_PER_SITE)이라 3주치가 20건으로 눌린다.
주차별로 나누면 주당 최대 20건씩 확보된다 — 물량이 목적이므로 이쪽을 택한다.

크레딧: 사이트 6개 × 넓은 질의 2개 × 주차 수. 2주 소급이면 24크레딧
(무료 월 1,000 기준 여유). 이번 주 정기 수집 12크레딧과 별개다.

**한 번만 돌리면 된다.** 라벨은 매주 wipe를 타지 않으므로(004) 여기서 만든
후보를 라벨링해 두면 그 가치는 영구히 남는다. 반면 소급 수집된 기사 자체는
다음 파이프라인 실행 시 wipe되므로, 라벨링을 미루면 후보가 사라진다.
필요하면 다시 돌려도 된다(같은 기사는 UNIQUE (run_id, url)로 한 번만 저장).

라벨의 주차 그룹은 run이 아니라 **기사 발행 주**로 잡힌다
(labeling._label_period_start) — 그래서 소급 수집분이 자연히 여러 주로 갈려
"지난주 라벨로 학습한 모델이 이번 주 기사에 통하는가"를 바로 측정할 수 있다.

    ./.venv/Scripts/python.exe scripts/backfill_past_weeks.py            # 2주(기본)
    ./.venv/Scripts/python.exe scripts/backfill_past_weeks.py --weeks 3
"""

import argparse
import sys
from datetime import date, timedelta

from tech_monitoring.collectors.search_engine import (
    SITE_DOMAINS,
    broad_queries_for_domain,
    collect_pool_for_site,
)
from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import get_run_period, week_bounds_for

DEFAULT_WEEKS = 2


def past_week_bounds(weeks: int, today: date | None = None) -> list[tuple[date, date]]:
    """오래된 주부터 차례로 (월요일, 일요일). 이번 주는 포함하지 않는다 —
    정기 수집이 이미 담당한다."""
    today = today or date.today()
    monday, _end = week_bounds_for(today)
    return [week_bounds_for(monday - timedelta(weeks=n)) for n in range(weeks, 0, -1)]


def backfill(conn, run_id: int, weeks: int = DEFAULT_WEEKS, today: date | None = None) -> list[dict]:
    """지난 `weeks`주를 주차별로 수집해 이 run에 담는다.

    한 사이트·주차가 실패해도 나머지는 계속 진행한다(collect_pool_for_site가
    예외를 error로 바꿔 돌려준다) — 수집기 전체의 실패 격리 원칙과 같다.
    """
    results = []
    for start_date, end_date in past_week_bounds(weeks, today):
        for domain in SITE_DOMAINS:
            result = collect_pool_for_site(conn, run_id, domain, start_date, end_date)
            results.append({**result, "period_start": start_date})
    return results


def _latest_run_id(conn) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM weekly_runs ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    return row[0] if row else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS, help="소급할 주 수(기본 2)")
    args = parser.parse_args()

    if not settings.tavily_api_key:
        print("TAVILY_API_KEY가 비어 있습니다 — .env를 확인하세요.", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    try:
        run_id = _latest_run_id(conn)
        if run_id is None:
            print("weekly_runs가 비어 있습니다 — 먼저 파이프라인을 한 번 실행하세요:\n"
                  "  ./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v2", file=sys.stderr)
            sys.exit(1)

        period_start, period_end = get_run_period(conn, run_id)
        weeks = past_week_bounds(args.weeks)
        per_week = sum(len(broad_queries_for_domain(d)) for d in SITE_DOMAINS)
        credits = per_week * len(weeks)
        print(f"run {run_id}(기준 주 {period_start} ~ {period_end})에 소급 수집분을 추가합니다.")
        print(f"대상 주차: {', '.join(f'{s}~{e}' for s, e in weeks)} (약 {credits}크레딧)\n")

        results = backfill(conn, run_id, args.weeks)

        by_week: dict[date, list[dict]] = {}
        for r in results:
            by_week.setdefault(r["period_start"], []).append(r)
        for week, rows in sorted(by_week.items()):
            got = sum(r["inserted"] for r in rows)
            print(f"  {week} 주: {got}건 추가")
            for r in rows:
                if r["error"]:
                    print(f"    실패 — {r['source']}: {r['error']}", file=sys.stderr)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM collected_articles WHERE run_id = %s", (run_id,))
            total = cur.fetchone()[0]
        print(f"\n이 run의 기사 풀: 총 {total}건. 이제 '🏷️ 라벨링' 탭에서 시작하세요.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
