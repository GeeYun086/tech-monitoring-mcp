# tech-monitoring-mcp

goormEDU 전략기획팀 **AX 시장 모니터링**.

## v2 아키텍처 (2026-08-13 피벗)

기존(v1)은 RSS로 넓게 수집한 뒤 임베딩·룰 기반으로 관련도·파급력을 사후
판별하는 방식이었다. 노이즈가 많고 판별 정확도도 낮아, **노이즈를 수집
단계에서 원천 차단**하는 방식으로 전환했다.

```
Tavily Search API + 코드로 관리하는 화이트리스트(SITE_INCLUDE/EXCLUDE_PATTERNS)
    ↓ time_range=week(지난 1주일), 고정 키워드×사이트 조합마다 개별 호출,
    ↓ top20 같은 인위적 컷 없이 넓게 수집 + 화이트리스트 패턴으로 최종 필터
search_results (원본 기사 풀)
    ↓ TF-IDF(코드, 정확한 카운팅) — 한글/영문 비대칭 처리
후보 키워드 목록
    ↓ Gemini(동의어 그룹핑만 — 숫자 계산은 시키지 않음)
    ↓ 코드가 그룹별 문서 집합을 합집합으로 재계산(doc_count 확정)
market_keywords ("이번 주 주요 키워드" 최종 목록)
```

관련도 판별 단계가 따로 없다 — 큐레이션된 화이트리스트 자체가 관련도를
보장한다는 게 이 피벗의 핵심 전제다. "파급력"도 별도 스코어링 없이
**언급량(doc_count) 자체가 신호**라는 더 단순한 모델을 쓴다(사건 단위
교차보도 클러스터링은 검토 후 제외 — 자세한 배경은 git 히스토리·PR 참고).

**검색 API로 원래 Google Custom Search JSON API를 쓰려 했으나(2026-08-13
실제 연동 중 발견) Google이 2025년 중반 이후 신규 계정에는 이 API 접근을
막아둬서(2027-01-01 서비스 종료 예정 공지) Tavily Search API로 교체했다.**
Tavily는 `include_domains`/`exclude_domains`를 API 파라미터로 직접 받아서
Google처럼 별도 "검색엔진" 설정을 웹 UI에서 만들 필요가 없고, `time_range`로
기간 제한도 네이티브 지원, 무료 티어 월 1,000크레딧(카드 등록 불필요)이다.
다만 Tavily의 도메인/경로 매칭 정확도를 100% 신뢰하지 않고, **화이트리스트
최종 판정은 `collectors/search_engine.py`의 `is_allowed_url()`(담당자가
검증한 패턴, 코드로 직접 매칭)이 이중으로 강제한다.**

**무료 DB 티어 유지 방침**: 매주 데이터를 통째로 비우고 재수집한다.
`fixed_keywords`(모니터링 대상 시장 설정)만 보존되고 나머지는
`TRUNCATE weekly_runs RESTART IDENTITY CASCADE`로 매주 wipe된다.

v1(RSS 수집 + 임베딩 기반 필터링)은 `db/migrations_v1_archive/`에 스키마만
남아 있다 — 왜 그 방식을 버렸는지의 조사 기록이라 지우지 않았다.

## 개발 환경 세팅

```bash
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
cp .env.example .env
```

