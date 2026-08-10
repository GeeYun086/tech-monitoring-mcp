from tech_monitoring import pipeline


def _patch_all_stages(monkeypatch, calls, fail_at=None):
    """모든 단계를 스텁으로 교체. fail_at에 해당하는 단계만 예외를 던진다."""

    def make_stage(name):
        def stage(*args, **kwargs):
            calls.append(name)
            if name == fail_at:
                raise RuntimeError(f"{name} 네트워크 오류(가정)")
            return {"ok": name}

        return stage

    monkeypatch.setattr(pipeline, "collect_all", make_stage("collect"))
    monkeypatch.setattr(pipeline, "_collect_geeknews_weekly", make_stage("collect_geeknews_weekly"))
    monkeypatch.setattr(pipeline, "backfill_content", make_stage("extract_content"))
    monkeypatch.setattr(pipeline, "apply_stage1", make_stage("stage1_rules"))
    monkeypatch.setattr(pipeline, "apply_stage2", make_stage("stage2_relevance"))
    monkeypatch.setattr(pipeline, "apply_stage2b", make_stage("stage2b_relevance_rerank"))
    monkeypatch.setattr(pipeline, "apply_stage5", make_stage("stage5_cluster"))
    monkeypatch.setattr(pipeline, "apply_stage3", make_stage("stage3_impact"))
    monkeypatch.setattr(pipeline, "rerank_top_candidates", make_stage("stage4_rerank"))


def test_runs_stages_in_fixed_order(monkeypatch):
    """README·설계서가 못박은 순서: 수집→본문백필→1→2→5(클러스터)→3(파급력)→4.
    Stage5가 Stage3보다 먼저 와야 cluster_size가 파급력 스코어에 반영된다."""
    calls = []
    _patch_all_stages(monkeypatch, calls)

    report = pipeline.run_pipeline()

    assert calls == [
        "collect",
        "collect_geeknews_weekly",
        "extract_content",
        "stage1_rules",
        "stage2_relevance",
        "stage2b_relevance_rerank",
        "stage5_cluster",
        "stage3_impact",
        "stage4_rerank",
    ]
    assert report["failed"] == []


def test_one_stage_failing_does_not_stop_the_rest(monkeypatch):
    """본문 백필 단계가 실패해도(네트워크 오류 등) 이후 단계는 계속 진행돼야 한다 —
    이게 빠져서 실제로 며칠간 본문 추출이 누락된 채 운영된 사고가 있었다."""
    calls = []
    _patch_all_stages(monkeypatch, calls, fail_at="extract_content")

    report = pipeline.run_pipeline()

    assert calls == [
        "collect",
        "collect_geeknews_weekly",
        "extract_content",
        "stage1_rules",
        "stage2_relevance",
        "stage2b_relevance_rerank",
        "stage5_cluster",
        "stage3_impact",
        "stage4_rerank",
    ]
    assert report["failed"] == ["extract_content"]
    assert "RuntimeError" in report["stages"]["extract_content"]["error"]
    assert report["stages"]["stage1_rules"] == {"ok": "stage1_rules"}
