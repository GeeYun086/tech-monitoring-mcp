-- 고정 키워드마다 언어별 검색어(동의어) 목록 추가(2026-08-13, 실사용 피드백).
--
-- 문제: fixed_keywords.keyword 문자열 하나를 그대로 검색어로 썼더니
-- (1) 한국어 사이트에서도 표현이 다르면 못 찾고("비즈니스 실적"은 한국어
--     사이트에서 3건만 나왔고 그나마 1건만 진짜 관련 있었음),
-- (2) 영어 사이트(TechCrunch 등)엔 한국어라 아예 안 맞아서 Tavily가
--     못 찾는 대신 그 도메인의 무관한 인기글로 채워 넣었다(실측 확인 —
--     "교육"으로 검색해도 TechCrunch에서 나온 19건이 전부 교육과 무관).
--
-- 짧게 쪼개는 게 해법은 아니었다 — "도입"·"실적"만 남기면 오히려 더
-- 흔해서("Signal 자동 키 검증 도입"처럼 AI와 무관한 것까지 걸림) 노이즈가
-- 늘어난다(실측 확인). 대신 같은 개념을 가리키는 여러 개의 **구체적인**
-- 동의어/변형 표현을 언어별로 병렬 등록해 recall을 넓힌다.
--
-- keyword(표시용 이름 — 탭 제목, Gemini 프롬프트의 "이 시장" 표기 등)는
-- 그대로 두고, 실제 검색엔 이 배열들을 쓴다. 비어있으면 collectors/
-- search_engine.py가 keyword 자체로 폴백한다(기존 동작 유지).
ALTER TABLE fixed_keywords
    ADD COLUMN IF NOT EXISTS search_terms_ko TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE fixed_keywords
    ADD COLUMN IF NOT EXISTS search_terms_en TEXT[] NOT NULL DEFAULT '{}';
