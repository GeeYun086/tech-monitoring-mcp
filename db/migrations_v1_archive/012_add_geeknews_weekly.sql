-- 2026-08-10: GeekNews Weekly(사람이 직접 큐레이션한 주간 다이제스트)를
-- 스크래핑 기반 소스로 추가한다. source_type='crawl'로 등록해 collect_all()의
-- 일반 RSS 루프(rss/aggregator/api만 대상)에서 자연히 빠지게 하고,
-- pipeline.py에서 collectors/geeknews_weekly.py를 별도로 호출한다.
--
-- 신뢰도를 원본 GeekNews 파이어호스(0.5)보다 높게(0.85) 잡는다 — Show GN·
-- 개인 프로젝트 같은 노이즈 없이 사람이 직접 골라 코멘트까지 단 것이라
-- Stratechery·Techmeme급 큐레이션 소스로 취급한다.
-- feed_url은 실제 피드가 아니라 아카이브 인덱스 URL(수집기가 여기서
-- 최신 주차를 찾아냄) — 참고용으로 남겨둔다.

INSERT INTO sources (name, source_type, feed_url, source_trust, active) VALUES
    ('GeekNews Weekly', 'crawl', 'https://news.hada.io/weekly', 0.85, TRUE)
ON CONFLICT (name) DO UPDATE
    SET source_type = EXCLUDED.source_type,
        feed_url = EXCLUDED.feed_url,
        source_trust = EXCLUDED.source_trust,
        active = EXCLUDED.active;
