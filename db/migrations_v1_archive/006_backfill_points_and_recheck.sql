-- Phase 1.5 후속 데이터 정리 (005의 사양 변경에 따른 1회성 재평가)

-- ① HN points 백필: 기존 수집분은 impact_signals에 points가 없지만,
--    hnrss 본문(summary)에 "Points: N"이 그대로 남아 있어 파싱해 채울 수 있다.
UPDATE articles
SET impact_signals = impact_signals || jsonb_build_object(
        'hn_points', (substring(summary from 'Points:\s*(\d+)'))::int
    )
WHERE summary ~ 'Points:\s*\d+'
  AND NOT (impact_signals ? 'hn_points');

-- ② Stage2에서 걸러진 기사 재평가:
--    관련도 기준이 "키워드 플레이스홀더" → "AX 시장 의미 서술"로 바뀌었으므로
--    이전 기준으로 아카이브된 건은 되돌려 새 기준으로 다시 판정한다.
--    (Stage1 룰 컷은 v2.0에서도 유효하므로 그대로 둔다)
UPDATE articles
SET status = 'new',
    relevance_score = NULL,
    matched_by = NULL,
    impact_signals = impact_signals - 'filtered_stage' - 'reason'
WHERE status = 'archived'
  AND impact_signals->>'filtered_stage' = 'stage2';

-- ③ 파급력·클러스터는 새 기준으로 다시 계산되도록 초기화
UPDATE articles
SET impact_score = NULL,
    cluster_id = NULL,
    impact_signals = impact_signals - 'cluster_size' - 'cluster_member_count'
                                    - 'rerank_score' - 'sentiment' - 'issue_type'
                                    - 'aggregator_signal' - 'source_trust' - 'recency'
WHERE status = 'new';
