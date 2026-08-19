"""pipeline_v2.py 테스트 — v1 tests/test_pipeline.py와 같은 패턴(모든 단계를
스텁으로 교체, 순서·실패 격리 검증). _start_run/_finish_run도 스텁으로 바꿔
실제 DB 연결 없이 오케스트레이션 로직만 검증한다."""

from datetime import date

from tech_monitoring import pipeline_v2

# 평상시(최초 수집이 아님) 계획 — _start_run이 돌려주는 모양.
_PLAN = {"weeks": [(date(2026, 8, 10), date(2026, 8, 16))],
         "run_period": (date(2026, 8, 10), date(2026, 8, 16)), "bootstrap": False}


def _patch_all_stages(monkeypatch, calls, fail_at=None):
    def make_stage(name):
        def stage(run_id, weeks=None):
            calls.append((name, run_id))
            if name == fail_at:
                raise RuntimeError(f"{name} 네트워크 오류(가정)")
            return {"ok": name}

        return stage

    monkeypatch.setattr(pipeline_v2, "_start_run", lambda: (99, _PLAN))
    monkeypatch.setattr(pipeline_v2, "_collect", make_stage("collect"))
    monkeypatch.setattr(pipeline_v2, "_merge_keywords", make_stage("merge_keywords"))
    finish_calls = []
    monkeypatch.setattr(pipeline_v2, "_finish_run", lambda run_id, failed: finish_calls.append((run_id, failed)))
    return finish_calls


def test_runs_stages_in_fixed_order(monkeypatch):
    """수집이 끝나야 그 주 search_results가 확정되므로 collect가 merge_keywords보다 먼저 와야 한다."""
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls)

    report = pipeline_v2.run_pipeline()

    assert calls == [("collect", 99), ("merge_keywords", 99)]
    assert report["run_id"] == 99
    assert report["failed"] == []
    assert finish_calls == [(99, [])]


def test_one_stage_failing_does_not_stop_the_rest(monkeypatch):
    """수집이 실패해도(네트워크 오류 등) 병합 단계는 계속 진행돼야 한다."""
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls, fail_at="collect")

    report = pipeline_v2.run_pipeline()

    assert calls == [("collect", 99), ("merge_keywords", 99)]
    assert report["failed"] == ["collect"]
    assert "RuntimeError" in report["stages"]["collect"]["error"]
    assert report["stages"]["merge_keywords"] == {"ok": "merge_keywords"}
    assert finish_calls == [(99, ["collect"])]


def test_finish_run_reports_failure_with_correct_run_id(monkeypatch):
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls, fail_at="merge_keywords")

    pipeline_v2.run_pipeline()

    assert finish_calls == [(99, ["merge_keywords"])]


def test_run_pipeline_returns_run_id_from_start_run(monkeypatch):
    monkeypatch.setattr(pipeline_v2, "_start_run", lambda: (7, _PLAN))
    monkeypatch.setattr(pipeline_v2, "_collect", lambda run_id, weeks=None: {"ok": True})
    monkeypatch.setattr(pipeline_v2, "_merge_keywords", lambda run_id, weeks=None: {"ok": True})
    monkeypatch.setattr(pipeline_v2, "_finish_run", lambda run_id, failed: None)

    report = pipeline_v2.run_pipeline()

    assert report["run_id"] == 7


def test_stage_returning_item_errors_is_counted_as_failed(monkeypatch):
    """2026-08-18 회귀 방지 — TAVILY_API_KEY 미설정처럼 한 건도 못 가져온
    경우에도 collect_all은 예외 없이 "error"만 담아 리턴한다. 그걸 실패로
    안 세면 빈 주가 completed로 조용히 마감된다(v3와 같은 사고)."""
    monkeypatch.setattr(pipeline_v2, "_start_run", lambda: (99, _PLAN))
    monkeypatch.setattr(pipeline_v2, "_collect", lambda run_id, weeks=None: {"results": [
        {"fixed_keyword": None, "fetched": 0, "inserted": 0, "error": "TAVILY_API_KEY 미설정 — .env 확인"},
    ]})
    monkeypatch.setattr(pipeline_v2, "_merge_keywords", lambda run_id: {"results": []})
    finish_calls = []
    monkeypatch.setattr(pipeline_v2, "_finish_run", lambda run_id, failed: finish_calls.append((run_id, failed)))

    report = pipeline_v2.run_pipeline()

    assert report["failed"] == ["collect"]
    assert finish_calls == [(99, ["collect"])]
