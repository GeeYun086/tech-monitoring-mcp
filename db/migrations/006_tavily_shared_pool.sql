-- Tavily 수집분을 시장과 분리된 공용 기사 풀에 담는다(2026-08-19).
--
-- 왜 바꾸는가 — 실측 근거(2026-08-18, 이틀치 데이터):
--   (1) 시장 이름을 그대로 검색어로 쓰면 후보가 시장당 3~5건뿐이다
--       ("에이전트 도입" 3건, "비즈니스 실적" 5건). 라벨링·학습이 성립하지
--       않는 양이다. 003이 기록한 문제("비즈니스 실적은 3건만 나왔다")가
--       그대로 재현됐다.
--   (2) 회사 환경에서 시장당 90건이 나왔던 이유는 동의어를 여러 개 등록해
--       뒀기 때문인데, 그러면 "동의어를 누가 지어내는가"라는 문제가 남는다.
--       사용자가 시장 이름만 넣고 쓰는 도구를 목표로 하면 이건 막힌 길이다.
--   (3) 넓은 질의(ko "AI"·"AI 기업" / en "AI"·"AI startup")로 던지면 고유
--       58건이 5개 사이트에 고르게 모였다. Tavily는 정확 일치를 요구하지
--       않고 결과가 부족하면 그 도메인 최신글로 채우기 때문에(003에도 같은
--       관찰이 있다), 넓은 질의는 사실상 "사이트 최신글 훑기"가 된다.
--       무관 기사가 섞이는 건 손해가 아니라 분류기 학습에 필요한 "도움 안 됨"
--       샘플이다(라벨이 relevant로만 쏠리면 지표 자체가 무의미해진다).
--
-- 그래서 정밀도를 검색어로 잡으려는 시도를 접고, **수집은 넓게 하고 선별은
-- 분류기(사람 라벨로 학습)가 한다**로 방향을 정했다.
--
-- 저장 구조: search_results는 fixed_keyword_id가 NOT NULL이라 시장이 행에
-- 박힌다 — 같은 기사가 시장 3개에 걸리면 3번 저장된다(실측 127행 = 고유
-- 58건). 넓은 질의는 시장과 무관하므로 그릇이 맞지 않는다. collected_articles
-- 는 UNIQUE (run_id, url)로 이미 시장과 분리돼 있고, "기사 × 시장" 판단은
-- article_keyword_relevance가 따로 받는다. 그래서 새 테이블을 만들지 않고
-- v3의 이 구조를 그대로 물려받는다(판단·라벨링 배선도 함께 재사용된다).
--
-- 부수 효과: 시장을 추가해도 재수집이 필요 없고, 크레딧이 시장 수와 무관해진다
-- (실측 기준 36 → 12/주).
--
-- search_results와 v2 수집 함수(collect_for_keyword)는 지우지 않는다 —
-- 시장별 검색어로 정밀도를 보강하는 선택적 경로로 남긴다.
ALTER TABLE collected_articles
    DROP CONSTRAINT IF EXISTS collected_articles_fetch_method_check;

-- 'search'(Tavily 검색 API) 추가. 'rss'/'scrape'는 v3 수집기가 그대로 쓴다.
ALTER TABLE collected_articles
    ADD CONSTRAINT collected_articles_fetch_method_check
    CHECK (fetch_method IN ('rss', 'scrape', 'search'));
