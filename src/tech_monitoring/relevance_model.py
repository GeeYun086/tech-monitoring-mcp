"""사람이 매긴 라벨(labeling.py)로 관련도 분류기를 학습하고 성능을 채점한다.

목적은 analysis/relevance_filter.py의 Gemini 호출을 이걸로 갈아끼우는 것이다.
API 호출이 0이므로 429·크레딧 문제에서 완전히 자유롭다.

dashboard_queries.py와 같은 원칙: **계산은 여기서 끝내고 화면/스크립트는
결과를 받아 보여주기만 한다.** Streamlit에도 DB에도 의존하지 않는다(라벨
리스트를 인자로 받는다) — 그래서 fake 데이터로 그대로 테스트된다.

## 두 가지 방식을 나란히 재는 이유

라벨이 수백 건 규모라 "무거운 모델이 항상 낫다"가 성립하지 않는다. 그래서
같은 조건에서 둘을 채점하고 이긴 쪽을 저장한다.

  - tfidf     : 문자 n-gram TF-IDF + 로지스틱 회귀. 모델 다운로드가 없고
                학습이 수십 ms다. 한국어·영어가 섞인 짧은 텍스트에서 문자
                n-gram은 형태소 분석 없이도 잘 먹는다.
  - embedding : 다국어 문장 임베딩(paraphrase-multilingual-MiniLM-L12-v2,
                384차원) + 로지스틱 회귀. 처음 보는 표현에 강하다. 모델이
                이미 로컬 캐시에 있어 추가 설치·다운로드가 필요 없다.

## 평가에서 반드시 지키는 것

**(1) 그룹 단위로 fold를 나눈다.** 무작위로 쪼개면 같은 기사가 학습·검증
양쪽에 들어가 점수가 부풀려진다 — 실측상 라벨 후보 262건 중 56개 URL이
여러 고정 키워드에 동시에 걸려 있어 실제로 일어나는 누수다. 라벨이 두 주
이상 쌓이면 아예 주차 단위로 나눠 "다음 주 기사에도 통하는가"를 직접 재고,
아직 한 주뿐이면 같은 기사(url_norm)가 갈리지 않게만 막는다(build_groups).

**(2) 항상 baseline과 함께 본다.** 후보가 이미 검색을 통과한 기사들이라
"도움됨"으로 쏠리기 쉬운데, 그러면 **무조건 도움됨이라고만 답하는 분류기도
정확도가 높게 나온다**. 그 쏠린 정확도(majority_accuracy)를 같이 내서,
분류기가 그보다 얼마나 나은지로 읽게 한다. "정확도 85%"는 의미가 없고
"찍기 62% → 분류기 85%"라야 의미가 있다.

**(3) 순위 지표를 함께 낸다.** 실제 사용은 "이 시장 기사 상위 N건"을 보는
형태라 Precision@K·NDCG@K가 체감에 가깝다. 순위는 시장(고정 키워드)별로
따로 매긴다 — 시장이 다른 기사끼리 한 줄로 세우는 건 의미가 없다.

    ./.venv/Scripts/python.exe scripts/train_relevance_classifier.py
"""

from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import make_pipeline

from tech_monitoring.labeling import LABEL_RELEVANT

# 이미 로컬 HF 캐시에 있는 다국어 모델(v1 임베딩 단계에서 받아둔 것 — 추가
# 다운로드 없이 오프라인으로 로드된다). 한국어 기사(AI타임스)와 영어 기사
# (TechCrunch)가 섞여 있어 다국어 모델이어야 한다.
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

METHODS = ("tfidf", "embedding")

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "relevance_classifier.joblib"

# 학습을 시도할 최소 라벨 수. 이보다 적으면 fold를 나눠도 검증 조각이 몇 건뿐이라
# 점수가 요동쳐서 숫자를 믿을 수 없다.
MIN_LABELS_TO_TRAIN = 30

# 순위 지표를 볼 상위 몇 건. 실사용에서 한 시장의 주간 기사를 훑는 분량에 맞춘다.
DEFAULT_K = 10


