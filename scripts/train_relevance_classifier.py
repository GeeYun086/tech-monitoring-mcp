"""라벨(article_labels)로 관련도 분류기를 학습하고 성능을 출력한다.

Streamlit "📈 성능" 탭과 **같은 함수(tech_monitoring.relevance_model)를 쓴다** —
화면에서 본 숫자와 터미널에서 본 숫자가 다르면 안 되기 때문이다. 여기는 CLI
표현만 담당한다(dashboard_queries/streamlit_app의 역할 분리와 같은 원칙).

두 방식(문자 n-gram TF-IDF / 다국어 문장 임베딩)을 같은 조건으로 채점하고,
이긴 쪽을 models/relevance_classifier.joblib에 저장한다. 이후
analysis/relevance_filter.py가 이 파일을 불러 Gemini 대신 쓴다.

    ./.venv/Scripts/python.exe scripts/train_relevance_classifier.py
    ./.venv/Scripts/python.exe scripts/train_relevance_classifier.py --no-save
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tech_monitoring import relevance_model as rm  # noqa: E402
from tech_monitoring.db.connection import get_connection  # noqa: E402
from tech_monitoring.labeling import fetch_all_labels  # noqa: E402

_METRIC_LABELS = {
    "precision": "Precision  (도움됨이라 한 것 중 실제 도움됨)",
    "recall": "Recall     (실제 도움됨 중 찾아낸 비율)",
    "f1": "F1         (위 둘의 균형)",
    "accuracy": "Accuracy   (전체 정답률)",
    "auc": "AUC        (순위를 매기는 능력, 0.5=찍기)",
}


def _print_distribution(distribution: dict) -> None:
    print("=" * 62)
    print("라벨 분포 (학습 전 반드시 확인)")
    print("=" * 62)
    print(f"  전체 {distribution['total']}건 — "
          f"도움됨 {distribution['relevant']} / 도움 안 됨 {distribution['irrelevant']}")
    print(f"  도움됨 비율      : {distribution['positive_rate']:.1%}")
    print(f"  찍기 기준선      : {distribution['majority_accuracy']:.1%} "
          "← 무조건 다수 쪽으로만 답하는 분류기의 정확도")
    if distribution["majority_accuracy"] >= 0.8:
        print("  ⚠️  한쪽으로 크게 쏠려 있습니다. 정확도만 보면 착시가 생기니")
        print("      Precision/Recall/AUC를 함께 보세요.")


def _print_result(result: dict, baseline: float) -> None:
    print(f"\n--- {result['method']} " + "-" * (58 - len(result["method"])))
    if not result["ok"]:
        print(f"  건너뜀: {result['reason']}")
        return

    cv = result["cv"]
    print(f"  교차검증: {cv['group_kind']} 단위 {cv['n_splits']}-fold "
          f"({cv['n_groups']}개 그룹) — 학습에 안 쓴 조각으로만 채점")
    metrics = result["metrics"]
    for key, label in _METRIC_LABELS.items():
        line = f"  {label:<48} {metrics[key]:.3f}"
        if key == "accuracy":
            line += f"   (찍기 {baseline:.3f})"
        print(line)

    ranking = {k: v for k, v in metrics.items() if k.startswith(("precision_at", "ndcg_at"))}
    if ranking:
        print("  [순위 지표 — 시장별로 따로 순위를 매겨 평균]")
        for key, value in ranking.items():
            print(f"  {key:<48} {value:.3f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="관련도 분류기 학습 + 성능 측정")
    parser.add_argument("--no-save", action="store_true", help="채점만 하고 모델 파일은 남기지 않는다")
    parser.add_argument("--k", type=int, default=rm.DEFAULT_K, help="순위 지표의 상위 K(기본 10)")
    args = parser.parse_args()

    conn = get_connection()
    try:
        labels = fetch_all_labels(conn)
    finally:
        conn.close()

    if not labels:
        print("라벨이 한 건도 없습니다.")
        print("Streamlit 대시보드의 '🏷️ 라벨링' 탭에서 먼저 라벨을 매겨주세요:")
        print("  ./.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py")
        return 1

    distribution = rm.class_distribution(labels)
    _print_distribution(distribution)

    print("\n" + "=" * 62)
    print("성능 (두 방식을 같은 조건으로 채점)")
    print("=" * 62)
    results = rm.evaluate_all(labels, k=args.k)
    for result in results:
        _print_result(result, distribution["majority_accuracy"])

    best = results[0]
    if not best.get("ok"):
        print("\n채점할 수 있는 방식이 없습니다 — 위 사유를 확인하세요.")
        return 1

    print("\n" + "=" * 62)
    print(f"승자: {best['method']} (F1 {best['metrics']['f1']:.3f})")

    # 기준선을 못 넘은 모델은 저장하지 않는다(2026-08-19 수정). 그전에는 경고만
    # 찍고 저장해서, 실측 30건 기준으로 찍기(0.567)보다 못한 모델(0.433)이 그대로
    # models/에 남았다 — 파이프라인은 파일이 있으면 무조건 집어 쓰므로 그 순간부터
    # 화면 순위가 무작위보다 나쁜 점수로 정렬된다. 화면(성능 탭)은 원래 이 경우
    # 저장하지 않았는데 CLI만 달랐다. 같은 규칙으로 맞춘다.
    if best["metrics"]["accuracy"] <= distribution["majority_accuracy"]:
        print(
            "⚠️  정확도가 찍기 기준선을 못 넘었습니다 — 라벨을 더 모아야 합니다.\n"
            "    모델을 저장하지 않았습니다(찍기보다 못한 순위로 화면을 정렬하게 되므로)."
        )
        return 1

    if args.no_save:
        print("--no-save 지정 — 모델을 저장하지 않았습니다.")
        return 0

    # 채점은 out-of-fold로 이미 끝났으므로, 실제로 쓸 모델은 전체 라벨로 학습한다.
    estimator = rm.train_final_model(labels, best["method"])
    path = rm.save_model(estimator, best["method"], best["metrics"])
    print(f"저장 완료: {path}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