`.env`에 채울 것:
- `DATABASE_URL` — Supabase(Postgres 호환) 연결 문자열
- `TAVILY_API_KEY` — [tavily.com](https://tavily.com)에서 발급(무료 티어, 카드 등록 불필요).
  화이트리스트 사이트 목록은 `collectors/search_engine.py`의
  `SITE_INCLUDE_PATTERNS`/`SITE_EXCLUDE_PATTERNS`에 코드로 관리(수정 시 이 파일만 고치면 됨)
- `GEMINI_API_KEY` — 무료 티어

```bash
./.venv/Scripts/python.exe -m tech_monitoring.db.migrate   # 스키마 적용(db/migrations/)
```

## 고정 키워드(모니터링 대상 시장) 설정

Streamlit UI가 생기기 전까지는 CLI로 관리한다.

```bash
./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py add "AX 시장" --order 1
./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py list
```

## 파이프라인 실행

```bash
./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v2
```

순서: (매주 데이터 wipe) → 이번 주 run 시작 → **검색엔진 수집(사이트별 공용
기사 풀, 006)** → **시장별 관련도 점수(분류기, 007 — 모델이 없으면 건너뜀)**
→ 키워드 후보추출 + Gemini 동의어 병합. 한 사이트·키워드가 실패해도 나머지는
계속 진행되고, 실패한 항목은 반환값의 `failed`에 남는다(항목별 `error`도
실패로 집계한다 — 조용히 성공으로 마감되던 문제를 막기 위해).

크레딧: 사이트 6개 × 넓은 질의 2개 = **주 12크레딧**(시장 수와 무관).

### 최초 라벨링용 소급 수집 (한 번만)

```bash
./.venv/Scripts/python.exe scripts/backfill_past_weeks.py            # 지난 2주
./.venv/Scripts/python.exe scripts/backfill_past_weeks.py --weeks 3
```

파이프라인은 이번 주만 수집하므로 주 초반에는 후보가 수십 건뿐이다(실측
2026-08-19: 52건). 라벨 30건을 모으기도 전에 바닥나서 시작 자체가 막히는데,
**Tavily는 start_date/end_date를 받으므로 과거 주를 주차별로 소급 수집할 수
있다**(RSS·AI타임스 스크래핑은 원리적으로 불가). 실측: 2주 소급으로 52건 →
**167건**(발행 주별 54/61/52), 24크레딧.

주차별로 따로 호출한다 — 3주를 한 번에 요청하면 호출당 20건 상한에 3주치가
눌린다. 그리고 라벨의 주차 그룹은 run이 아니라 **기사 발행 주**로 잡히므로
(`labeling._label_period_start`), 소급분이 자연히 여러 주로 갈려
"지난주 라벨로 학습한 모델이 이번 주 기사에 통하는가"를 바로 측정할 수 있다.

한 번만 돌리면 된다: 라벨은 매주 wipe를 타지 않지만 소급 수집된 기사 자체는
다음 파이프라인 실행 때 지워지므로, 돌린 주에 라벨링을 해두는 게 좋다.

개별 단계만 다시 돌리고 싶을 때:

```bash
./.venv/Scripts/python.exe -m tech_monitoring.collectors.search_engine
./.venv/Scripts/python.exe -m tech_monitoring.analysis.keyword_extraction  # 후보 목록만 확인(저장 안 함)
./.venv/Scripts/python.exe -m tech_monitoring.analysis.keyword_merge       # Gemini 병합 + market_keywords 저장
./.venv/Scripts/python.exe -m pytest tests/ -q
```

### v3(비교 실험)

```bash
./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v3
```

**반드시 v2를 먼저 돌린 뒤 실행할 것** — v3는 매주 wipe를 하지 않는다(v2·
v3 나란히 비교하려고 의도적으로 그렇게 만듦, `pipeline_v3.py` 모듈 docstring
참고). v2가 이번 주 run을 시작(및 wipe)해두면 v3는 그 위에 올라타기만 한다.

순서: 재선정 사이트 4개 수집(RSS 2 + 스크래핑 2) → 적합성 판단(고정
키워드별) → 키워드 후보추출 + Gemini 동의어 병합(`pipeline='rss_llm'`로
저장). 개별 단계:

```bash
./.venv/Scripts/python.exe -m tech_monitoring.collectors.rss_collector       # Techmeme·TechCrunch
./.venv/Scripts/python.exe -m tech_monitoring.collectors.geeknews_weekly     # 스크래핑
./.venv/Scripts/python.exe -m tech_monitoring.collectors.aitimes_scraper    # 스크래핑
./.venv/Scripts/python.exe -m tech_monitoring.analysis.relevance_filter     # 적합성 판단
```

## 관련도 판단 — 사람 라벨 기반 분류기 (2026-08-18)

적합성 판단은 원래 Gemini 전담이었는데, **한 번 막히면 그 주 관련 기사가
통째로 0건**이 되는 구조였다(그게 "결과가 흐지부지된다"의 직접 원인).
그래서 담당자가 직접 매긴 라벨로 로컬 분류기를 학습해 대체한다 — API 호출이
0이라 429·크레딧 상태와 무관하다.

```bash
./.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py   # 🏷️ 라벨링 → 📈 성능 탭
./.venv/Scripts/python.exe scripts/train_relevance_classifier.py   # CLI로도 같은 채점
```

1. **라벨링** — 기사 하나씩 "도움이 되는 기사예요 / 도움이 되지 않는 기사예요".
   라벨 단위는 기사가 아니라 **"기사 × 고정 키워드" 쌍**이다(같은 기사가
   "교육"엔 도움돼도 "비즈니스 실적"엔 무관할 수 있다 — 실측상 후보 262건 중
   56개 URL이 여러 키워드에 걸쳐 있다).
2. **채점** — 문자 n-gram TF-IDF와 다국어 문장 임베딩 두 방식을 같은 조건으로
   교차검증해 이긴 쪽을 `models/relevance_classifier.joblib`에 저장한다.
   fold는 그룹 단위로 나눈다: 라벨이 두 주 이상이면 **주차 단위**로 나눠
   "다음 주에도 통하는가"를 직접 재고, 한 주뿐이면 같은 기사가 학습·검증에
   갈리는 누수만 막는다.
3. **적용** — `relevance_filter.judge_all`이 저장된 모델을 자동으로 집어 쓰고,
   기사마다 확률을 `article_keyword_relevance.score`에 남긴다(007). 화면은 이
   점수로 **정렬만** 하고 잘라내지 않는다 — 점수가 낮은 기사도 목록에 남는다
   (분류기가 틀려서 기사가 사라지는 건 "Gemini가 막히면 그 주 0건"과 같은
   종류의 사고다). 모델이 없으면 판단을 건너뛰고(`method`가
   `skipped:모델 없음`) 화면은 최신순 전체를 보여준다 — 라벨이 없는 첫 주에
   LLM을 부를 이유가 없고, 그 주 결과물은 사람이 라벨링한 것 자체가 된다.
   Gemini 경로는 `allow_llm_fallback=True`로만 쓴다(비교 실험용).
   성능 탭에서 모델을 저장하면 그 자리에서 이번 주 기사를 다시 채점해
   시장 탭 순서가 바로 갱신된다(파이프라인 재실행 불필요).

정확도는 **항상 "찍기 기준선"과 나란히** 본다 — 라벨이 한쪽으로 쏠리면
"무조건 도움됨"이라고만 답하는 분류기도 정확도가 높게 나와 단독 수치는
착시다. 화면에도 `+0.000 vs 찍기` 형태로 함께 표시하고, 기준선을 못 넘으면
모델을 저장하지 않는다.

**라벨은 사람 단위로 남는다**(`labeled_by`, 005). 앱은 각자 띄우고 DB는 공용
하나를 쓰는 배포가 목표라, 이 값이 없으면 나중에 라벨한 사람이 앞사람 판단을
조용히 덮어쓴다. `.env`의 `LABELED_BY`로 지정하고(비우면 `local`), 기본 동작은
**내 라벨만으로 학습·집계**한다 — 팀 전체로 학습하려면
`fetch_all_labels(conn, labeled_by=ALL_LABELERS)`를 쓴다. 개인 모델과 통합 모델
중 어느 쪽이 나은지는 라벨이 쌓인 뒤 같은 채점 틀로 비교해서 정한다.

## 대시보드

```bash
./.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
```

직접 검색(라이브 호출, DB 저장 안 함) + 고정 키워드 탭(이번 주 주요 키워드
막대그래프 + 키워드 선택 시 관련 기사만 필터). 계산은 `dashboard_queries.py`가
전담하고 화면은 레이아웃만 — 데이터가 바뀔 때 손으로 다시 계산할 부분이 없다.

DB 연결 실패(Supabase 무료 티어는 7일 미사용 시 자동 일시정지된다) 시
10초 내로 원인을 알려주는 에러 메시지를 띄운다(무한 로딩 방지).

## 스키마

마이그레이션은 `db/migrations/`에 001~007로 누적돼 있다(001 v2 · 002 v3 ·
003 검색어 · 004 라벨 · 005 라벨 주체 · 006 공용 기사 풀 · 007 관련도 점수).
테이블 7개:

| 테이블 | 역할 | 매주 wipe? |
| --- | --- | --- |
| `fixed_keywords` | 사용자가 지정한 고정 키워드(모니터링 대상 시장) | 아니오 — 설정값 |
| `weekly_runs` | 주간 배치 실행 메타. 다른 수집 테이블은 전부 이 테이블에 cascade 연결 | 예(wipe 트리거) |
| `search_results` | 시장별 검색어로 수집하던 옛 경로 — 006부터 파이프라인이 쓰지 않는다(정밀도 보강용으로 보존) | 예 |
| `collected_articles` | **이번 주 공용 기사 풀** — Tavily 넓은 질의(006)와 v3 수집기가 함께 쓴다. 시장과 분리해 기사당 한 행 | 예 |
| `article_keyword_relevance` | "기사 × 시장" 판단(다대다). `score`에 분류기 확률을 남겨 화면 정렬에 쓴다(007) | 예 |
| `article_labels` | 사람이 매긴 관련도 라벨(분류기 학습 데이터). `weekly_runs`를 참조하지 않고 원문을 스냅샷으로 복사해 둔다. `labeled_by`로 라벨 주체를 함께 남긴다(005) | **아니오 — 학습 자산** |
| `market_keywords` | v2/v3 공용 — "이번 주 주요 키워드" 최종 목록. `pipeline` 컬럼(`search_engine`/`rss_llm`)으로 구분 | 예 |

`market_keywords.canonical_phrase` + `variant_phrases`(병합된 원본 표기들,
예: `{"OpenAI","오픈AI","오픈 ai"}`)로 `search_results`(v2) 또는
`collected_articles`(v3, `article_keyword_relevance`로 관련 있는 것만 필터링)를
매칭하면 그게 그대로 "이 키워드 관련 주간 기사 목록"이 된다.

## v2 vs v3 비교 실험(2026-08-13 시작)

v2(검색엔진 기반)를 대체하는 게 아니라 **같은 주, 같은 DB에 나란히 돌려서
비교**하기 위한 실험이다. 몇 주간 recall(놓치는 기사)·정밀도(노이즈)·
비용/안정성을 실측하고 하나를 접을 계획 — 그때까지 v2·v3 코드·테이블
둘 다 살아있는 게 정상이다.

**v3 차이점**: 검색엔진은 "고정 키워드 × 사이트"로 쿼리해서 수집 시점에
이미 어느 고정 키워드에 속하는지 알았지만(`search_results.fixed_keyword_id`
NOT NULL), v3는 재선정한 사이트 4개를 통째로 먼저 수집하고(`collected_articles`,
고정 키워드 무관) **관련도 판단을 규칙이 아니라 LLM에게 맡긴다**
(`article_keyword_relevance`). "카운팅은 코드, 판단은 LLM"이라는 v2의
원칙은 그대로 유지 — LLM은 관련/무관 판단만 하고 TF-IDF 후보추출·doc_count
계산은 기존 `analysis/keyword_extraction.py`·`keyword_merge.py`를 그대로
재사용한다.

**v3 재선정 사이트 4개** (v1의 28개 소스 중 실사용 검증 근거가 있는 것만
남기고, Hacker News는 "AX 무관 인기글 다수 섞임" 문제 재발 우려로 제외):

| 소스 | 수집 방식 |
| --- | --- |
| Techmeme | RSS(`techmeme.com/feed.xml`) |
| TechCrunch | RSS(`techcrunch.com/feed/`) |
| GeekNews Weekly | 스크래핑(RSS 없음, v1 스크래퍼 코드 재사용 — `news.hada.io/weekly`) |
| AI타임스 AI산업/AI기업 | 스크래핑(2026-08-13 확인: RSS 주소가 `cdn.aitimes.com/rss/gn_rss_allArticle.xml`로 이전됐고 섹션 필터도 안 돼 전체기사엔 AX 무관 지역뉴스까지 섞임 — 그래서 `articleList.html?sc_section_code=S1N3`(AI산업)/`sc_sub_section_code=S2N51`(AI기업) 목록 페이지를 직접 파싱) |

## 카운팅은 코드, 의미 판단만 Gemini

`analysis/keyword_merge.py`의 핵심 원칙: Gemini에게 원본 기사나 숫자 계산을
시키지 않는다. 후보 phrase 목록(이미 코드가 TF-IDF로 정확히 센 것)만 보여
주고 "동의어끼리 그룹으로 묶어달라"고만 요청한다. 최종 `doc_count`는 코드가
그룹의 문서 집합을 **합집합**으로 재계산한다(단순 합산 아님 — 한 문서에
여러 표기가 같이 나올 수 있어서). Gemini 응답은 환각(원본에 없는 phrase)·
중복 배정을 걸러내는 검증을 거치고, 실패하면 전부 단독 그룹으로 폴백한다.

## 프로젝트 구조

```
src/tech_monitoring/
  collectors/search_engine.py     # v2: Tavily 수집 + 화이트리스트 이중 검증(time_range=week)
  collectors/rss_collector.py     # v3: Techmeme·TechCrunch RSS
  collectors/geeknews_weekly.py   # v3: GeekNews Weekly 스크래핑(RSS 없음)
  collectors/aitimes_scraper.py   # v3: AI타임스 AI산업 섹션 스크래핑(RSS 없음)
  analysis/keyword_extraction.py  # TF-IDF 후보 추출(코드, 정확한 카운팅) — v2/v3 공용
  analysis/keyword_merge.py       # Gemini 동의어 병합 + market_keywords 확정 — v2/v3 공용
  analysis/relevance_filter.py    # v3: 기사-고정키워드 적합성 판단(분류기 우선, Gemini 폴백)
  labeling.py                     # 사람 라벨 저장/조회(라벨링 화면 ↔ article_labels)
  relevance_model.py              # 라벨로 분류기 학습 + 교차검증 채점(계산 전담)
  pipeline_report.py              # 단계 결과의 항목별 error를 실패로 집계(조용한 실패 차단)
  llm_client.py                   # Gemini 호출 공용 wrapper(keyword_merge·relevance_filter 공유)
  utils/keyword_text.py           # 구(phrase)+TF-IDF 로직(v1에서 이관, 실사용 검증 완료)
  db/connection.py, db/migrate.py, db/weekly_run.py
  pipeline_v2.py                  # v2 오케스트레이터(매주 wipe 담당)
  pipeline_v3.py                  # v3 오케스트레이터(wipe 안 함 — v2 이후 실행)
db/migrations/          # v2(001) + v3(002) + 검색어(003) + 라벨(004)
                        # + 라벨 주체(005) + 공용 기사 풀(006) + 관련도 점수(007)
db/migrations_v1_archive/  # v1 스키마(참고용, 더 이상 적용 안 됨)
scripts/manage_fixed_keywords.py
scripts/train_relevance_classifier.py   # 라벨 → 분류기 학습 + 성능 출력
models/                 # 학습된 분류기(.joblib, git 추적 안 함 — 라벨에서 재생성)
```

## 진행 현황

- v2: 수집(검색엔진) → 후보추출(TF-IDF) → 병합(Gemini) → 오케스트레이터까지 완료,
  실제 Tavily 연동 검증 완료(2026-08-13).
- v3: 수집(RSS 2 + 스크래핑 2) → 적합성 판단 → 후보추출·병합 재사용 →
  오케스트레이터까지 완료, 실제 사이트 연동 검증 완료(2026-08-13, 4개 소스
  전부 수집 성공).
- **조용한 실패 차단(2026-08-18)**: 각 단계는 개별 항목이 실패해도 예외 대신
  결과의 `error` 필드로만 알리는 관례라, 예외만 보던 오케스트레이터가 Gemini
  전면 실패·`TAVILY_API_KEY` 미설정 같은 상황을 `ok`로 넘기고 run을
  `completed`로 마감하고 있었다. `pipeline_report.stage_errors`로 항목별
  error를 실패로 집계하고, 대시보드 상단에도 실패 배너를 띄운다.
- **관련도 분류기(2026-08-18)**: 라벨 테이블·라벨링 화면·학습/채점(교차검증,
  찍기 기준선 비교, Precision@K·NDCG@K)·파이프라인 연결까지 코드 완료.
  라벨 수집은 진행 전이라 실측 성능은 아직 없다(라벨 30건부터 측정 가능).
- v1 코드(수집기·필터 5단계·MCP 서버·대시보드 데이터 스크립트) 정리 완료.
- Streamlit 대시보드(고정 키워드 탭 + 주요 키워드 랭킹 + 키워드별 주간 기사
  목록 + 직접 검색창)까지 완료 — 다만 v2 전용(v3의 `pipeline='rss_llm'` 결과는
  아직 화면에 안 붙임).
- **다음**: (1) 라벨 수집(대시보드 🏷️ 라벨링 탭 — 현재 후보 262건) 후
  📈 성능 탭에서 실측, (2) v2 vs v3 비교를 대시보드에서 보여주는 화면,
  (3) Claude 연결용 MCP는 v2/v3 스키마 기준으로 아직 재구축 전(당분간 공백).

> ⚠️ `pipeline_v2`는 시작할 때 지난주 데이터를 통째로 지운다(`TRUNCATE
> weekly_runs CASCADE`). **라벨링할 후보가 남아 있으면 파이프라인을 돌리기
> 전에 라벨링을 끝내야 한다** — 라벨 자체는 스냅샷이라 안전하지만, 아직
> 라벨 안 한 기사는 사라진다.
