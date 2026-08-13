-- Phase 1 Day 3: API 소스(arXiv/HN Algolia/Naver) + 모니터링 주제 플레이스홀더.
-- 실제 모니터링 대상·키워드는 담당자 확인 후 교체 [확인 필요] — 지금은 메커니즘 검증용 플레이스홀더.

-- arXiv는 Atom 피드라 feed_url만 주면 기존 RSS 수집기를 그대로 재사용 가능.
INSERT INTO sources (name, source_type, feed_url, source_trust) VALUES
    ('arXiv cs.AI', 'api', 'https://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=50', 0.8)
ON CONFLICT (name) DO NOTHING;

-- HN Algolia / Naver는 키워드 기반 동적 질의라 feed_url이 없고, 별도 수집기(keyword_api.py)가 처리.
-- last_collected_at은 증분 수집 기준점으로 그대로 재사용.
INSERT INTO sources (name, source_type, feed_url, source_trust) VALUES
    ('Hacker News (Algolia)', 'api', NULL, 0.6),
    ('Naver News', 'api', NULL, 0.6)
ON CONFLICT (name) DO NOTHING;

INSERT INTO topics (name, keywords) VALUES
    ('placeholder-AX', ARRAY['AI transformation', 'AX', '인공지능 전환'])
ON CONFLICT (name) DO NOTHING;
