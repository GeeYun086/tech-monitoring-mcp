"""v2 파이프라인 오케스트레이터 — 검색엔진 수집 → 키워드 후보추출 →
Gemini 동의어 병합까지 정해진 순서로 한 번에 실행한다. v1의
pipeline.py(README·개발계획서 Phase 5 안정화)와 같은 목적을 새 아키텍처
(collectors/search_engine.py + analysis/keyword_extraction.py +
analysis/keyword_merge.py)에 적용한 것 — v1이 완전히 정리되면 이 파일이
pipeline.py 자리를 대체할 예정이다.

순서 고정 이유:
    (매주 데이터 wipe) → 이번 주 run 시작 → 검색엔진 수집(사이트별 공용 풀)
    → 시장별 관련도 점수(분류기) →
        키워드 후보추출 + Gemini 동의어 병합(고정 키워드별, run 전체 한 번에)
    수집이 끝나야 그 주 collected_articles가 확정되고, 그걸 기반으로 키워드
    후보를 뽑아야 한다 — 순서가 바뀌면 지난주 데이터로 이번 주 키워드를
    뽑는 사고가 난다(v1이 겪은 "본문 백필 누락" 사고와 같은 종류의 문제라
    v1 pipeline.py처럼 순서를 코드로 고정해둔다).

무료 DB 티어 유지 방침(db/migrations/001_market_keywords_schema.sql)에 따라
reset_weekly_data()로 지난주 데이터를 통째로 비운 뒤 이번 주 run을 새로
시작한다 — fixed_keywords(사용자 설정)는 TRUNCATE CASCADE 범위 밖이라
안 건드려진다.

실패 격리는 v1과 같은 원칙: 한 단계(수집 또는 병합)가 실패해도 나머지는
계속 진행하고, 실패한 단계는 report의 "failed"에 남는다. 다만 run
시작(_start_run: reset + weekly_runs row 생성) 자체는 격리하지 않는다 —
run_id 없이는 뒤 단계가 결과를 어디에도 쓸 수 없으므로 여기서 실패하면
그대로 예외를 올린다.

"실패"의 정의는 예외만이 아니다(2026-08-18 수정) — 단계가 정상 리턴했더라도
결과 항목에 error가 섞여 있으면 부분 실패로 세서 failed에 넣는다. 그 전까지는
TAVILY_API_KEY 미설정처럼 한 건도 못 가져온 경우에도 "collect ok"가 찍히고
run이 completed로 마감됐다(pipeline_report.py 헤더 주석 참고).

    ./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v2
"""

import logging
import time

from tech_monitoring.analysis.keyword_extraction import fetch_pool_rows
from tech_monitoring.analysis.keyword_merge import run_for_all_keywords
from tech_monitoring.analysis.relevance_filter import judge_all
from tech_monitoring.collectors.search_engine import collect_all
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import (
    complete_weekly_run,
    fail_weekly_run,
    mark_bootstrapped,
    plan_collection,
    reset_weekly_data,
    start_weekly_run,
)
from tech_monitoring.pipeline_report import stage_errors

logger = logging.getLogger("tech_monitoring.pipeline_v2")


def _start_run() -> tuple[int, dict]:
    """수집 계획을 먼저 정하고 run을 연다.

    계획을 wipe 전에 읽는 게 중요하다 — 최초 여부(pipeline_state)는 wipe
    대상이 아니지만, 순서를 뒤집으면 나중에 다른 상태를 볼 때 실수하기 쉽다.
    """
    conn = get_connection()
    try:
        plan = plan_collection(conn)
        reset_weekly_data(conn)
        return start_weekly_run(conn, period=plan["run_period"]), plan
    finally:
        conn.close()


def _mark_bootstrapped() -> None:
    conn = get_connection()
    try:
        mark_bootstrapped(conn)
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


def _collect(run_id: int, weeks=None) -> dict:
    return {"results": collect_all(run_id, weeks)}


def _judge_relevance(run_id: int) -> dict:
    """수집된 기사에 시장별 점수를 매긴다(007). 학습된 모델이 없으면 각
    항목이 "skipped:모델 없음"으로 돌아온다 — 실패가 아니라 아직 라벨이
    없다는 뜻이라 error에 담지 않는다(담으면 파이프라인이 매주 실패로
    마감된다). 그 상태에서는 화면이 최신순 전체를 보여준다."""
    conn = get_connection()
    try:
        return {"results": judge_all(conn, run_id)}
    finally:
        conn.close()


def _merge_keywords(run_id: int) -> dict:
    """키워드 후보를 공용 기사 풀에서 뽑는다(006부터) — 기본값인
    search_results는 이제 수집되지 않으므로 fetch_rows를 명시해야 한다."""
    conn = get_connection()
    try:
        return {"results": run_for_all_keywords(conn, run_id, fetch_rows=fetch_pool_rows)}
    finally:
        conn.close()


def _stages(run_id: int, weeks=None) -> list[tuple[str, object]]:
    # 매 호출 시점에 run_id를 바인딩 — 테스트에서 monkeypatch로 _collect/
    # _merge_keywords 자체를 갈아끼울 수 있게 모듈 함수를 참조 형태로 감싼다
    # (v1 pipeline.py와 동일 패턴).
    return [
        ("collect", lambda: _collect(run_id, weeks)),
        ("judge_relevance", lambda: _judge_relevance(run_id)),
        ("merge_keywords", lambda: _merge_keywords(run_id)),
    ]


def run_pipeline() -> dict:
    run_id, plan = _start_run()
    logger.info(
        "수집 대상 %s: %s",
        "3주치(최초 1회)" if plan["bootstrap"] else "직전 주",
        ", ".join(f"{s}~{e}" for s, e in plan["weeks"]),
    )

    results: dict[str, dict] = {}
    failed: list[str] = []

    for name, fn in _stages(run_id, plan["weeks"]):
        started = time.monotonic()
        try:
            result = fn()
        except Exception as exc:
            failed.append(name)
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            logger.error("%s FAILED (%.1fs): %s", name, time.monotonic() - started, exc)
            continue

        # 예외가 안 났어도 끝난 게 아니다 — collect는 TAVILY_API_KEY가
        # 비어 한 건도 못 가져와도 각 항목의 "error"에 사유만 담아 정상
        # 리턴한다. 그걸 실패로 안 세면 빈 주가 completed로 마감된다
        # (pipeline_report.py 헤더 주석 참고).
        results[name] = result
        errors = stage_errors(result)
        if errors:
            failed.append(name)
            logger.error(
                "%s FAILED (%.1fs): %s", name, time.monotonic() - started, "; ".join(errors),
            )
        else:
            logger.info("%s ok (%.1fs): %s", name, time.monotonic() - started, result)

    # 최초 수집을 마쳤다고 표시하는 건 **실제로 성공했을 때만** — 실패했는데
    # 표시하면 다음부터 직전 주만 걷어 3주치를 영영 못 채운다.
    if plan["bootstrap"] and "collect" not in failed:
        _mark_bootstrapped()
        logger.info("최초 3주치 수집 완료 — 다음부터는 직전 주만 걷습니다.")

    _finish_run(run_id, failed)

    return {"run_id": run_id, "stages": results, "failed": failed,
            "bootstrap": plan["bootstrap"],
            "weeks": [(str(s), str(e)) for s, e in plan["weeks"]]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = run_pipeline()
    if report["failed"]:
        logger.error("파이프라인 완료, 실패한 단계: %s (run_id=%s)", report["failed"], report["run_id"])
    else:
        logger.info("파이프라인 전체 성공 (run_id=%s)", report["run_id"])
    print(report)
