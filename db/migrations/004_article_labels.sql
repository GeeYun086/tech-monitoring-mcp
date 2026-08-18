-- 사람이 직접 매긴 "이 기사가 이 시장과 관련 있는가" 라벨(2026-08-18).
--
-- 목적: 관련도 판단을 Gemini(analysis/relevance_filter.py)에서 떼어내
-- 로컬 분류기로 대체하기 위한 학습 데이터. 커피챗 조언(기사 선택/미선택
-- 행동 자체를 라벨로 삼아 분류기를 학습)을 그대로 따른다.
--
-- 설계에서 반드시 지켜야 하는 것 세 가지 — 전부 이 프로젝트의 기존 구조
-- 때문에 생기는 함정이라 하나라도 어기면 학습 데이터가 조용히 망가진다.
--
-- (1) **weekly_runs를 참조하지 않는다.** 다른 수집 테이블은 전부
--     "TRUNCATE weekly_runs RESTART IDENTITY CASCADE"(db/weekly_run.py의
--     reset_weekly_data)로 매주 통째로 지워진다. 라벨은 몇 주에 걸쳐 쌓아야
--     의미가 있는 학습 데이터라 여기 물리면 다음 주에 전부 사라진다.
--     fixed_keywords가 wipe를 피하는 것과 같은 이유·같은 방법이다.
--
-- (2) **원본 행 id를 중복 판정 키로 쓰지 않는다.** 위 TRUNCATE에는
--     RESTART IDENTITY가 붙어 있어 collected_articles.id / search_results.id
--     시퀀스가 매주 1부터 다시 시작한다. 즉 "이번 주 42번 기사"와 "다음 주
--     42번 기사"는 완전히 다른 글이다. 원본 id로 "이미 라벨함"을 판정하면
--     다음 주에 처음 보는 기사가 라벨된 것으로 잡혀 화면에서 사라진다.
--     그래서 판정 키는 주차와 무관하게 안정적인 **정규화된 URL**
--     (utils/url_normalize.normalize_url — utm/amp/m. 변형을 수렴시킴)이다.
--     원본 id는 다음 주면 가리키는 대상이 바뀌므로 아예 저장하지 않는다.
--
-- (3) **fixed_keyword_id가 필수다.** 관련도는 기사 자체의 속성이 아니라
--     "기사 × 고정 키워드" 쌍의 속성이다(article_keyword_relevance가 다대다인
--     이유와 동일 — 같은 기사가 "AI 교육"엔 알짜여도 "비즈니스 실적"엔
--     무관할 수 있다). 기사 단위로만 라벨하면 학습된 분류기가
--     relevance_filter.judge_keyword(고정 키워드별 판단) 자리를 대체할 수
--     없다. 학습 입력도 제목/요약만이 아니라 이 키워드를 함께 넣는다.
--
-- title/snippet 등을 스냅샷으로 복사해 두는 이유도 (1)과 같다 — 원본 행이
-- 다음 주 wipe로 사라져도 몇 주 뒤 재학습이 가능해야 한다. 이 테이블만으로
-- 학습이 되도록 자족적으로 만든다(fixed_keywords는 wipe 대상이 아니므로
-- 키워드 문자열은 JOIN으로 가져오면 되고 스냅샷이 필요 없다).
CREATE TABLE IF NOT EXISTS article_labels (
    id BIGSERIAL PRIMARY KEY,

    -- 어느 시장 기준의 판단인지. ON DELETE CASCADE를 일부러 안 붙인다 —
    -- scripts/manage_fixed_keywords.py의 remove_keyword가 세운 관례
    -- (설정 삭제로 지난 데이터가 조용히 같이 지워지면 안 됨)를 따르는 것이고,
    -- 라벨은 그중에서도 재수집이 불가능한 자산이라 더더욱 그렇다. 키워드를
    -- 지우려면 라벨을 어떻게 할지 먼저 정해야 하고, 평소엔 삭제 대신
    -- 비활성화(active=FALSE)를 쓰면 된다.
    fixed_keyword_id INTEGER NOT NULL REFERENCES fixed_keywords(id),

    -- 중복 판정 키(위 (2) 참고). utils/url_normalize.normalize_url의 결과를 넣는다.
    url_norm TEXT NOT NULL,

    -- 사람이 누른 값. BOOLEAN 대신 TEXT+CHECK인 이유: 실제로 라벨링을 해보면
    -- "애매해서 보류"가 필요해질 가능성이 높은데, 그때 BOOLEAN이면 타입을
    -- 바꾸는 데이터 마이그레이션이 필요하지만 이 형태면 CHECK 제약만 풀면
    -- 된다('unsure' 추가). status/fetch_method/pipeline 등 이 스키마의 기존
    -- 관례와도 같은 모양이다.
    label TEXT NOT NULL CHECK (label IN ('relevant', 'irrelevant')),

    -- 학습 입력 스냅샷(원본이 wipe돼도 재학습 가능하게).
    title TEXT NOT NULL,
    snippet TEXT,
    url TEXT NOT NULL,              -- 화면에서 원문으로 이동하는 용도(정규화 전 원본)
    source_domain TEXT,
    published_at TIMESTAMPTZ,

    -- 어느 파이프라인의 수집분이었는지(v2/v3 비교 실험 중이라 구분이 필요하고,
    -- 나중에 "출처별로 라벨 편향이 있나"를 볼 때도 쓴다).
    source_table TEXT NOT NULL CHECK (source_table IN ('search_results', 'collected_articles')),

    -- 어느 주 수집분인지. 모델 평가 시 fold를 기사 단위로 쪼개면 같은 주
    -- 같은 키워드의 기사가 학습·검증에 나뉘어 들어가 점수가 부풀려진다.
    -- (주차 × 키워드) 그룹 단위로 나누려면 주차 정보가 남아 있어야 하는데,
    -- weekly_runs는 매주 지워지므로 여기 복사해 둔다.
    period_start DATE NOT NULL,

    labeled_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- 같은 기사·같은 키워드에 라벨은 하나. 다시 누르면 덮어쓰기(UPSERT)한다.
    UNIQUE (url_norm, fixed_keyword_id)
);

-- 키워드별 라벨 조회/집계(라벨링 화면의 진행률, 학습 전 클래스 분포 확인)용.
-- 위 UNIQUE 인덱스는 url_norm이 선두라 키워드로 거르는 질의엔 안 쓰인다.
CREATE INDEX IF NOT EXISTS idx_article_labels_keyword
    ON article_labels (fixed_keyword_id, label);
