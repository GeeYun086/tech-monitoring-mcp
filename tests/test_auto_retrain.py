"""auto_retrain.py 테스트 — v1 tests/test_pipeline.py·test_pipeline_v2.py와
같은 패턴(무거운 부분을 스텁으로 교체, 오케스트레이션 로직만 검증).
build_model·judge_all은 실제 임베딩·DB를 타므로 monkeypatch로 갈아끼운다."""

from tech_monitoring import auto_retrain


class _SpyCursor:
    """pipeline_state 하나만 흉내낸다 — db/weekly_run.py 테스트의
    is_bootstrapped/mark_bootstrapped 패턴과 같다."""

    def __init__(self, state: dict):
        self._state = state
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=()):
        if "SELECT value FROM pipeline_state" in query:
            (key,) = params
            value = self._state.get(key)
            self._rows = [(value,)] if value is not None else []
        elif "INSERT INTO pipeline_state" in query:
            key, value = params
            self._state[key] = value
        else:
            raise AssertionError(f"스텁이 모르는 질의: {query}")

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _SpyConn:
    def __init__(self, state: dict | None = None):
        self.state = state if state is not None else {}

    def cursor(self):
        return _SpyCursor(self.state)


def _labels(n: int) -> list[dict]:
    return [{"id": i} for i in range(n)]


def test_does_nothing_below_the_threshold(monkeypatch):
    """문턱(every)을 안 넘었으면 build_model을 아예 부르지 않아야 한다 —
    임베딩 인코딩은 비싸서 라벨 한 건마다 돌면 안 된다."""
    conn = _SpyConn()
    monkeypatch.setattr(auto_retrain.labeling, "fetch_all_labels", lambda c, **_kw: _labels(3))
    called = []
    monkeypatch.setattr(auto_retrain, "build_model", lambda labels: called.append(labels) or None)

    result = auto_retrain.maybe_retrain(conn, every=5)

    assert result is None
    assert called == []


def test_retrains_once_the_threshold_is_crossed(monkeypatch):
    conn = _SpyConn()
    monkeypatch.setattr(auto_retrain.labeling, "fetch_all_labels", lambda c, **_kw: _labels(5))
    monkeypatch.setattr(
        auto_retrain, "build_model",
        lambda labels: {"estimator": "est", "method": "tfidf", "metrics": {"f1": 0.8}},
    )
    monkeypatch.setattr(auto_retrain, "get_latest_run", lambda c: {"id": 42})
    judged_calls = []
    monkeypatch.setattr(
        auto_retrain, "judge_all",
        lambda c, run_id, bundle: judged_calls.append((run_id, bundle)) or [{"judged": 5}],
    )

    result = auto_retrain.maybe_retrain(conn, every=5)

    assert result == {
        "trained": True, "method": "tfidf", "metrics": {"f1": 0.8}, "judged": [{"judged": 5}],
    }
    assert judged_calls == [(42, {"estimator": "est", "method": "tfidf", "metrics": {"f1": 0.8}})]


def test_records_attempt_even_when_baseline_not_beaten(monkeypatch):
    """build_model이 None을 돌려줘도(찍기 기준선을 못 넘김) 카운터는 갱신해야
    한다 — 안 그러면 그다음 라벨 한 건마다 매번 재시도(비싼 인코딩 포함)한다."""
    conn = _SpyConn()
    monkeypatch.setattr(auto_retrain.labeling, "fetch_all_labels", lambda c, **_kw: _labels(5))
    monkeypatch.setattr(auto_retrain, "build_model", lambda labels: None)
    judge_called = []
    monkeypatch.setattr(auto_retrain, "judge_all", lambda *a, **k: judge_called.append(1))

    result = auto_retrain.maybe_retrain(conn, every=5)

    assert result == {"trained": False}
    assert judge_called == []
    assert conn.state[auto_retrain._LAST_RETRAIN_KEY] == "5"


def test_does_not_retrain_again_until_another_full_batch_accumulates(monkeypatch):
    """5건마다 한 번 — 재학습 직후 라벨이 8건이면 다음 시도는 13건부터다."""
    conn = _SpyConn(state={auto_retrain._LAST_RETRAIN_KEY: "5"})
    monkeypatch.setattr(auto_retrain.labeling, "fetch_all_labels", lambda c, **_kw: _labels(8))
    called = []
    monkeypatch.setattr(auto_retrain, "build_model", lambda labels: called.append(labels) or None)

    result = auto_retrain.maybe_retrain(conn, every=5)

    assert result is None
    assert called == []


def test_skips_judging_when_no_run_exists_yet(monkeypatch):
    """수집이 아직 한 번도 안 돈 상태(run 없음)에서도 재학습 자체는 성공할 수
    있다 — 그때는 재판단만 건너뛴다."""
    conn = _SpyConn()
    monkeypatch.setattr(auto_retrain.labeling, "fetch_all_labels", lambda c, **_kw: _labels(5))
    monkeypatch.setattr(
        auto_retrain, "build_model",
        lambda labels: {"estimator": "est", "method": "tfidf", "metrics": {"f1": 0.8}},
    )
    monkeypatch.setattr(auto_retrain, "get_latest_run", lambda c: None)
    judge_called = []
    monkeypatch.setattr(auto_retrain, "judge_all", lambda *a, **k: judge_called.append(1))

    result = auto_retrain.maybe_retrain(conn, every=5)

    assert result["trained"] is True
    assert result["judged"] == []
    assert judge_called == []


def test_uses_the_module_default_threshold_when_not_given(monkeypatch):
    conn = _SpyConn()
    monkeypatch.setattr(auto_retrain.labeling, "fetch_all_labels",
                         lambda c, **_kw: _labels(auto_retrain.RETRAIN_EVERY_N_LABELS - 1))
    called = []
    monkeypatch.setattr(auto_retrain, "build_model", lambda labels: called.append(labels) or None)

    assert auto_retrain.maybe_retrain(conn) is None
    assert called == []
