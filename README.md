# tech-monitoring-mcp

goormEDU 전략기획팀 **AX 시장 모니터링** 백엔드 + 사내 MCP.
근거 문서: PRD v2.0 · 기술 설계서 v2.0 · 개발 계획서 v2.0 (8/6 회의 반영).

**시스템은 관련도(AX 시장 관련?) × 파급력(반향이 큰가?) 2축만 산정한다.**
주관적 "중요도"는 산정하지 않으며 담당자 판단 영역이다.

## 개발 환경 세팅

```bash
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
cp .env.example .env

docker compose up -d          # Postgres + pgvector 컨테이너
./.venv/Scripts/python.exe -m tech_monitoring.db.migrate   # 스키마 적용 + 소스 시딩
```

## 파이프라인 실행

**한 번에 실행(권장)**: `python -m tech_monitoring.pipeline` — 수집부터 리랭커까지 정해진
순서로 실행하고, 한 단계가 실패해도 나머지는 계속 진행한 뒤 실패한 단계를 보고한다(Phase 3).
아래 개별 실행은 특정 단계만 다시 돌리고 싶을 때 쓴다.

```bash
./.venv/Scripts/python.exe -m tech_monitoring.pipeline                    # 전체 파이프라인(권장)

./.venv/Scripts/python.exe -m tech_monitoring.collectors.rss              # RSS/애그리게이터/arXiv 수집
./.venv/Scripts/python.exe -m tech_monitoring.collectors.extract_content  # trafilatura 본문 백필

./.venv/Scripts/python.exe -m tech_monitoring.filters.stage1_rules        # Stage1 룰 프리필터
./.venv/Scripts/python.exe -m tech_monitoring.filters.stage2_relevance    # Stage2 AX 관련도
./.venv/Scripts/python.exe -m tech_monitoring.filters.stage5_cluster      # Stage5 이슈 클러스터링
./.venv/Scripts/python.exe -m tech_monitoring.filters.stage3_impact       # Stage3 파급력 스코어
./.venv/Scripts/python.exe -m tech_monitoring.filters.stage4_rerank       # Stage4 리랭커(상위 30건)

./.venv/Scripts/python.exe scripts/tune_relevance_threshold.py            # τ 민감도 스윕(PoC)
./.venv/Scripts/python.exe -m pytest tests/ -q                            # 테스트
```

> 실행 순서 주의: **Stage5(클러스터링)를 Stage3보다 먼저** 돌려야 `cluster_size`가 파급력 스코어에 반영된다.
> `tech_monitoring.pipeline`은 이 순서를 코드로 고정해뒀다.

## 모니터링 MCP

마스터 DB를 사내 Claude에 노출하는 stdio MCP 서버(설계서 v2.0 §6).
**내부 정성 데이터 전용** — DART·특허·금융 등 공개 API는 프로젝트 ②(별도 MCP)다.

| 도구 | 용도 | 주요 인자 |
| --- | --- | --- |
| `search_news` | 하이브리드(BM25+BGE-M3) 검색. 질의어를 비우면 파급력 상위 반환 | `query` `since` `until` `min_impact` `limit` |
| `get_weekly_digest` | 구간 이슈를 `cluster_id`로 묶어 상위 이슈 제공(주간 센싱용) | `period` `limit` `min_impact` |

- 기간 인자는 `7d` · `24h` · `2w` · `2026-08-01` · `2026-08-01..2026-08-07`,
  `period`는 추가로 `last_week`(기본) · `this_week`를 받는다.
  `last_week`는 **전주 월~일**로 끊어 스케줄 센싱에 이번 주 진행분이 섞이지 않게 한다.
- 응답은 Claude 컨텍스트를 고려해 요약 400자 제한·필드 축소로 추리고,
  파급력 근거(`impact_signals`)를 함께 실어 **판단은 담당자가** 하도록 한다.
- `status='archived'`(필터 탈락) 기사는 조회되지 않는다.
- `search_news`의 첫 호출은 BGE-M3 로딩(CPU)으로 수십 초가 걸릴 수 있다. 이후는 캐시된다.

```bash
./.venv/Scripts/python.exe -m tech_monitoring.mcp_server   # stdio 서버 실행
./.venv/Scripts/python.exe scripts/smoke_mcp.py            # 도구 목록·질문형 응답 검증
```

### Claude 연결

- **Claude Code**: 저장소의 `.mcp.json`을 그대로 사용한다(프로젝트 스코프, 상대 경로).
- **Claude Desktop**: 설정 파일에 같은 내용을 **절대 경로**로 넣는다.

