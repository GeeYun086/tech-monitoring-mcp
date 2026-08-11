-- 2026-08-11: cluster_id 충돌 버그 수정.
--
-- stage5_cluster.py의 apply_stage5()는 호출(=배치)마다 로컬 카운터를
-- f"cluster-{n}"으로 1부터 다시 매겼다. 파이프라인이 6시간마다 도는 배치성
-- 작업이라, 서로 다른 시점의 배치가 우연히 같은 순번("cluster-12" 등)을
-- 만들면 전혀 무관한 시점의 기사들이 같은 cluster_id로 뒤섞여 보였다.
--
-- 실사용 중 발견: "cluster-12"에 2018-02-07 기사와 2026-08-10 기사가 함께
-- 묶여 있었다(count=10, distinct_days=10) — 같은 사건일 수 없는 조합.
-- queries.py의 build_clusters()가 이 cluster_id 문자열만 보고 그룹핑하므로
-- "N개 매체 동시보도" 배지·관련 기사 목록에 영향을 줄 수 있다.
--
-- 전역 유일값을 보장하는 시퀀스를 도입하고, 기존 'new' 상태 기사의
-- cluster_id를 초기화해 다음 파이프라인 실행 때 새 스킴으로 재클러스터링
-- 되게 한다(006/007 마이그레이션과 같은 패턴 — cluster_id NULL 처리 후
-- 재처리에 맡긴다). 'archived' 기사는 조회 대상에서 항상 제외되므로 건드릴
-- 필요가 없다.

CREATE SEQUENCE IF NOT EXISTS cluster_id_seq;

UPDATE articles
SET cluster_id = NULL
WHERE status = 'new' AND cluster_id IS NOT NULL;
