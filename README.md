# tech-monitoring-mcp
goormEDU 전략기획팀 업무 효율성 개선 및 자동화를 위한 프로젝트 - 산업 동향 모니터링용 사내 mcp 시스템 구현

## 개발 환경 세팅

```bash
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
cp .env.example .env

docker compose up -d          # Postgres + pgvector 컨테이너
./.venv/Scripts/python.exe -m tech_monitoring.db.migrate   # 스키마 적용 + 소스 시딩

./.venv/Scripts/python.exe -m tech_monitoring.collectors.rss           # RSS/arXiv(API) 수집
./.venv/Scripts/python.exe -m tech_monitoring.collectors.keyword_api    # HN Algolia/Naver 키워드 수집
./.venv/Scripts/python.exe -m tech_monitoring.collectors.extract_content  # trafilatura 본문 백필

./.venv/Scripts/python.exe -m tech_monitoring.filters.stage1_rules      # Stage1 룰 프리필터
./.venv/Scripts/python.exe -m tech_monitoring.filters.stage2_relevance  # Stage2 하이브리드 관련도 필터

./.venv/Scripts/python.exe -m tech_monitoring.filters.stage3_importance # Stage3 중요도 스코어
./.venv/Scripts/python.exe -m tech_monitoring.filters.stage4_rerank     # Stage4 리랭커(상위 후보만)
./.venv/Scripts/python.exe -m tech_monitoring.filters.stage4_llm_judge  # Stage4 LLM-judge 프롬프트 확인(호출은 안 함)
./.venv/Scripts/python.exe -m tech_monitoring.filters.stage5_cluster    # Stage5 이슈 클러스터링

./.venv/Scripts/python.exe scripts/tune_relevance_threshold.py          # τ 민감도 스윕(PoC)
./.venv/Scripts/python.exe -m pytest tests/ -q                          # 테스트
```

## 필터 파이프라인 (Day 4 기준)

- **Stage1** (`filters/stage1_rules.py`): 빈 제목·차단 확장자·최소 길이만 값싸게 컷. 임계값은 `[확인 필요]`.
- **Stage2** (`filters/stage2_relevance.py`): BGE-M3(`filters/embeddings.py`)로 topics/articles 임베딩 →
  키워드(tsvector) **OR** 시맨틱(cosine ≥ τ) 중 하나라도 만족하면 통과. `relevance_score`는 BM25 랭크 + dense 랭크의 RRF 융합값(설명용).
  τ=0.35는 라벨링 세트로 튜닝 전 임시값 `[확인 필요]` (Day6에서 조정).
- 필터를 통과하지 못한 기사는 삭제하지 않고 `status='archived'` + `importance_signals.filtered_stage`로 사유를 남김(디버깅·재현 가능성 확보).
- BGE-M3는 CPU 추론 시 느릴 수 있어 `max_seq_length=512`로 제한(기본 8192는 비현실적으로 느림).

## 중요도·정밀판단 (Day 5 기준)

- **Stage3** (`filters/stage3_importance.py`): source_trust·aggregator_signal·cluster_size·recency·issue_type·sentiment
  6개 신호의 가중합. 가중치는 균등 플레이스홀더 `[확인 필요]`. issue_type/sentiment는 키워드 휴리스틱(모델 없이 설명 가능).
  cluster_size는 Stage5(클러스터링, Day6) 이전엔 중립값 사용.
- **Stage4 리랭커** (`filters/stage4_rerank.py`): importance_score 상위 30건만 `bge-reranker-v2-m3`로 재정렬,
  `importance_signals.rerank_score`에 저장(비싼 단계는 소수에만 적용하는 퍼널 원칙).
- **Stage4 LLM-as-judge** (`filters/stage4_llm_judge.py`): 실제 호출 없는 **틀**. 설계상 LLM 판단은 별도 API 과금이 아니라
  Phase 2 통합 MCP를 통해 팀의 구독형 Claude가 수행하므로, 여기서는 후보 선정 + 프롬프트 조립까지만 구현.
  회사 관점 중요도 기준(`criteria`)은 담당자 확인 전까지 비워둠 `[확인 필요]`.

## 클러스터링·파라미터화·튜닝 (Day 6 — Phase 1 뼈대 완성)

- **Stage5** (`filters/stage5_cluster.py`): 임베딩 코사인 유사도 기반 그리디 클러스터링으로 동일 이슈를 `cluster_id`로 묶음.
  클러스터 크기는 `importance_signals.cluster_size`(5건 이상 동시보도면 1.0로 포화)로 저장되어 Stage3 중요도 스코어에 재반영됨.
- **파라미터화**: τ(관련도 임계값)·클러스터 유사도 임계값·최신성 반감기·6개 중요도 가중치를 모두
  `config.py`의 `Settings`로 이동 — `.env`에 `RELEVANCE_COSINE_THRESHOLD` 등으로 덮어쓸 수 있음. 담당자 기준 수신 후
  코드 수정 없이 값만 조정하면 됨 `[확인 필요]`.
- **관련도 PoC 튜닝** (`scripts/tune_relevance_threshold.py`): τ를 0.20~0.50으로 스윕하며 시맨틱 매칭 통과 건수 변화를 관찰.
  실제 정밀도·재현율 측정은 라벨링 세트가 있어야 하므로 `[확인 필요]` — 지금은 민감도 곡선만 확인하는 메커니즘.
  실행 결과(현재 데이터 1,260건 기준): τ=0.30→1246건, 0.35→1161건, 0.40→789건, 0.45→323건, 0.50→48건 통과.
  실제 Stage2는 후보를 상위 300건으로 제한(`CANDIDATE_TOP_N`)한 뒤 τ를 적용하므로 최종 통과 수(301건)는 이보다 적음.

이것으로 **Phase 1(공유 백엔드 뼈대)** 완성 — 수집(Day1-3) → 필터(Day4-6) → 마스터DB까지 end-to-end 동작 확인.
다음은 Phase 2(통합 MCP).

Naver 수집은 `.env`에 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`이 없으면 자동 skip(`[확인 필요]`).
키워드 기반 수집은 `topics` 테이블의 활성 주제 키워드를 사용 — 현재는 `placeholder-AX`로 메커니즘만 시딩되어 있고, 실제 모니터링 대상/키워드는 담당자 확인 후 교체.

## 소스 현황 (Day 2 기준)

`db/migrations/002_seed_sources.sql`에 리서치 문서상 피드 URL이 확인된 소스만 시딩. 실제 수집 테스트 결과 3건은 깨져 있어 비활성화(`003_deactivate_broken_feeds.sql`):
- 전자신문: `/rss/`가 HTML을 반환 (실제 피드 주소 재확인 필요)
- a16z: `/feed/` 404 (주소 변경 추정)
- GeekNews: UA 지정에도 403 (nginx 차단, 기존 시스템 B5와 동일 증상)

나머지 국내 매체(ZDNet·AI타임스·블로터·바이라인)와 Anthropic 공식 피드는 리서치 문서에서도 주소가 ◐확인 상태라 이번엔 시딩하지 않음.

## 프로젝트 구조

```
src/tech_monitoring/
  collectors/   # RSS·API 수집기 (Day2~3)
  filters/      # 관련도·중요도 필터 (Day4~6)
  db/           # DB 연결·마이그레이션
  mcp_server/   # 통합 MCP 서버 (Phase 2)
db/migrations/  # SQL 스키마 마이그레이션
```
