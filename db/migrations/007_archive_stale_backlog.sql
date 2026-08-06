-- Phase 1.5: 최신성 로직 개선(개발계획서 v2.0 Phase 1.5 점검 항목)의 기존 데이터 정리.
--
-- 배경: OpenAI 피드가 최초 수집 시 2015년까지의 전체 아카이브 1,110건을 한 번에 내려줬다.
-- 이 과거 글들이 전부 AI 주제라 관련도 필터를 통과하면서 Stage2 후보 풀(CANDIDATE_TOP_N)을
-- 점령해, 정작 최근 이슈와 다른 소스가 밀려났다(통과분 300건 중 277건이 OpenAI).
--
-- 수집기에는 initial_backfill_days(기본 30일) 제한을 넣었고, 여기서는 이미 적재된
-- 소급분을 같은 기준으로 아카이브해 데이터셋을 정책과 일치시킨다.
-- 순서 주의: ①이전 기준 판정 되돌리기 → ②소급 윈도우 적용 → ③남은 건 재계산 초기화

-- ① Stage2(이전 관련도 기준)로 아카이브된 건을 일단 되돌린다
UPDATE articles
SET status = 'new'
WHERE status = 'archived'
  AND impact_signals->>'filtered_stage' = 'stage2';

-- ② 소급 윈도우 밖(30일 초과) 기사를 아카이브. ①로 되살아난 과거 아카이브분도 여기서 걸러진다.
--    Stage1 룰 컷(filtered_stage='stage1')은 status='archived'라 영향을 받지 않는다.
UPDATE articles
SET status = 'archived',
    impact_signals = impact_signals || '{"filtered_stage": "backfill_window", "reason": "older_than_initial_backfill_window"}'::jsonb
WHERE status = 'new'
  AND published_at IS NOT NULL
  AND published_at < now() - interval '30 days';

-- ③ 남은 후보는 축소된 풀에서 새 기준으로 다시 판정되도록 초기화
UPDATE articles
SET relevance_score = NULL,
    matched_by = NULL,
    impact_score = NULL,
    cluster_id = NULL,
    impact_signals = impact_signals - 'filtered_stage' - 'reason' - 'cluster_size'
                                    - 'cluster_member_count' - 'rerank_score'
                                    - 'aggregator_signal' - 'source_trust' - 'recency'
WHERE status = 'new';