def build_text(row: dict) -> str:
    """분류기 입력 문자열.

    **고정 키워드를 반드시 앞에 붙인다** — 관련도는 기사만의 속성이 아니라
    "기사 × 시장" 쌍의 속성이라(004 마이그레이션 헤더 (3)), 키워드가 빠지면
    같은 기사에 붙은 서로 다른 정답이 모순 데이터가 된다.
    """
    parts = [row["fixed_keyword"], "[SEP]", row["title"] or ""]
    if row.get("snippet"):
        parts.append(row["snippet"])
    return " ".join(parts)


def build_xy(labels: list[dict]) -> tuple[list[str], np.ndarray]:
    texts = [build_text(row) for row in labels]
    y = np.array([1 if row["label"] == LABEL_RELEVANT else 0 for row in labels])
    return texts, y


def build_groups(labels: list[dict]) -> tuple[np.ndarray, str]:
    """fold를 나눌 그룹 키와 그 종류를 돌려준다(모듈 docstring (1) 참고).

    주차가 둘 이상이면 주차 단위 — 학습에 안 쓴 주의 기사로 채점하므로
    "다음 주에도 통하는가"를 직접 재는 가장 엄격한 방식이다.
    한 주뿐이면 그렇게 나눌 수가 없으니, 최소한 같은 기사(url_norm)가
    학습·검증에 갈리는 누수만 막는다.
    """
    weeks = {row["period_start"] for row in labels}
    if len(weeks) >= 2:
        return np.array([str(row["period_start"]) for row in labels]), "주차"
    return np.array([row["url_norm"] for row in labels]), "기사"


def _make_estimator(method: str):
    # class_weight="balanced": 라벨이 한쪽으로 쏠려도 소수 클래스를 무시하고
    # 다수 클래스만 찍는 분류기로 수렴하지 않게 한다.
    classifier = LogisticRegression(max_iter=1000, class_weight="balanced")
    if method == "tfidf":
        # char_wb n-gram: 한국어는 형태소 분석기 없이, 영어는 어간 처리 없이
        # 같은 방식으로 다룰 수 있다. TF-IDF는 fold 안에서 다시 학습돼야
        # 검증 데이터의 어휘가 새어들지 않으므로 파이프라인으로 묶는다.
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=2, sublinear_tf=True)
        return make_pipeline(vectorizer, classifier)
    return classifier


