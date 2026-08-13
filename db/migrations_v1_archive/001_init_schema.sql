-- Phase 1 Day 1: 마스터 DB 스키마 (articles / sources / topics)
-- 근거: 기술 설계서 §3 데이터 모델

CREATE EXTENSION IF NOT EXISTS vector;

-- 설정 테이블: 모니터링 대상 소스 (RSS/API), 가중치는 [확인 필요] → 기본값 플레이스홀더
CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL CHECK (source_type IN ('rss', 'api', 'aggregator', 'crawl')),
    feed_url TEXT,
    source_trust FLOAT NOT NULL DEFAULT 0.5,  -- [확인 필요] 담당자 신뢰도 기준
    active BOOLEAN NOT NULL DEFAULT TRUE,
    last_collected_at TIMESTAMPTZ,  -- 증분 수집 기준점 (B2 버그 해결)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 설정 테이블: 모니터링 주제/키워드 (관심 주제 벡터 매칭용)
CREATE TABLE IF NOT EXISTS topics (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    keywords TEXT[] NOT NULL DEFAULT '{}',
    embedding vector(1024),  -- BGE-M3 dense 차원
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 마스터 뉴스 테이블
CREATE TABLE IF NOT EXISTS articles (
    id BIGSERIAL PRIMARY KEY,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    source_id INTEGER REFERENCES sources(id),
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_trust FLOAT NOT NULL DEFAULT 0.5,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    url_canonical TEXT NOT NULL,
    summary TEXT,
    content TEXT,
    lang TEXT,
    embedding vector(1024),
    ts tsvector,
    matched_by TEXT,
    relevance_score FLOAT,
    importance_score FLOAT,
    importance_signals JSONB NOT NULL DEFAULT '{}'::jsonb,
    cluster_id TEXT,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reported', 'archived')),
    UNIQUE (url_canonical)
);

-- tsvector 자동 갱신 (제목+본문 → BM25용 인덱스)
CREATE OR REPLACE FUNCTION articles_ts_update() RETURNS trigger AS $$
BEGIN
    NEW.ts := to_tsvector('simple', coalesce(NEW.title, '') || ' ' || coalesce(NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_articles_ts_update ON articles;
CREATE TRIGGER trg_articles_ts_update
    BEFORE INSERT OR UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION articles_ts_update();

CREATE INDEX IF NOT EXISTS idx_articles_ts ON articles USING GIN (ts);
CREATE INDEX IF NOT EXISTS idx_articles_embedding ON articles USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles (published_at);
CREATE INDEX IF NOT EXISTS idx_articles_cluster_id ON articles (cluster_id);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles (status);
