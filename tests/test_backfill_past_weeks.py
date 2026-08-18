"""소급 수집 스크립트 테스트(scripts/backfill_past_weeks.py).

핵심은 두 가지 — (1) 대상 주차 계산이 맞는지(이번 주는 제외, 오래된 주부터),
(2) 주차별로 따로 수집하는지. 3주를 한 번에 요청하면 Tavily의 호출당 20건
상한에 3주치가 눌려 물량이 안 나온다(스크립트 docstring 참고)."""

from datetime import date

from scripts import backfill_past_weeks as bf
from tech_monitoring.collectors import search_engine


def test_past_weeks_excludes_this_week_and_starts_from_oldest():
    weeks = bf.past_week_bounds(2, today=date(2026, 8, 19))

    assert weeks == [
        (date(2026, 8, 3), date(2026, 8, 9)),
        (date(2026, 8, 10), date(2026, 8, 16)),
    ]


def test_past_weeks_respects_requested_count():
    assert len(bf.past_week_bounds(4, today=date(2026, 8, 19))) == 4


def test_backfill_collects_each_site_once_per_week(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bf, "collect_pool_for_site",
        lambda conn, run_id, domain, start_date, end_date: calls.append((domain, start_date))
        or {"source": domain, "fetched": 0, "inserted": 0, "error": None},
    )

    results = bf.backfill(conn=None, run_id=1, weeks=2, today=date(2026, 8, 19))

    assert len(calls) == len(search_engine.SITE_DOMAINS) * 2
    assert {start for _domain, start in calls} == {date(2026, 8, 3), date(2026, 8, 10)}
    # 어느 주 수집분인지 결과에 남아야 요약 출력이 주차별로 묶인다.
    assert {r["period_start"] for r in results} == {date(2026, 8, 3), date(2026, 8, 10)}


def test_backfill_keeps_going_when_one_site_fails(monkeypatch):
    """한 사이트가 죽어도 나머지 주차·사이트는 계속 수집해야 한다."""
    def fake_collect(conn, run_id, domain, start_date, end_date):
        error = "boom" if domain == "aitimes.com" else None
        return {"source": domain, "fetched": 0, "inserted": 0, "error": error}

    monkeypatch.setattr(bf, "collect_pool_for_site", fake_collect)

    results = bf.backfill(conn=None, run_id=1, weeks=1, today=date(2026, 8, 19))

    assert len(results) == len(search_engine.SITE_DOMAINS)
    assert sum(1 for r in results if r["error"]) == 1
