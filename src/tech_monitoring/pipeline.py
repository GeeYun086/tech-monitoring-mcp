"""전체 파이프라인을 정해진 순서로 한 번에 실행 (Phase 5 안정화).

배경: 지금까지 각 단계를 README 안내에 따라 사람이 순서대로 수동 실행했다.
실제로 이 때문에 문제가 생겼다 — collectors.extract_content(본문 백필)가
누락된 채로 여러 날 운영되면서, 신규 기사 205건 중 192건이 "Points: N" 같은
메타데이터만으로 임베딩됐고 그 결과 관련도 필터가 사실상 무력화됐다
(search_news 실사용 검증 중 발견). 수동 단계가 많을수록 이런 누락이 또
생긴다 — 이 모듈은 정해진 순서를 코드로 고정해 그 위험을 없앤다.

순서 고정 이유(README와 동일):
    수집 → 본문 백필 → Stage1 → Stage2(관련도) → Stage5(클러스터링) → Stage3(파급력) → Stage4(리랭커)
    Stage5가 Stage3보다 먼저 와야 cluster_size가 파급력 스코어에 반영된다.

한 단계가 실패해도(네트워크 오류 등) 나머지 단계는 계속 진행하고,
실패한 단계는 report의 "failed"에 남겨 무엇이 왜 실패했는지 알 수 있게 한다
— "안정화(로깅·실패 알림)"의 최소 구현. 알림 채널(Slack 등) 연동은 아직 없다.
"""

import logging
import time

from tech_monitoring.collectors.extract_content import backfill_content
from tech_monitoring.collectors.rss import collect_all
from tech_monitoring.filters.stage1_rules import apply_stage1
from tech_monitoring.filters.stage2_relevance import apply_stage2
from tech_monitoring.filters.stage3_impact import apply_stage3
from tech_monitoring.filters.stage4_rerank import rerank_top_candidates
from tech_monitoring.filters.stage5_cluster import apply_stage5

logger = logging.getLogger("tech_monitoring.pipeline")


def _stages() -> list[tuple[str, object]]:
    # 매 호출 시점의 모듈 전역을 참조 — 테스트에서 monkeypatch로 각 단계를 갈아끼울 수 있게 한다.
    return [
        ("collect", lambda: {"sources": collect_all()}),
        ("extract_content", backfill_content),
        ("stage1_rules", apply_stage1),
        ("stage2_relevance", apply_stage2),
        ("stage5_cluster", apply_stage5),
        ("stage3_impact", apply_stage3),
        ("stage4_rerank", rerank_top_candidates),
    ]


def run_pipeline() -> dict:
    results: dict[str, dict] = {}
    failed: list[str] = []

    for name, fn in _stages():
        started = time.monotonic()
        try:
            result = fn()
            results[name] = result
            logger.info("%s ok (%.1fs): %s", name, time.monotonic() - started, result)
        except Exception as exc:
            failed.append(name)
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            logger.error("%s FAILED (%.1fs): %s", name, time.monotonic() - started, exc)

    return {"stages": results, "failed": failed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = run_pipeline()
    if report["failed"]:
        logger.error("파이프라인 완료, 실패한 단계: %s", report["failed"])
    else:
        logger.info("파이프라인 전체 성공")
    print(report)
