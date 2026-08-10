-- 2026-08-10: 담당자 피드백 — 국내 매체가 단 하나도 활성화돼 있지 않았음.
-- 시도했던 국내 소스 3개가 전부 "일부러 뺀 게" 아니라 기술적으로 막혀서
-- 결과적으로 0개가 됐던 것 — 실제로 코드(feedparser + 기존 User-Agent)로
-- 재검증해 아래처럼 확인했다:
--
-- - 전자신문: 등록된 주소(www.etnews.com/rss/)가 RSS가 아니라 HTML 안내
--   페이지였다. 실제 섹션별 RSS 주소(rss.etnews.com/04.xml, AI·SW)를 찾아
--   재검증 — 정상 작동.
-- - GeekNews: 예전엔 403(nginx 차단)이었는데 같은 URL·UA로 재시도하니 지금은
--   막혀 있지 않다(원인 불명 — 일시적 차단이었던 것으로 추정, 다시 막힐 수도
--   있음). 다만 영어 Hacker News와 똑같이 "인기면 뭐든 올라오는 커뮤니티"라
--   AX·사업과 무관한 개인 프로젝트 글(Show GN 등)이 섞인다 — 그래서 HN과
--   같은 이유로 신뢰도를 낮게 잡는다.
-- - 네이버뉴스: 검색어 필요한 API 구조상 "키워드 미지정" 방침과 근본적으로
--   안 맞아 계속 비활성 유지. RSS 기반 국내 소스로 그 역할을 대체한다.
--
-- 새로 찾은 후보(바이라인네트워크·ZDNet Korea)까지 포함해 4개를 추가한다.

-- 전자신문: 주소만 실제 작동하는 섹션 피드로 교체하고 재활성화
UPDATE sources
SET feed_url = 'http://rss.etnews.com/04.xml', active = TRUE
WHERE name = '전자신문';

-- GeekNews: 주소는 그대로, 재활성화하되 HN과 동일한 이유로 신뢰도를 낮춘다
UPDATE sources
SET active = TRUE, source_trust = 0.5, source_type = 'aggregator'
WHERE name = 'GeekNews';

INSERT INTO sources (name, source_type, feed_url, source_trust, active) VALUES
    ('바이라인네트워크', 'rss', 'https://byline.network/feed/', 0.8, TRUE),
    ('ZDNet Korea', 'rss', 'https://feeds.feedburner.com/zdkorea', 0.7, TRUE)
ON CONFLICT (name) DO UPDATE
    SET source_type = EXCLUDED.source_type,
        feed_url = EXCLUDED.feed_url,
        source_trust = EXCLUDED.source_trust,
        active = EXCLUDED.active;
