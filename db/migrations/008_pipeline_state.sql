-- 파이프라인이 주(week)를 넘어 기억해야 하는 소수의 상태(2026-08-19).
--
-- 지금 담는 것은 하나 — "최초 수집(bootstrap)을 이미 했는가".
--
-- 왜 필요한가: 수집 주기를 두 가지로 나눴다(담당자 결정 2026-08-19).
--   최초 1회 : 이번 주 + 지난 2주 = 3주치를 한 번에
--   그 이후  : 매주 월요일에 **완료된 직전 주**만
-- 그러려면 "이번이 최초인가"를 판단해야 하는데, 판단 근거를 둘 곳이 없었다.
--
-- weekly_runs로는 알 수 없다: 매주 TRUNCATE weekly_runs RESTART IDENTITY
-- CASCADE로 통째로 비우므로(001 헤더의 무료 티어 방침), 매주 실행마다 테이블이
-- 비어 있는 상태에서 시작한다 — 늘 "최초"로 보인다.
-- article_labels가 비었는지로 대신 볼 수도 없다: 라벨링을 며칠 미루면 그 사이
-- 실행마다 3주치를 다시 긁어 Tavily 크레딧을 반복해서 쓴다.
--
-- 그래서 wipe를 타지 않는 별도 테이블에 기록한다. weekly_runs를 참조하지
-- 않으므로 CASCADE 범위 밖이다 — fixed_keywords·article_labels가 살아남는
-- 것과 같은 방법이다.
--
-- 단일 컬럼 대신 key/value로 둔 이유: 앞으로 "마지막 성공 수집 주" 같은
-- 주 넘김 상태가 더 생겨도 마이그레이션 없이 키만 추가하면 된다. 반대로
-- 여기에 대량 데이터를 넣지는 않는다(그건 각자의 테이블로).
CREATE TABLE IF NOT EXISTS pipeline_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
