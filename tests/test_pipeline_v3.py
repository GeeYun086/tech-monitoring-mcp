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


def test_stage_returning_item_errors_is_counted_as_failed(monkeypatch):
    """2026-08-18 회귀 방지 — Gemini 429로 관련도 판단이 전부 죽어도 예외는
    안 나고 각 항목 "error"에 사유만 담겨 돌아온다. 그걸 실패로 안 세면
    "관련 기사 0건"인 주가 completed로 조용히 마감된다."""
    monkeypatch.setattr(pipeline_v3, "_start_run", lambda: 99)
    monkeypatch.setattr(pipeline_v3, "_collect", lambda run_id: {"results": [
        {"source": "Techmeme", "fetched": 30, "inserted": 30, "error": None},
    ]})
    monkeypatch.setattr(pipeline_v3, "_judge_relevance", lambda run_id: {"results": [
        {"fixed_keyword": "AI 교육", "judged": 0, "relevant": 0, "error": "ClientError: 429"},
    ]})
    monkeypatch.setattr(pipeline_v3, "_merge_keywords", lambda run_id: {"results": []})
    finish_calls = []
    monkeypatch.setattr(pipeline_v3, "_finish_run", lambda run_id, failed: finish_calls.append((run_id, failed)))

    report = pipeline_v3.run_pipeline()

    assert report["failed"] == ["judge_relevance"]
    # 실패로 세더라도 결과 자체는 report에 그대로 남아야 한다(사유 추적용).
    assert report["stages"]["judge_relevance"]["results"][0]["error"] == "ClientError: 429"
    # weekly_runs를 'failed'로 마감시키는 경로까지 이어져야 대시보드에 드러난다.
    assert finish_calls == [(99, ["judge_relevance"])]


def test_partial_collector_failure_is_counted_as_failed(monkeypatch):
    """RSS 피드 하나만 죽어도 실패로 잡는다 — 조용히 넘어가 몇 주를 놓치는
    것보다 과하게 알리는 쪽이 낫다는 판단(pipeline_report.py 헤더 참고)."""
    monkeypatch.setattr(pipeline_v3, "_start_run", lambda: 99)
    monkeypatch.setattr(pipeline_v3, "_collect", lambda run_id: {"results": [
        {"source": "Techmeme", "fetched": 30, "inserted": 30, "error": None},
        {"source": "AI타임스", "fetched": 0, "inserted": 0, "error": "ReadTimeout"},
    ]})
    monkeypatch.setattr(pipeline_v3, "_judge_relevance", lambda run_id: {"results": []})
    monkeypatch.setattr(pipeline_v3, "_merge_keywords", lambda run_id: {"results": []})
    monkeypatch.setattr(pipeline_v3, "_finish_run", lambda run_id, failed: None)

    report = pipeline_v3.run_pipeline()

    assert report["failed"] == ["collect"]


def test_all_stages_clean_still_completes(monkeypatch):
    """error가 전부 None이면 예전처럼 성공으로 마감돼야 한다(오탐 방지)."""
    monkeypatch.setattr(pipeline_v3, "_start_run", lambda: 99)
    monkeypatch.setattr(pipeline_v3, "_collect", lambda run_id: {"results": [
        {"source": "Techmeme", "fetched": 30, "inserted": 30, "error": None},
    ]})
    monkeypatch.setattr(pipeline_v3, "_judge_relevance", lambda run_id: {"results": [
        {"fixed_keyword": "AI 교육", "judged": 30, "relevant": 8, "error": None},
    ]})
    monkeypatch.setattr(pipeline_v3, "_merge_keywords", lambda run_id: {"results": [
        {"fixed_keyword": "AI 교육", "groups": 12, "inserted": 12},
    ]})
    monkeypatch.setattr(pipeline_v3, "_finish_run", lambda run_id, failed: None)

    assert pipeline_v3.run_pipeline()["failed"] == []
