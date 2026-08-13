-- 2026-08-10: HN이 "신뢰도 있는 큐레이션 매체"가 아니라 "인기 있으면 뭐든 올라오는
-- 범용 커뮤니티"라는 걸 실사용 중 발견 (담당자 피드백: 주간 다이제스트 상위 15건 중
-- 10건이 HN이었고, 그중 다수가 AX·사업과 무관한 일반 인기글 — "택시기사는 알츠하이머
-- 안 걸린다", "1998년 웹 표준 에세이" 등).
--
-- Techmeme·Stratechery·Import AI·OpenAI는 실제로 AX 관련 콘텐츠만 다루는
-- 편집/큐레이션 소스라 0.9가 맞지만, HN은 그런 편집 과정이 없다. 완전히
-- 제외하지는 않는다(가끔 진짜 중요한 AX 이슈도 HN에서 먼저 터짐 — 담당자 확인).
-- source_trust만 낮춰 비중을 줄인다.

UPDATE sources SET source_trust = 0.5 WHERE name = 'Hacker News (points>=100)';

-- source_trust는 수집 시점에 articles에 그대로 복사되므로(비정규화), 이미 쌓인
-- 행도 같이 갱신해야 한다. 이후 stage3_impact를 재실행해 impact_score에 반영한다.
UPDATE articles SET source_trust = 0.5 WHERE source = 'Hacker News (points>=100)';
