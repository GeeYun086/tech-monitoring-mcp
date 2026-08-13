-- Phase 1.5: 8/6 회의 반영 (PRD v2.0 / 기술설계서 v2.0)
-- ① importance(주관 중요도) → impact(파급력) 리네이밍
--    설계서 §3: "importance(주관 중요도) 필드 없음 — 담당자 판단 영역.
--    시스템은 관련도 + 파급력만 산정."
-- ② 모니터링 대상 = AX 시장 전체 · 키워드 미지정
-- ③ 키워드 의존 수집 소스 비활성화

-- RENAME COLUMN에는 IF EXISTS가 없어 재실행 시 깨지므로 존재 여부를 확인하고 수행
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'articles' AND column_name = 'importance_score') THEN
        ALTER TABLE articles RENAME COLUMN importance_score TO impact_score;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'articles' AND column_name = 'importance_signals') THEN
        ALTER TABLE articles RENAME COLUMN importance_signals TO impact_signals;
    END IF;
END $$;

-- topics: 키워드 목록이 아니라 "AX 시장" 의미 서술을 임베딩해 넓게 판단(설계서 §5).
-- description이 관련도 필터의 의미 기준점 = 가장 중요한 튜닝 노브.
ALTER TABLE topics ADD COLUMN IF NOT EXISTS description TEXT;

-- 004에서 시딩된 키워드 기반 플레이스홀더 제거(마이그레이션은 매번 전체 재적용되므로 idempotent 유지)
DELETE FROM topics WHERE name = 'placeholder-AX';

INSERT INTO topics (name, keywords, description) VALUES (
    'AX 시장 전체',
    '{}',
    'AX(AI Transformation, AI 전환) 시장 전반. 기업과 조직이 인공지능을 도입·활용·내재화하는 흐름 전체를 다룬다. '
    'AI 시스템 구축과 도입 사례, AI 역량 평가와 진단, AI 교육과 인재 양성, 생성형 AI·LLM·AI 에이전트의 기업 적용, '
    'AI 개발 도구와 플랫폼, AI 인프라와 모델 생태계, AI 관련 투자·인수·규제·정책 변화, '
    'AI가 산업과 일하는 방식에 미치는 구조적 변화를 포함한다.'
) ON CONFLICT (name) DO UPDATE
    SET keywords = EXCLUDED.keywords,
        description = EXCLUDED.description;

-- description 기준으로 다시 임베딩되도록 초기화 (Stage2가 embedding IS NULL인 주제를 채움)
UPDATE topics SET embedding = NULL WHERE embedding IS NOT NULL;

-- 키워드 미지정이면 키워드 검색 수집기(HN Algolia·Naver)는 질의어가 없어 동작 불가.
-- HN은 이미 hnrss frontpage(points>=100) 애그리게이터 피드로 커버되고,
-- 설계서 §4가 "국내 한정하지 않음 · 큐레이션 중심"이므로 Naver 검색은 보류.
UPDATE sources SET active = FALSE WHERE name IN ('Hacker News (Algolia)', 'Naver News');
