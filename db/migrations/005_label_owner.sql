-- 라벨을 누가 매겼는지 기록한다(2026-08-18).
--
-- 왜 지금 넣는가: 라벨이 한 건이라도 쌓인 뒤에는 **소급이 불가능한** 정보다.
-- 004의 UNIQUE (url_norm, fixed_keyword_id)는 "이 기사·이 시장의 라벨은
-- 하나"를 뜻해서, 여러 사람이 같은 DB에 라벨하면 나중에 누른 사람이 앞사람
-- 판단을 조용히 덮어쓴다(save_label이 ON CONFLICT DO UPDATE라 에러도 안 남).
-- 지금은 개인 도커 DB라 문제가 안 보이지만, 배포 계획은 앱만 각자 띄우고
-- DB는 공용(Supabase) 하나를 쓰는 구조다 — 그 순간부터 라벨이 섞인다.
--
-- 이 컬럼이 있으면 나중에 세 방식을 **같은 데이터로** 채점해서 고를 수 있다:
--   (1) 개인 모델   — WHERE labeled_by = '나'
--   (2) 통합 모델   — 전체 라벨(팀 공통 기준)
--   (3) 하이브리드  — 통합으로 학습 후 개인 라벨로 미세조정
-- 없이 시작하면 "누가 무엇을 판단했는지"를 복원할 방법이 없어 (1)·(3)이
-- 영구히 닫힌다. 그래서 라벨링을 본격적으로 시작하기 전에 먼저 넣는다.
--
-- 부수 효과로 **사람 간 판단 불일치를 측정**할 수 있다 — 같은 (url_norm,
-- fixed_keyword_id)에 서로 다른 label이 남으면 그게 곧 "팀의 기준이 실제로
-- 얼마나 다른가"다. 이 값이 낮으면 통합 모델이 맞고, 높으면 개인 모델이
-- 맞다는 근거가 된다(감으로 정하지 않기 위한 장치).
ALTER TABLE article_labels
    ADD COLUMN IF NOT EXISTS labeled_by TEXT NOT NULL DEFAULT 'local';

-- 004의 "기사×시장에 라벨 하나" 제약을 "기사×시장×사람에 라벨 하나"로 바꾼다.
-- 같은 사람이 같은 기사를 다시 누르면 여전히 덮어쓰기(UPSERT)이고,
-- 다른 사람의 판단은 별도 행으로 나란히 남는다.
ALTER TABLE article_labels
    DROP CONSTRAINT IF EXISTS article_labels_url_norm_fixed_keyword_id_key;

-- CONSTRAINT 대신 UNIQUE INDEX로 만드는 이유: Postgres의 ADD CONSTRAINT는
-- IF NOT EXISTS를 지원하지 않아 재실행 시 실패한다. ON CONFLICT는 제약이
-- 아니라 인덱스로도 추론되므로 save_label의 UPSERT는 그대로 동작한다.
CREATE UNIQUE INDEX IF NOT EXISTS article_labels_url_keyword_labeler_key
    ON article_labels (url_norm, fixed_keyword_id, labeled_by);

-- "내가 이 시장에서 라벨한 것" 조회용(라벨링 화면의 진행률·후보 제외, 학습
-- 데이터를 개인별로 뽑을 때). 위 UNIQUE 인덱스는 url_norm이 선두라 이
-- 질의엔 쓰이지 않는다.
CREATE INDEX IF NOT EXISTS idx_article_labels_labeler
    ON article_labels (labeled_by, fixed_keyword_id, label);
