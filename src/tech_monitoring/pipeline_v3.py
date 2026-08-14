"""v3 파이프라인 오케스트레이터 — 재선정 사이트 수집(RSS 2 + 스크래핑 2) →
LLM 적합성 판단(고정 키워드별) → TF-IDF 후보추출 + Gemini 동의어 병합
(pipeline='rss_llm'로 태그, analysis/keyword_merge.py의 run_for_all_keywords를
그대로 재사용). README "v2 vs v3 비교 실험" 참고.

**pipeline_v2.py와 달리 reset_weekly_data()를 호출하지 않는다.** v2·v3를
같은 주, 같은 run_id에 나란히 쌓아서 비교하는 게 이 파이프라인의 존재
이유인데, 매주 wipe(TRUNCATE weekly_runs CASCADE)는 딱 한 곳에서만
일어나야 한다 — 두 파이프라인이 각자 reset을 호출하면 나중에 도는 쪽이
먼저 돈 쪽의 이번 주 수집 결과를 통째로 지워버린다. 그래서 wipe는
pipeline_v2.py가 계속 담당하고, 여긴 start_weekly_run()으로 "이번 주 run"에
올라타기만 한다.

**알려진 한계(운영 방침으로 남겨둠)**: 이번 주 들어 v3가 v2보다 먼저
돌면(=이번 주 첫 실행이 v3면) 지난주 데이터가 안 비워진 채로 새 run_id
아래 쌓인다 — 정확성 문제는 없다(모든 조회가 run_id로 스코프됨)지만,
다음에 v2가 돌 때까지 스토리지가 한 주치 더 쌓인다. 비교 실험 기간엔
v2를 먼저 돌리는 걸 권장(README에도 명시).

순서 고정 이유(pipeline_v2.py와 동일 원칙): 수집이 끝나야 그 주
collected_articles가 확정되고, 그걸 기반으로 관련도 판단을 해야 하며,
관련도 판단이 끝나야 그걸 기반으로 키워드 후보를 뽑을 수 있다 — 순서가
바뀌면 지난주 데이터로 이번 주 결과를 만드는 사고가 난다.

실패 격리 원칙도 동일: 한 단계가 실패해도 나머지는 계속 진행하고, 실패한
단계는 report의 "failed"에 남는다.

    ./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v3
"""

import logging
import time

from tech_monitoring.analysis.keyword_merge import run_for_all_keywords
from tech_monitoring.analysis.relevance_filter import fetch_relevant_articles, judge_all
from tech_monitoring.collectors import aitimes_scraper, geeknews_weekly, rss_collector
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import complete_weekly_run, fail_weekly_run, start_weekly_run

logger = logging.getLogger("tech_monitoring.pipeline_v3")


def _start_run() -> int:
    # reset_weekly_data()를 의도적으로 호출하지 않음 — 모듈 docstring 참고.
    conn = get_connection()
    try:
        return start_weekly_run(conn)
    finally:
        conn.close()


def _finish_run(run_id: int, failed: list[str]) -> None:
    conn = get_connection()
    try:
        if failed:
            fail_weekly_run(conn, run_id, "; ".join(failed))
        else:
            complete_weekly_run(conn, run_id)
    finally:
        conn.close()


def _collect(run_id: int) -> dict:
    conn = get_connection()
    try:
        results = rss_collector.collect_all(conn, run_id)
        results.append(geeknews_weekly.collect_geeknews_weekly(conn, run_id))
        results.append(aitimes_scraper.collect_aitimes(conn, run_id))
        return {"results": results}
    finally:
        conn.close()


def _judge_relevance(run_id: int) -> dict:
    conn = get_connection()
    try:
        return {"results": judge_all(conn, run_id)}
    finally:
        conn.close()


def _merge_keywords(run_id: int) -> dict:
    conn = get_connection()
    try:
        return {"results": run_for_all_keywords(
            conn, run_id, pipeline="rss_llm", fetch_rows=fetch_relevant_articles,
        )}
    finally:
        conn.close()


def _stages(run_id: int) -> list[tuple[str, object]]:
    # 매 호출 시점에 run_id를 바인딩 — 테스트에서 monkeypatch로 각 단계
    # 자체를 갈아끼울 수 있게 모듈 함수를 참조 형태로 감싼다(pipeline_v2.py와 동일 패턴).
    return [
        ("collect", lambda: _collect(run_id)),
        ("judge_relevance", lambda: _judge_relevance(run_id)),
        ("merge_keywords", lambda: _merge_keywords(run_id)),
    ]


def run_pipeline() -> dict:
    run_id = _start_run()

    results: dict[str, dict] = {}
    failed: list[str] = []

    for name, fn in _stages(run_id):
        started = time.monotonic()
        try:
            result = fn()
            results[name] = result
            logger.info("%s ok (%.1fs): %s", name, time.monotonic() - started, result)
        except Exception as exc:
            failed.append(name)
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            logger.error("%s FAILED (%.1fs): %s", name, time.monotonic() - started, exc)

    _finish_run(run_id, failed)

    return {"run_id": run_id, "stages": results, "failed": failed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = run_pipeline()
    if report["failed"]:
        logger.error("파이프라인 완료, 실패한 단계: %s (run_id=%s)", report["failed"], report["run_id"])
    else:
        logger.info("파이프라인 전체 성공 (run_id=%s)", report["run_id"])
    print(report)