```json
{
  "mcpServers": {
    "tech-monitoring": {
      "command": "C:\\Users\\<user>\\Desktop\\tech-monitoring-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "tech_monitoring.mcp_server"]
    }
  }
}
```

DB 접속 정보는 `.env`(`DATABASE_URL`)에서 읽으므로 MCP 설정에 비밀값을 넣지 않는다.
Postgres 컨테이너가 떠 있어야 도구가 응답한다.

## 필터 파이프라인

- **Stage1** (`filters/stage1_rules.py`): 빈 제목·차단 확장자·최소 길이만 값싸게 컷.
- **Stage2 · 관련도** (`filters/stage2_relevance.py`): BGE-M3로 topics/articles 임베딩 → 시맨틱(cosine ≥ τ) 또는 키워드(tsvector) 매칭.
  **키워드 목록이 아니라 `topics.description`의 "AX 시장" 의미 서술과의 유사도로 넓게 판단**(설계서 v2.0 §5).
  `topics.keywords`는 비어 있는 것이 정상이며, 이 경우 BM25 경로는 비고 dense 경로만 동작한다.
  **`topics.description`이 관련도 필터의 의미 기준점 = 가장 중요한 튜닝 노브.**
- **Stage5 · 클러스터링** (`filters/stage5_cluster.py`): 코사인 유사도 기반 그리디 클러스터링으로 동일 이슈를 `cluster_id`로 묶고,
  `impact_signals.cluster_size`(5건 이상 동시보도면 1.0 포화)를 남겨 파급력 신호를 보강.
- **Stage3 · 파급력** (`filters/stage3_impact.py`): 4개 신호 가중합.

  | 신호 | 내용 | 기본 가중치 |
  | --- | --- | --- |
  | `source_trust` | 큐레이션 소스 중심 신뢰도 | 0.35 |
  | `aggregator_signal` | **실제 HN points**(500점 포화). 수치 없는 소스는 0 | 0.25 |
  | `cluster_size` | 여러 매체 동시 보도 | 0.20 |
  | `recency` | 최신성 감쇠(반감기 72h) | 0.20 |

  v2.0에서 **감성 분석·이슈유형 휴리스틱·회사관점 LLM 중요도 판정은 제거**됨.
- **Stage4 · 리랭커** (`filters/stage4_rerank.py`): 파급력 상위 30건만 `bge-reranker-v2-m3`로 재정렬 → `impact_signals.rerank_score`.
- 필터를 통과하지 못한 기사는 **삭제하지 않고** `status='archived'` + `impact_signals.filtered_stage`로 사유를 남긴다(재현·디버깅용).
- BGE-M3는 CPU 추론이 느려 `max_seq_length=512`로 제한(기본 8192는 비현실적).

## 파라미터 튜닝

τ·클러스터 임계값·최신성 반감기·파급력 가중치·HN points 포화점·소급 수집 기간을 모두 `config.py`의 `Settings`로 관리하며,
`.env`에서 `RELEVANCE_COSINE_THRESHOLD` 등으로 코드 수정 없이 덮어쓸 수 있다.

`scripts/tune_relevance_threshold.py`로 τ 민감도 곡선을 확인할 수 있다.
**실제 정밀도·재현율 측정은 AX 실데이터 라벨링 세트가 있어야 하며 Phase 5 과제**(설계서 v2.0 §11).

### Phase 3 조사 결과 — τ·클러스터 임계값을 지금 올리지/내리지 않은 이유

실사용 검증(주간 다이제스트) 중 "AX와 무관한 기사가 상위에 뜬다"는 문제를 조사했다.

- **근본 원인은 τ가 아니라 본문 누락이었다.** `status='new'` 205건 중 192건이
  `content IS NULL` — 즉 관련도 판정이 실제 기사 본문이 아니라 "Points: N" 같은
  RSS 메타데이터만으로 이뤄지고 있었다(HN 계열 피드는 본문을 안 주고,
  `extract_content` 백필이 오래된 순으로 돌면서 신규 기사에 못 미쳤다).
  → `collectors/extract_content.py`가 `status='new'`를 우선하도록 수정하고,
  전체 백필(179/200건 추출 성공) 후 재임베딩·재판정했다.
- **τ는 올리지 않았다.** 실제 본문으로 재계산해도 관련/무관 기사의 코사인 유사도가
  0.33~0.51 범위에서 매끈하게 이어져 있어(라벨 없이는) 안전하게 자를 지점이 없다.
  라벨링 세트 없이 τ를 올리면 무관한 기사와 함께 관련 있는 기사도 잘라낼 위험이 있다.