def encode_texts(texts: list[str]):
    """문장 임베딩. 라벨을 보지 않는 변환이라 fold 밖에서 한 번에 계산해도
    누수가 아니다(TF-IDF와 달리 학습 데이터로 어휘를 만들지 않는다)."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return model.encode(texts, show_progress_bar=False)


def precision_at_k(relevance_sorted: np.ndarray, k: int) -> float:
    top = relevance_sorted[:k]
    return float(top.sum() / len(top)) if len(top) else 0.0


def ndcg_at_k(relevance_sorted: np.ndarray, k: int) -> float:
    """상위 K에 정답이 얼마나 위쪽으로 몰렸는지. 1.0이면 완벽한 순서."""
    def dcg(rel):
        return float(np.sum(rel / np.log2(np.arange(2, len(rel) + 2))))

    actual = dcg(relevance_sorted[:k])
    ideal = dcg(np.sort(relevance_sorted)[::-1][:k])
    return actual / ideal if ideal > 0 else 0.0


def ranking_metrics(labels: list[dict], y: np.ndarray, scores: np.ndarray, k: int = DEFAULT_K) -> dict:
    """시장(고정 키워드)별로 따로 순위를 매겨 평균낸다 — 시장이 다른 기사끼리
    한 줄로 세우는 건 실사용과 맞지 않는다."""
    per_market = {}
    for i, row in enumerate(labels):
        per_market.setdefault(row["fixed_keyword"], []).append((scores[i], y[i]))

    precisions, ndcgs = [], []
    for pairs in per_market.values():
        if len(pairs) < 2:
            continue
        ordered = np.array([rel for _score, rel in sorted(pairs, key=lambda p: -p[0])])
        precisions.append(precision_at_k(ordered, k))
        ndcgs.append(ndcg_at_k(ordered, k))

    if not precisions:
        return {}
    return {f"precision_at_{k}": float(np.mean(precisions)), f"ndcg_at_{k}": float(np.mean(ndcgs))}


def _safe_auc(y: np.ndarray, scores: np.ndarray) -> float | None:
    """한 시장의 라벨이 전부 같은 클래스면 AUC는 정의되지 않는다 — 0으로
    적으면 "성능이 나쁘다"로 오해되므로 None으로 비워 둔다."""
    if len(set(y)) < 2:
        return None
    return float(roc_auc_score(y, scores))


def per_market_metrics(
    labels: list[dict], y: np.ndarray, scores: np.ndarray, k: int = DEFAULT_K,
) -> list[dict]:
    """시장(고정 키워드)별 성능 분해.

    전체 통합 점수만 보면 "교육은 잘 맞히는데 비즈니스 실적은 못 맞힌다"가
    평균에 묻힌다. 어느 시장의 라벨을 더 모아야 하는지는 이 표에서만 보인다.

    입력은 evaluate()가 이미 계산해 둔 out-of-fold 확률이라, 시장별로 다시
    학습하지 않는다(임베딩 방식일 때 시장 수만큼 재학습하면 훨씬 느려진다).
    """
    buckets: dict[str, list[int]] = {}
    for i, row in enumerate(labels):
        buckets.setdefault(row["fixed_keyword"], []).append(i)

    rows = []
    for market, idx in sorted(buckets.items()):
        market_y = y[idx]
        market_scores = scores[idx]
        market_pred = (market_scores >= 0.5).astype(int)
        positive = int(market_y.sum())
        total = len(market_y)
        majority = max(positive, total - positive)

        ordered = np.array([rel for _s, rel in sorted(zip(market_scores, market_y), key=lambda p: -p[0])])
        rows.append({
            "fixed_keyword": market,
            "n_labels": total,
            "relevant": positive,
            "positive_rate": float(positive / total) if total else 0.0,
            "majority_accuracy": float(majority / total) if total else 0.0,
            "precision": float(precision_score(market_y, market_pred, zero_division=0)),
            "recall": float(recall_score(market_y, market_pred, zero_division=0)),
            "f1": float(f1_score(market_y, market_pred, zero_division=0)),
            "accuracy": float((market_pred == market_y).mean()),
            "auc": _safe_auc(market_y, market_scores),
            f"precision_at_{k}": precision_at_k(ordered, k),
            f"ndcg_at_{k}": ndcg_at_k(ordered, k),
        })
    return rows


def class_distribution(labels: list[dict]) -> dict:
    """학습 전에 반드시 봐야 하는 쏠림(모듈 docstring (2))."""
    _texts, y = build_xy(labels)
    positive = int(y.sum())
    total = len(y)
    majority = max(positive, total - positive)
    return {
        "total": total,
        "relevant": positive,
        "irrelevant": total - positive,
        "positive_rate": float(positive / total) if total else 0.0,
        # 무조건 다수 클래스로만 찍는 분류기의 정확도 — 우리 분류기는 이걸 넘겨야 의미가 있다.
        "majority_accuracy": float(majority / total) if total else 0.0,
    }


def evaluate(labels: list[dict], method: str = "tfidf", k: int = DEFAULT_K) -> dict:
    """그룹 교차검증으로 채점한다. 학습에 쓰이지 않은 조각에서 나온 예측만
    모아(out-of-fold) 지표를 계산하므로, 모든 라벨이 정확히 한 번씩 "처음 보는
    기사"로 채점된다.

    돌릴 수 없는 상태(라벨 부족·한쪽 클래스만 있음·그룹 부족)면 계산을 억지로
    하지 않고 reason에 이유를 담아 돌려준다 — 근거 없는 숫자를 화면에 띄우면
    안 되기 때문이다.
    """
    distribution = class_distribution(labels)
    result = {"method": method, "n_labels": len(labels), "distribution": distribution}

    if len(labels) < MIN_LABELS_TO_TRAIN:
        return {**result, "ok": False,
                "reason": f"라벨이 {len(labels)}건뿐입니다. 최소 {MIN_LABELS_TO_TRAIN}건은 필요합니다."}

    texts, y = build_xy(labels)
    if len(set(y)) < 2:
        only = "도움됨" if y[0] == 1 else "도움 안 됨"
        return {**result, "ok": False,
                "reason": f"라벨이 전부 '{only}'입니다. 두 종류가 모두 있어야 학습할 수 있습니다."}

    groups, group_kind = build_groups(labels)
    n_splits = min(5, len(set(groups)))
    if n_splits < 2:
        return {**result, "ok": False,
                "reason": f"{group_kind} 그룹이 하나뿐이라 학습·검증을 나눌 수 없습니다."}

    try:
        X = encode_texts(texts) if method == "embedding" else texts
    except ImportError:
        # sentence-transformers는 선택 설치(pyproject의 [embedding] extra) —
        # 없으면 이 방식만 건너뛰고 tfidf 결과는 그대로 낸다.
        return {**result, "ok": False,
                "reason": "sentence-transformers가 설치돼 있지 않습니다. "
                          '설치: pip install -e ".[embedding]"'}

    estimator = _make_estimator(method)

    probabilities = cross_val_predict(
        estimator, X, y,
        cv=GroupKFold(n_splits=n_splits), groups=groups, method="predict_proba",
    )[:, 1]
    predictions = (probabilities >= 0.5).astype(int)

    metrics = {
        "precision": float(precision_score(y, predictions, zero_division=0)),
        "recall": float(recall_score(y, predictions, zero_division=0)),
        "f1": float(f1_score(y, predictions, zero_division=0)),
        "accuracy": float((predictions == y).mean()),
        "auc": float(roc_auc_score(y, probabilities)),
        **ranking_metrics(labels, y, probabilities, k=k),
    }

    return {**result, "ok": True, "reason": None, "metrics": metrics,
            "per_market": per_market_metrics(labels, y, probabilities, k=k),
            "cv": {"group_kind": group_kind, "n_splits": n_splits, "n_groups": len(set(groups))}}


def evaluate_all(labels: list[dict], k: int = DEFAULT_K) -> list[dict]:
    """두 방식을 같은 조건으로 채점해 f1 내림차순으로 돌려준다(첫 번째가 승자)."""
    results = [evaluate(labels, method=method, k=k) for method in METHODS]
    return sorted(results, key=lambda r: r.get("metrics", {}).get("f1", -1), reverse=True)


def train_final_model(labels: list[dict], method: str):
    """채점이 끝난 뒤 전체 라벨로 다시 학습한 최종 모델. 평가는 이미 위에서
    out-of-fold로 끝냈으므로, 실제로 쓸 모델은 데이터를 한 건도 버리지 않는다."""
    texts, y = build_xy(labels)
    X = encode_texts(texts) if method == "embedding" else texts
    estimator = _make_estimator(method)
    estimator.fit(X, y)
    return estimator


def save_model(estimator, method: str, metrics: dict, path: Path = MODEL_PATH) -> Path:
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    # method를 같이 저장한다 — 불러올 때 임베딩이 필요한 모델인지 알아야 한다.
    joblib.dump({"estimator": estimator, "method": method, "metrics": metrics}, path)
    return path


def load_model(path: Path = MODEL_PATH) -> dict | None:
    import joblib

    if not path.exists():
        return None
    return joblib.load(path)


def predict_proba(bundle: dict, rows: list[dict]) -> np.ndarray:
    """저장된 모델로 "도움될 확률"을 매긴다. rows는 build_text가 읽을 수 있는
    모양(fixed_keyword/title/snippet)이면 된다 — 라벨 행이든 수집 기사든 같다."""
    texts = [build_text(row) for row in rows]
    X = encode_texts(texts) if bundle["method"] == "embedding" else texts
    return bundle["estimator"].predict_proba(X)[:, 1]
