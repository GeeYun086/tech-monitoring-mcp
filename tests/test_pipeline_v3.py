"""pipeline_v3.py 테스트 — pipeline_v2.py와 같은 패턴(모든 단계를 스텁으로
교체, 순서·실패 격리 검증). _start_run/_finish_run도 스텁으로 바꿔 실제 DB
연결 없이 오케스트레이션 로직만 검증한다."""

from tech_monitoring import pipeline_v3


def _patch_all_stages(monkeypatch, calls, fail_at=None):
    def make_stage(name):
        def stage(run_id):
            calls.append((name, run_id))
            if name == fail_at:
                raise RuntimeError(f"{name} 네트워크 오류(가정)")
            return {"ok": name}

        return stage

    monkeypatch.setattr(pipeline_v3, "_start_run", lambda: 99)
    monkeypatch.setattr(pipeline_v3, "_collect", make_stage("collect"))
    monkeypatch.setattr(pipeline_v3, "_judge_relevance", make_stage("judge_relevance"))
    monkeypatch.setattr(pipeline_v3, "_merge_keywords", make_stage("merge_keywords"))
    finish_calls = []
    monkeypatch.setattr(pipeline_v3, "_finish_run", lambda run_id, failed: finish_calls.append((run_id, failed)))
    return finish_calls


def test_runs_stages_in_fixed_order(monkeypatch):
    """수집 → 관련도 판단 → 키워드 병합 순서가 지켜져야 한다(각 단계가 앞 단계 결과에 의존)."""
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls)

    report = pipeline_v3.run_pipeline()

    assert calls == [("collect", 99), ("judge_relevance", 99), ("merge_keywords", 99)]
    assert report["run_id"] == 99
    assert report["failed"] == []
    assert finish_calls == [(99, [])]


def test_one_stage_failing_does_not_stop_the_rest(monkeypatch):
    """수집이 실패해도(네트워크 오류 등) 뒤 단계가 계속 진행돼야 한다(실패 격리)."""
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls, fail_at="collect")

    report = pipeline_v3.run_pipeline()

    assert calls == [("collect", 99), ("judge_relevance", 99), ("merge_keywords", 99)]
    assert report["failed"] == ["collect"]
    assert "RuntimeError" in report["stages"]["collect"]["error"]
    assert report["stages"]["judge_relevance"] == {"ok": "judge_relevance"}
    assert report["stages"]["merge_keywords"] == {"ok": "merge_keywords"}
    assert finish_calls == [(99, ["collect"])]


def test_finish_run_reports_failure_with_correct_run_id(monkeypatch):
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls, fail_at="merge_keywords")

    pipeline_v3.run_pipeline()

    assert finish_calls == [(99, ["merge_keywords"])]


def test_run_pipeline_returns_run_id_from_start_run(monkeypatch):
    monkeypatch.setattr(pipeline_v3, "_start_run", lambda: 7)
    monkeypatch.setattr(pipeline_v3, "_collect", lambda run_id: {"ok": True})
    monkeypatch.setattr(pipeline_v3, "_judge_relevance", lambda run_id: {"ok": True})
    monkeypatch.setattr(pipeline_v3, "_merge_keywords", lambda run_id: {"ok": True})
    monkeypatch.setattr(pipeline_v3, "_finish_run", lambda run_id, failed: None)

    report = pipeline_v3.run_pipeline()

    assert report["run_id"] == 7


def test_start_run_does_not_reset_weekly_data(monkeypatch):
    """v2와 나란히 비교하기 위한 핵심 제약 — v3는 절대 reset_weekly_data를
    호출해서는 안 된다(호출하면 v2가 이미 수집한 이번 주 데이터가 지워짐)."""
    calls = []

    class _FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(pipeline_v3, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(pipeline_v3, "reset_weekly_data", lambda conn: calls.append("reset"), raising=False)
    monkeypatch.setattr(pipeline_v3, "start_weekly_run", lambda conn: 1)

    pipeline_v3._start_run()

    assert calls == []
