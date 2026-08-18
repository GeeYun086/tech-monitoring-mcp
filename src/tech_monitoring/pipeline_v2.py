"""v2 파이프라인 오케스트레이터 — 검색엔진 수집 → 키워드 후보추출 →
Gemini 동의어 병합까지 정해진 순서로 한 번에 실행한다. v1의
pipeline.py(README·개발계획서 Phase 5 안정화)와 같은 목적을 새 아키텍처
(collectors/search_engine.py + analysis/keyword_extraction.py +
analysis/keyword_merge.py)에 적용한 것 — v1이 완전히 정리되면 이 파일이
pipeline.py 자리를 대체할 예정이다.

순서 고정 이유:
    (매주 데이터 wipe) → 이번 주 run 시작 → 검색엔진 수집(고정 키워드별) →
        키워드 후보추출 + Gemini 동의어 병합(고정 키워드별, run 전체 한 번에)
    수집이 끝나야 그 주 search_results가 확정되고, 그걸 기반으로 키워드
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

from tech_monitoring.analysis.keyword_merge import run_for_all_keywords
from tech_monitoring.collectors.search_engine import collect_all
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import (
    complete_weekly_run,
    fail_weekly_run,
    reset_weekly_data,
    start_weekly_run,
)
from tech_monitoring.pipeline_report import stage_errors

logger = logging.getLogger("tech_monitoring.pipeline_v2")


def _start_run() -> int:
    conn = get_connection()
    try:
        reset_weekly_data(conn)
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
    return {"results": collect_all(run_id)}


def _merge_keywords(run_id: int) -> dict:
    conn = get_connection()
    try:
        return {"results": run_for_all_keywords(conn, run_id)}
    finally:
        conn.close()


def _stages(run_id: int) -> list[tuple[str, object]]:
    # 매 호출 시점에 run_id를 바인딩 — 테스트에서 monkeypatch로 _collect/
    # _merge_keywords 자체를 갈아끼울 수 있게 모듈 함수를 참조 형태로 감싼다
    # (v1 pipeline.py와 동일 패턴).
    return [
        ("collect", lambda: _collect(run_id)),
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
