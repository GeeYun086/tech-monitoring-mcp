-- 팀별 자체 수집(2026-08-25) — "새 팀 만들기" 화면이 팀마다 다른 사이트
-- 조합으로 수집하게 해준다.
--
-- search_terms_ko/en(003)은 이미 있어서 "팀마다 다른 검색어"는 됐는데,
-- "팀마다 다른 사이트"는 지금까지 SITE_DOMAINS(collectors/search_engine.py의
-- 전역 상수) 하나로 고정돼 있었다. 완전히 새로운 사이트를 팀이 직접
-- 추가하는 건 여전히 안 된다(도메인마다 URL 화이트리스트 패턴을 사람이
-- 한 번은 확인해야 한다 — is_allowed_url 헤더 참고) — 대신 이미 검증된
-- SITE_DOMAINS 중 어떤 걸 쓸지는 팀이 고를 수 있게 한다.
--
-- NULL(기본값)이든 빈 배열('{}')이든 "전체 화이트리스트 사용"으로
-- 취급한다(collect_for_keyword의 `or SITE_DOMAINS` 폴백) — 팀 화면에서
-- 사이트를 하나도 안 고른 실수로 수집이 조용히 0건이 되는 것보다,
-- 일단 전체로 도는 게 덜 놀랍다는 판단. 화면(app/streamlit_app.py "새
-- 팀 만들기")은 그래도 최소 1곳 선택을 요구해 이 폴백에 기대지 않는다.
ALTER TABLE fixed_keywords
    ADD COLUMN IF NOT EXISTS site_domains TEXT[];
