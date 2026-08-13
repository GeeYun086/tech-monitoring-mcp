-- v2 피벗: 룰 기반 필터링(stage1~5) + 구글 트렌드 폐기.
-- 최종 방식: 큐레이션 검색엔진(Custom Search, dateRestrict=w1)으로 고정
-- 키워드별 이번 주 기사를 넓게 수집 → 파이썬이 TF-IDF로 후보 구(phrase)와
-- 정확한 등장 횟수를 계산 → Gemini는 그 후보 목록만 보고 동의어 그룹만
-- 병합(OpenAI/오픈AI/오픈 ai 같은 표기 차이) → 파이썬이 합집합으로 최종
-- 횟수 재계산. 카운팅은 항상 코드, 의미 판단(동의어 여부)만 Gemini —
-- LLM에게 원본 기사 목록을 통째로 주고 빈도를 세게 하지 않는다(목록이
-- 길어지면 LLM이 카운팅을 잘 틀림).
-- 관련도는 큐레이션 검색엔진 자체가 보장하므로 별도 관련도 판별 단계 없음.
-- 이 파일은 v1(001~013, articles/sources/topics, pgvector)과 무관한
-- 완전히 새로운 DB(Supabase)를 대상으로 한다.
--
-- 운영 방침: 무료 DB 티어 유지를 위해 매주 데이터를 통째로 비우고 재수집.
--   TRUNCATE weekly_runs RESTART IDENTITY CASCADE;
-- 한 줄로 fixed_keywords(사용자 설정)만 남기고 나머지가 전부 비워진다.

-- 사용자가 지정한 고정 키워드(모니터링 대상 시장). 매주 wipe 대상 아님 — 설정값.
CREATE TABLE IF NOT EXISTS fixed_keywords (
    id SERIAL PRIMARY KEY,
    keyword TEXT NOT NULL UNIQUE,
    display_order SMALLINT NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 주간 배치 실행 메타. 다른 수집 테이블은 전부 이 테이블을 통해 cascade 삭제된다.
CREATE TABLE IF NOT EXISTS weekly_runs (
    id SERIAL PRIMARY KEY,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    UNIQUE (period_start, period_end)
);

-- 검색엔진으로 수집한 원본 기사 전체(이번 주, 고정 키워드별, top20으로
-- 안 자름). market_keywords의 variant_phrases로 필터링하면 이게 그대로
-- "이 키워드 관련 주간 이슈 기사 목록"이 된다(사건 단위 그룹화 아님 —
-- 같은 키워드를 언급한 기사 전체라는 더 넓은 범주).
CREATE TABLE IF NOT EXISTS search_results (
    id BIGSERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES weekly_runs(id) ON DELETE CASCADE,
    fixed_keyword_id INTEGER NOT NULL REFERENCES fixed_keywords(id),
    query TEXT NOT NULL,           -- 검색엔진에 실제로 보낸 질의어(고정 키워드 원문)
    rank SMALLINT NOT NULL,        -- 검색결과 내 원 순위(참고용)
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source_domain TEXT,
    snippet TEXT,
    published_at TIMESTAMPTZ,
    content TEXT,                  -- collectors/extract_content.py로 본문 백필(선택)
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, fixed_keyword_id, url)
);

CREATE INDEX IF NOT EXISTS idx_search_results_run_fixed ON search_results (run_id, fixed_keyword_id);

-- "이번 주 주요 키워드" 최종 목록(워드클라우드/순위 겸 이슈 기사 필터 키).
-- canonical_phrase = Gemini가 고른 대표 표기, variant_phrases = 병합된
-- 원본 표기들(대시보드에서 기사 필터링 시 이 배열 전체로 매칭).
-- doc_count·tfidf_score는 병합 이후 파이썬이 재계산한 값 — Gemini는
-- variant_phrases 그룹핑만 하고 숫자는 만들지 않는다.
CREATE TABLE IF NOT EXISTS market_keywords (
    id BIGSERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES weekly_runs(id) ON DELETE CASCADE,
    fixed_keyword_id INTEGER NOT NULL REFERENCES fixed_keywords(id),
    canonical_phrase TEXT NOT NULL,
    variant_phrases TEXT[] NOT NULL DEFAULT '{}',
    doc_count SMALLINT NOT NULL,        -- 병합 후 재계산(합집합, 단순 합산 아님)
    tfidf_score NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, fixed_keyword_id, canonical_phrase)
);

CREATE INDEX IF NOT EXISTS idx_market_keywords_run_fixed
    ON market_keywords (run_id, fixed_keyword_id, tfidf_score DESC);

-- 참고: 대시보드의 "검색창"(사용자 직접 검색)은 이 테이블에 저장하지 않고
-- 요청 시점에 커스텀 검색엔진 API를 라이브로 호출하는 걸 권장(무료 한도 절약).
