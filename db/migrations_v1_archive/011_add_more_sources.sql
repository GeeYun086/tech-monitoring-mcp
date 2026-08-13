-- 2026-08-10: 담당자가 조사한 IT/AI 뉴스 사이트 정리 목록 중 RSS가 실제로
-- 존재하는지 하나씩 검증(feedparser로 직접 테스트)해 확인된 4개를 추가한다.
--
-- - AI타임스: 국내 AI 전문 매체(정책·산업동향 중심). rss/allArticle.xml 확인.
-- - flex blog "AX 허브": 조직 운영·전략 중심 국내 기업 블로그. "AX 시대,
--   조직의 운명을 가르는 5가지 변화" 등 이름 그대로 AX 콘텐츠.
-- - McKinsey: 해외 컨설팅사 공식 인사이트(조직 운영·전략 중심).
-- - AI News(artificialintelligence-news.com): 해외 AI 산업·정책 전문지.
--
-- 확인했으나 RSS가 없어서 보류: OKKY·혁신의숲·NAVER Cloud/Clova 블로그·
-- Upstage 블로그(스크래핑 필요, 별도 검토), Gartner·AI Magazine(403 차단).

INSERT INTO sources (name, source_type, feed_url, source_trust, active) VALUES
    ('AI타임스', 'rss', 'https://www.aitimes.com/rss/allArticle.xml', 0.8, TRUE),
    ('flex blog AX 허브', 'rss', 'https://flex.team/blog/rss.xml', 0.7, TRUE),
    ('McKinsey', 'rss', 'https://www.mckinsey.com/rss', 0.8, TRUE),
    ('AI News', 'rss', 'https://www.artificialintelligence-news.com/feed/', 0.8, TRUE)
ON CONFLICT (name) DO UPDATE
    SET source_type = EXCLUDED.source_type,
        feed_url = EXCLUDED.feed_url,
        source_trust = EXCLUDED.source_trust,
        active = EXCLUDED.active;
