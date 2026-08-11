-- 2026-08-11: 담당자가 추가 조사한 사이트 목록을 RSS 존재 여부 + robots.txt
-- 준수 여부까지 직접 확인(curl -A "<프로젝트 UA>")한 뒤, 통과한 것만 추가한다.
--
-- - Gartner 뉴스룸: 011에서 "403 차단"으로 보류했었는데, 그건 다른 경로(메인
--   리서치 페이지 등)를 시도했다가 막힌 기록으로 보인다. `/en/newsroom/rss`는
--   재검증 결과 200 OK로 정상 응답하고, robots.txt(`User-agent: *`)에도 이
--   경로를 막는 규칙이 없다. 단, 이건 보도자료·시장 전망 발표 피드이고 매직
--   쿼드런트·하이프사이클 같은 애널리스트 리포트 원문은 여전히 구독 벽 뒤에
--   있어 이 피드로는 못 가져온다 — 그 구분을 신뢰도(0.8, McKinsey와 동급)에 반영.
-- - 국내/해외 기업 기술블로그 6종(우아한형제들·카카오·토스·당근·NHN Cloud·
--   Meta): 언론사가 아니라 회사가 직접 쓰는 1차 소스라 사실관계 오류 위험은
--   낮지만, "시장 전체 동향"이 아니라 "자사 사례" 중심이라 대표성이 떨어진다.
--   신뢰도는 언론사(0.8대)보다 낮고 커뮤니티 애그리게이터(HN·GeekNews 파이어호스
--   0.5)보다는 높은 0.65로 잡는다. AX 무관 글(예: 일반 백엔드 최적화)은 기존
--   관련도 필터가 걸러낼 것으로 기대.
-- - 구글 코리아 개발자 블로그: 위 기업 블로그류와 결이 비슷하지만 매주
--   Gemini/AI 업데이트를 요약해서 내는 "위클리 다이제스트" 성격이라 개별
--   엔지니어링 포스트보다 산업 동향에 조금 더 가깝다고 보고 0.7로 소폭 상향.
--   실제 피드는 Blogger 주소(`/feeds/posts/default`)가 FeedBurner로 리다이렉트
--   되므로, 리다이렉트 없이 바로 받아지는 FeedBurner 주소를 등록.
--
-- 확인했으나 보류(robots.txt 위반 또는 RSS 없음):
-- - 아이티월드(itworld.co.kr): robots.txt가 `/feed/`·`/*/feed/`를 전역 차단.
--   대체 RSS 경로도 못 찾음.
-- - TechNeedle: `techneedle.com/feed` 자체는 200 OK로 살아있지만 robots.txt가
--   `Disallow: /feed`를 명시. 자동 준수 로직은 없지만 정책상 존중해 보류.
-- - 티타임즈: 추정 RSS 경로 전부 302/404. robots.txt도 포괄 `User-agent: *`
--   규칙 없이 지정 크롤러(Googlebot 등)만 명시적으로 허용하는 구조라 일반
--   수집기 대상 공개 피드로 보기 어려움. 카드뉴스 포맷이라 스크래핑 시 텍스트
--   추출도 까다로움.
-- - "Dighty Data Market": 검색으로 정확한 사이트를 특정하지 못해 보류 —
--   정확한 URL 확인 후 재검토.

INSERT INTO sources (name, source_type, feed_url, source_trust, active) VALUES
    ('Gartner 뉴스룸', 'rss', 'https://www.gartner.com/en/newsroom/rss', 0.8, TRUE),
    ('우아한형제들 기술블로그', 'rss', 'https://techblog.woowahan.com/feed/', 0.65, TRUE),
    ('카카오 기술블로그', 'rss', 'https://tech.kakao.com/feed', 0.65, TRUE),
    ('토스 기술블로그', 'rss', 'https://toss.tech/rss.xml', 0.65, TRUE),
    ('당근 기술블로그', 'rss', 'https://medium.com/feed/daangn', 0.65, TRUE),
    ('NHN Cloud 기술블로그', 'rss', 'https://meetup.nhncloud.com/rss', 0.65, TRUE),
    ('Meta 엔지니어링 블로그', 'rss', 'https://engineering.fb.com/feed/', 0.65, TRUE),
    ('구글 코리아 개발자 블로그', 'rss', 'http://feeds.feedburner.com/GoogleDevelopersKorea', 0.7, TRUE)
ON CONFLICT (name) DO UPDATE
    SET source_type = EXCLUDED.source_type,
        feed_url = EXCLUDED.feed_url,
        source_trust = EXCLUDED.source_trust,
        active = EXCLUDED.active;
