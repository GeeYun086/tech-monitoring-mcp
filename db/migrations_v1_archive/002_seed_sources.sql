-- Phase 1 Day 2: 1차 구현 세트 중 RSS 피드 URL이 리서치 문서에 명시적으로 확인된 소스만 시딩.
-- source_trust는 리서치 문서의 참고도(★)를 임시 매핑한 값 — 담당자 신뢰도 기준 수신 후 재조정 [확인 필요]
-- 국내 매체(ZDNet·AI타임스·블로터·바이라인·디지털데일리) 및 Anthropic 공식 피드는
-- RSS 주소가 ◐확인(미검증) 상태라 이번 시딩에서 제외함 — 주소 확인 후 별도 추가.

INSERT INTO sources (name, source_type, feed_url, source_trust) VALUES
    ('전자신문', 'rss', 'https://www.etnews.com/rss/', 0.7),
    ('GeekNews', 'rss', 'https://news.hada.io/rss', 0.6),
    ('The Verge', 'rss', 'https://www.theverge.com/rss/index.xml', 0.7),
    ('TechCrunch', 'rss', 'https://techcrunch.com/feed/', 0.7),
    ('Ars Technica', 'rss', 'https://feeds.arstechnica.com/arstechnica/index', 0.7),
    ('MIT Technology Review', 'rss', 'https://www.technologyreview.com/feed/', 0.7),
    ('Techmeme', 'aggregator', 'https://www.techmeme.com/feed.xml', 0.9),
    ('Hacker News (points>=100)', 'aggregator', 'https://hnrss.org/frontpage?points=100', 0.9),
    ('Stratechery', 'aggregator', 'https://stratechery.com/feed/', 0.9),
    ('Import AI', 'aggregator', 'https://importai.substack.com/feed', 0.9),
    ('TLDR AI', 'aggregator', 'https://tldr.tech/api/rss/ai', 0.7),
    ('a16z', 'aggregator', 'https://a16z.com/feed/', 0.7),
    ('OpenAI', 'rss', 'https://openai.com/news/rss.xml', 0.9)
ON CONFLICT (name) DO NOTHING;
