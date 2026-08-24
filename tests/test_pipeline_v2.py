"""pipeline_v2.py 테스트 — v1 tests/test_pipeline.py와 같은 패턴(모든 단계를
스텁으로 교체, 순서·실패 격리 검증). _start_run/_finish_run도 스텁으로 바꿔
실제 DB 연결 없이 오케스트레이션 로직만 검증한다.

merge_keywords 단계는 2026-08-24에 파이프라인에서 뺐다(pipeline_v2.py 헤더
참고) — 이 파일의 스텁도 남은 두 단계(collect, judge_relevance) 기준으로
맞춘다."""

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
    monkeypatch.setattr(pipeline_v2, "_judge_relevance", make_stage("judge_relevance"))
    finish_calls = []
    monkeypatch.setattr(pipeline_v2, "_finish_run", lambda run_id, failed: finish_calls.append((run_id, failed)))
    return finish_calls


def test_runs_stages_in_fixed_order(monkeypatch):
    """수집이 끝나야 그 주 collected_articles가 확정되므로 collect가
    judge_relevance보다 먼저 와야 한다."""
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls)

    report = pipeline_v2.run_pipeline()

    assert calls == [("collect", 99), ("judge_relevance", 99)]
    assert report["run_id"] == 99
    assert report["failed"] == []
    assert finish_calls == [(99, [])]


def test_one_stage_failing_does_not_stop_the_rest(monkeypatch):
    """수집이 실패해도(네트워크 오류 등) 판단 단계는 계속 진행돼야 한다."""
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls, fail_at="collect")

    report = pipeline_v2.run_pipeline()

    assert calls == [("collect", 99), ("judge_relevance", 99)]
    assert report["failed"] == ["collect"]
    assert "RuntimeError" in report["stages"]["collect"]["error"]
    assert report["stages"]["judge_relevance"] == {"ok": "judge_relevance"}
    assert finish_calls == [(99, ["collect"])]


def test_finish_run_reports_failure_with_correct_run_id(monkeypatch):
    calls = []
    finish_calls = _patch_all_stages(monkeypatch, calls, fail_at="judge_relevance")

    pipeline_v2.run_pipeline()

    assert finish_calls == [(99, ["judge_relevance"])]


def test_run_pipeline_returns_run_id_from_start_run(monkeypatch):
    monkeypatch.setattr(pipeline_v2, "_start_run", lambda: (7, _PLAN))
    monkeypatch.setattr(pipeline_v2, "_collect", lambda run_id, weeks=None: {"ok": True})
    monkeypatch.setattr(pipeline_v2, "_judge_relevance", lambda run_id: {"ok": True})
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
    monkeypatch.setattr(pipeline_v2, "_judge_relevance", lambda run_id: {"results": []})
    finish_calls = []
    monkeypatch.setattr(pipeline_v2, "_finish_run", lambda run_id, failed: finish_calls.append((run_id, failed)))

    report = pipeline_v2.run_pipeline()

    assert report["failed"] == ["collect"]
    assert finish_calls == [(99, ["collect"])]


def test_report_marks_failure_so_ci_can_see_it(monkeypatch):
    """자동 실행(GitHub Actions)은 로그를 사람이 안 보므로 종료 코드가 유일한
    신호다(작업 9). __main__이 report["failed"]로 종료 코드를 정하므로, 실패가
    거기에 남는지가 곧 "CI가 빨간불이 되는가"다."""
    monkeypatch.setattr(pipeline_v2, "_start_run", lambda: (99, _PLAN))
    monkeypatch.setattr(pipeline_v2, "_collect", lambda run_id, weeks=None: {"results": [
        {"source": "AI타임스", "fetched": 0, "inserted": 0, "error": "ReadTimeout"},
    ]})
    monkeypatch.setattr(pipeline_v2, "_judge_relevance", lambda run_id: {"results": []})
    monkeypatch.setattr(pipeline_v2, "_finish_run", lambda run_id, failed: None)

    assert pipeline_v2.run_pipeline()["failed"] == ["collect"]


def test_clean_run_reports_no_failure(monkeypatch):
    """오탐 방지 — 정상 주에 CI가 빨간불이면 아무도 안 본다."""
    monkeypatch.setattr(pipeline_v2, "_start_run", lambda: (99, _PLAN))
    monkeypatch.setattr(pipeline_v2, "_collect", lambda run_id, weeks=None: {"results": [
        {"source": "AI타임스", "fetched": 12, "inserted": 12, "error": None},
    ]})
    monkeypatch.setattr(pipeline_v2, "_judge_relevance", lambda run_id: {"results": [
        {"fixed_keyword": "교육", "judged": 12, "relevant": 5, "error": None},
    ]})
    monkeypatch.setattr(pipeline_v2, "_finish_run", lambda run_id, failed: None)

    assert pipeline_v2.run_pipeline()["failed"] == []