- **클러스터 임계값(0.85)도 낮추지 않았다.** 0.60~0.75로 낮춰 봤더니 서로 다른
  실적 발표·서로 다른 제품 공지가 "같은 이슈"로 잘못 묶였다(뉴스레터·다이제스트류
  글이 텍스트 구조가 비슷해 임베딩이 가까워짐). 이대로 낮추면 "여러 매체 동시보도"
  신호 자체가 거짓이 되므로 보류.
- **다음에 필요한 것**: (1) AX 관련/무관 라벨 세트(담당자 라벨링) → τ 그리드 서치,
  (2) 클러스터링에 URL 도메인·발행 시각 근접성 등 임베딩 외 신호 추가.

## 수집 정책

- 소스는 `sources` 테이블 설정으로 관리하고 코드는 하나. RSS·애그리게이터·API(arXiv Atom)를 같은 수집기가 처리.
- **증분 수집**: 2회차부터 `last_collected_at` 이후 발행분만 (기존 시스템 B2 버그 대응).
- **최초 수집 소급 제한**(`initial_backfill_days`, 기본 30일): 일부 피드는 전체 아카이브를 한 번에 내려준다.
  실제로 OpenAI 피드가 2015년까지 1,110건을 쏟아냈고, 전부 AI 주제라 관련도 필터를 통과하면서
  후보 풀을 점령해 다른 소스를 밀어냈다(통과분 300건 중 277건이 OpenAI). 이 제한으로 해소.
- **URL 정규화 중복제거**: utm/www/amp/모바일 변형 흡수 (B8 대응).
- **HN points 수집**: hnrss 피드 본문의 `Points: N`을 파싱해 `impact_signals.hn_points`에 저장 → 파급력 신호로 사용.

## 소스 현황

활성: Techmeme · HN(points≥100) · Stratechery · Import AI · TLDR AI(큐레이션·애그리게이터) /
arXiv cs.AI(논문) / The Verge · TechCrunch · Ars Technica · MIT Tech Review · OpenAI(글로벌).

비활성:
- 전자신문 `/rss/`가 HTML 반환, a16z `/feed/` 404, GeekNews 403(nginx 차단) — 피드가 깨짐
- Hacker News (Algolia) · Naver News — **키워드 검색 기반**이라 "키워드 미지정" 방침에서 질의어가 없어 동작 불가.
  HN은 hnrss 애그리게이터 피드가 대체하며, Naver는 "국내 한정하지 않음" 기조상 보류.
  `keyword_api.py` 모듈 자체는 남겨두어, `topics.keywords`를 채우면 다시 동작한다.

## 프로젝트 구조

```
src/tech_monitoring/
  collectors/   # RSS·API 수집기, trafilatura 본문추출
  filters/      # 관련도(Stage1~2)·클러스터링(5)·파급력(3~4) 필터
  db/           # DB 연결·마이그레이션(적용 이력 추적)
  mcp_server/   # 모니터링 MCP — server.py(도구 정의) · queries.py(DB 조회 계층)
db/migrations/  # SQL 마이그레이션 (schema_migrations로 1회만 적용)
.mcp.json       # Claude Code용 MCP 연결 설정
```

## 진행 현황

- **Phase 0~1 완료**: 수집 → 필터 → 마스터 DB end-to-end 동작.
- **Phase 1.5 완료**: 8/6 회의 변경사항(impact 리네이밍·필터 간소화·AX 시장 전체·최신성 로직) 반영.
- **Phase 2 완료**: 모니터링 MCP(`search_news`·`get_weekly_digest`) + Claude 연결 설정·응답 검증.
- **Phase 3 진행 중** — ⚠ 실제 진행 순서를 원래 개발계획서와 바꿔서 진행: 원래 Phase 3은
  스케줄 센싱이고 이 튜닝·안정화 내용은 원래 Phase 5였다. 주간 다이제스트 실사용 검증에서
  무관한 데이터가 상위에 뜨는 걸 확인해, 무관한 데이터로 주간 인사이트부터 만드는 게
  무의미하다고 판단해 순서를 바꿨다(단계 내용·문서상 이름은 그대로, 진행 순서만 교체).
  이번에 한 일: 실사용 검증 중 발견한 데이터 품질 문제(본문 누락) 수정,
  파이프라인 오케스트레이터(`tech_monitoring.pipeline`)로 순서 고정·실패 격리.
  τ·클러스터 임계값은 라벨 세트 없이는 안전하게 조정 불가로 판단, 보류(위 "Phase 3 조사 결과" 참고).
- **다음**: (원래 Phase 5 검증 계속 →) 스케줄 센싱(주간 인사이트, 커밋상 Phase 5) → Artifact 대시보드.
  공개 API 도구(DART·특허 등)는 별도 프로젝트 ②.
