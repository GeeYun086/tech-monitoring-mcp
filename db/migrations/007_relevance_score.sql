-- 관련도 판단에 확률(점수)을 함께 남긴다(2026-08-19).
--
-- 지금까지 분류기는 확률을 계산해놓고 0.5로 잘라 is_relevant(BOOLEAN)만
-- 저장했다. 그래서 (1) 화면은 최신순으로만 정렬됐고(알짜가 위로 오지 않음),
-- (2) 자르는 순간 분류기가 틀리면 기사가 목록에서 사라졌다 — Gemini가 막히면
-- 그 주 관련 기사가 0건이 되던 문제와 같은 종류의 사고다.
--
-- 그래서 두 가지를 함께 바꾼다:
--   - 확률을 score로 저장한다. 화면은 이 값으로 **정렬만** 하고 잘라내지
--     않는다(대시보드의 "top20으로 자르지 않는다" 원칙과 같은 방향).
--     is_relevant는 임계값 판정 결과로 남겨둔다 — 지표 계산과 기존 조회
--     (fetch_relevant_articles)가 쓰고 있다.
--   - 판단 행을 관련 있는 것만이 아니라 **그 시장의 모든 기사**에 대해
--     남긴다. 순위를 매기려면 낮은 점수도 있어야 한다.
--
-- NULL을 허용하는 이유: Gemini 판단(폴백 경로)은 확률을 주지 않는다 —
-- "관련 있음/없음"만 답한다. 그때는 score가 비고, 화면은 NULLS LAST로
-- 밀어낸 뒤 최신순으로 정렬한다. 즉 score의 유무가 "이 판단이 분류기에서
-- 나왔는지"를 그대로 알려준다.
ALTER TABLE article_keyword_relevance
    ADD COLUMN IF NOT EXISTS score REAL;

-- 화면 정렬(시장별 점수 내림차순)용. 기존 부분 인덱스는 is_relevant인 것만
-- 담고 있어서, 잘라내지 않고 전체를 정렬하는 이 질의에는 쓰이지 않는다.
CREATE INDEX IF NOT EXISTS idx_article_keyword_relevance_score
    ON article_keyword_relevance (run_id, fixed_keyword_id, score DESC);
