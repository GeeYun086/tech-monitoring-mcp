-- v3 실험: 검색엔진(고정 키워드별 쿼리) 대신 재선정한 사이트 4개
-- (Techmeme·TechCrunch는 RSS, GeekNews Weekly·AI타임스 AI산업/AI기업은
-- 스크래핑)를 통째로 수집하고, "이 글이 이 고정 키워드와 관련 있는가"
-- 판단을 규칙이 아니라 LLM에게 맡긴다(2026-08-13, 담당자 결정).
--
-- v2(collectors/search_engine.py 기반)를 대체하는 게 아니라 **나란히 돌려
-- 몇 주간 비교**하기 위한 것이라 기존 테이블은 건드리지 않고 새 테이블만
-- 추가한다. 어느 쪽이 나은지(recall, 노이즈, 비용/안정성) 실측 후 하나를
-- 접을 계획 — 그때까지 이 마이그레이션도, v2 쪽도 둘 다 살아있는 게 정상.
--
-- v2와 근본적으로 다른 지점: 검색엔진은 "고정 키워드 × 사이트"로 쿼리해서
-- 수집 시점에 이미 어느 고정 키워드에 속하는지 알았다(search_results.
-- fixed_keyword_id NOT NULL). 여기선 사이트 전체를 먼저 가져오고 나서
-- 어느 고정 키워드와 관련 있는지를 LLM이 사후 판단하므로, 원본 수집과
-- 키워드 연관을 별도 테이블로 분리한다(기사 하나가 여러 고정 키워드와
-- 동시에 관련될 수 있어 다대다).

-- 사이트 전체 수집 결과(고정 키워드 무관). 매주 wipe(run_id로 cascade).
CREATE TABLE IF NOT EXISTS collected_articles (
    id BIGSERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES weekly_runs(id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,      -- 'Techmeme' / 'TechCrunch' / 'GeekNews Weekly' / 'AI타임스'
    fetch_method TEXT NOT NULL CHECK (fetch_method IN ('rss', 'scrape')),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source_domain TEXT,
    snippet TEXT,                   -- RSS description 또는 큐레이터 코멘트(GeekNews Weekly 등)
    content TEXT,                   -- 본문 백필(선택 — search_results와 동일 관례)
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, url)            -- 같은 글이 여러 섹션 스크래핑에 걸쳐 중복 수집돼도 한 번만
);

CREATE INDEX IF NOT EXISTS idx_collected_articles_run ON collected_articles (run_id);

-- LLM의 "이 글이 이 고정 키워드와 관련 있는가" 판단(다대다). 카운팅은
-- 여전히 코드가 한다는 v2의 원칙을 그대로 유지 — LLM은 관련/무관 판단만
-- 하고, 이후 TF-IDF 후보추출·doc_count 계산은 기존
-- analysis/keyword_extraction.py·keyword_merge.py를 그대로 재사용한다
-- (이 테이블에서 관련 있는 기사만 골라 search_results와 같은 모양
-- (title/snippet/source_domain)의 행으로 넘겨주기만 하면 됨 — 새 fetch
-- 함수 하나만 추가하면 나머지 파이프라인은 무변경).
CREATE TABLE IF NOT EXISTS article_keyword_relevance (
    id BIGSERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES weekly_runs(id) ON DELETE CASCADE,
    article_id BIGINT NOT NULL REFERENCES collected_articles(id) ON DELETE CASCADE,
    fixed_keyword_id INTEGER NOT NULL REFERENCES fixed_keywords(id),
    is_relevant BOOLEAN NOT NULL,
    judged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, fixed_keyword_id)   -- 재실행해도 같은 글·키워드 쌍은 한 번만 판정
);

-- "관련 있는 것만" 조회가 압도적으로 잦은 접근 패턴이라 부분 인덱스로 좁힌다.
CREATE INDEX IF NOT EXISTS idx_article_keyword_relevance_lookup
    ON article_keyword_relevance (run_id, fixed_keyword_id) WHERE is_relevant;

-- market_keywords는 v2/v3 공용 출력 테이블 — 대시보드에서 나란히 비교하려면
-- 어느 파이프라인이 만든 행인지 구분해야 한다. 그래서 기존 UNIQUE(run_id,
-- fixed_keyword_id, canonical_phrase)에 pipeline을 더해, 같은
-- run·고정키워드·phrase라도 파이프라인별로 독립된 행을 남긴다.
ALTER TABLE market_keywords
    ADD COLUMN IF NOT EXISTS pipeline TEXT NOT NULL DEFAULT 'search_engine'
        CHECK (pipeline IN ('search_engine', 'rss_llm'));

ALTER TABLE market_keywords
    DROP CONSTRAINT IF EXISTS market_keywords_run_id_fixed_keyword_id_canonical_phrase_key;
ALTER TABLE market_keywords
    ADD CONSTRAINT market_keywords_run_fixed_phrase_pipeline_key
        UNIQUE (run_id, fixed_keyword_id, canonical_phrase, pipeline);

CREATE INDEX IF NOT EXISTS idx_market_keywords_pipeline
    ON market_keywords (run_id, fixed_keyword_id, pipeline, tfidf_score DESC);
