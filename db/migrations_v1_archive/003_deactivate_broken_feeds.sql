-- Phase 1 Day 2: 실제 수집 테스트에서 확인된 깨진 피드 3건 비활성화.
-- 전자신문: /rss/ 가 RSS가 아니라 HTML 페이지를 반환 (실제 피드 주소 재확인 필요)
-- a16z: /feed/ 404 (사이트 리뉴얼로 주소 변경된 것으로 추정, 재확인 필요)
-- GeekNews: UA 지정에도 403 Forbidden (nginx 레벨 차단, 기존 시스템의 B5와 동일 증상)

UPDATE sources SET active = FALSE
WHERE name IN ('전자신문', 'a16z', 'GeekNews');
