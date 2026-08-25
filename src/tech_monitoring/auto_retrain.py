"""라벨이 쌓일 때마다 분류기를 자동으로 다시 학습하고 이번 주 기사 순위를
갱신한다(2026-08-24) — 🏷️ 라벨링 탭(한 번에 기사 하나씩 강제 순회)을 없애고
시장 탭의 기사 목록에 인라인 👍/👎 버튼을 붙이면서, 성능 탭의 "성능
측정하기" 버튼을 수동으로 눌러야 했던 것도 함께 자동화했다.

**클릭마다 재학습하지 않는다.** relevance_model.py 모듈 docstring대로
임베딩 모델 로드에만 수십 초 걸린다 — 버튼 하나 누를 때마다 그 지연을
물리면 좋아요/싫어요가 무거운 액션이 돼버린다. 대신 라벨이 RETRAIN_EVERY_N
_LABELS건 새로 쌓일 때마다 한 번만 돈다.

마지막으로 재학습을 시도한 시점의 전체 라벨 수를 pipeline_state(주간
TRUNCATE 대상이 아님, 008 마이그레이션)에 남겨 다음 판단 기준으로 삼는다 —
db/weekly_run.py의 mark_bootstrapped/is_bootstrapped와 같은 패턴이다.
**시도했다는 사실 자체를 기록**하는 이유: 라벨이 아직 찍기 기준선을 못
넘어 build_model이 None을 돌려줘도 카운터는 갱신해야, 그 다음 라벨 한 건마다
매번 재시도(비싼 임베딩 인코딩 포함)하는 걸 막을 수 있다.

**labeling.ALL_LABELERS로 부르는 이유(2026-08-24, 익명 좋아요 개수 집계
도입)**: 인라인 버튼이 세션마다 무작위 labeled_by를 새로 발급하므로,
labeled_by 기본값(설정값 하나)으로 좁혀 세는 예전 방식으로는 새로 쌓이는
라벨이 전부 안 걸린다 — 그러면 항상 0건으로 보여 재학습이 영원히 안 돈다.
"""

from tech_monitoring import labeling
from tech_monitoring.analysis.relevance_filter import judge_all
from tech_monitoring.dashboard_queries import get_latest_run
from tech_monitoring.relevance_model import build_model

# 라벨 몇 건마다 재학습을 시도할지. 너무 작으면 클릭할 때마다 비싼 임베딩
# 인코딩이 자주 돌고, 너무 크면 반영이 굼떠 보인다 — 처음엔 5로 시작하고
# 실사용 체감을 보고 조정한다.
RETRAIN_EVERY_N_LABELS = 5

_LAST_RETRAIN_KEY = "last_auto_retrain_label_count"


def _get_last_retrain_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM pipeline_state WHERE key = %s", (_LAST_RETRAIN_KEY,))
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _mark_retrained(conn, label_count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pipeline_state (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (_LAST_RETRAIN_KEY, str(label_count)),
        )


def maybe_retrain(conn, *, every: int = RETRAIN_EVERY_N_LABELS) -> dict | None:
    """라벨을 저장/취소한 직후 호출한다.

    문턱(every)을 안 넘었으면 아무 것도 안 하고 None을 돌려준다(호출부가
    "이번엔 그냥 저장만 됐다"로 처리하면 된다). 넘었으면 전체 라벨로
    재학습하고, 성공했으면(찍기 기준선을 넘겼으면) 이번 주 기사도 새
    점수로 다시 매긴 뒤 결과를 돌려준다 — 성능 탭의 "성능 측정하기" 버튼이
    하던 일과 정확히 같다(_render_performance_tab 참고).

    반환값의 "trained"가 False면 재학습 자체는 시도했지만 찍기 기준선을
    못 넘겨 모델을 쓰지 않았다는 뜻이다(build_model이 None을 돌려준
    경우) — 이 경우에도 카운터는 갱신되므로 다음 시도는 또 every건 뒤다.
    """
    labels = labeling.fetch_all_labels(conn, labeled_by=labeling.ALL_LABELERS)
    last = _get_last_retrain_count(conn)
    if len(labels) - last < every:
        return None

    bundle = build_model(labels)
    _mark_retrained(conn, len(labels))
    if bundle is None:
        return {"trained": False}

    run = get_latest_run(conn)
    judged = judge_all(conn, run["id"], bundle=bundle) if run is not None else []
    return {
        "trained": True,
        "method": bundle["method"],
        "metrics": bundle["metrics"],
        "judged": judged,
    }
