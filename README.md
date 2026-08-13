# tech-monitoring-mcp

goormEDU 전략기획팀 **AX 시장 모니터링**.

## v2 아키텍처 (2026-08-13 피벗)

기존(v1)은 RSS로 넓게 수집한 뒤 임베딩·룰 기반으로 관련도·파급력을 사후
판별하는 방식이었다. 노이즈가 많고 판별 정확도도 낮아, **노이즈를 수집
단계에서 원천 차단**하는 방식으로 전환했다.

```
사용자가 직접 구성한 Custom Search Engine(화이트리스트 사이트만 검색됨)
    ↓ dateRestrict=w1(지난 1주일), 고정 키워드마다 top20 같은 인위적 컷 없이 넓게 수집
search_results (원본 기사 풀)
    ↓ TF-IDF(코드, 정확한 카운팅) — 한글/영문 비대칭 처리
후보 키워드 목록
    ↓ Gemini(동의어 그룹핑만 — 숫자 계산은 시키지 않음)
    ↓ 코드가 그룹별 문서 집합을 합집합으로 재계산(doc_count 확정)
market_keywords ("이번 주 주요 키워드" 최종 목록)
```

관련도 판별 단계가 따로 없다 — 큐레이션 검색엔진 자체가 관련도를 보장한다는
게 이 피벗의 핵심 전제다. "파급력"도 별도 스코어링 없이 **언급량(doc_count)
자체가 신호**라는 더 단순한 모델을 쓴다(사건 단위 교차보도 클러스터링은
검토 후 제외 — 자세한 배경은 git 히스토리·PR 참고).

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
- `GOOGLE_SEARCH_API_KEY` / `GOOGLE_SEARCH_CX` — Custom Search JSON API 키 +
  [programmablesearchengine.google.com](https://programmablesearchengine.google.com)에서
  화이트리스트 사이트로 직접 구성한 검색엔진 ID
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

순서: (매주 데이터 wipe) → 이번 주 run 시작 → 검색엔진 수집(고정 키워드별)
→ 키워드 후보추출 + Gemini 동의어 병합. 한 고정 키워드의 수집·병합이
실패해도 다른 고정 키워드는 계속 진행되고, 실패한 단계는 반환값의
`failed`에 남는다.

개별 단계만 다시 돌리고 싶을 때:

```bash
./.venv/Scripts/python.exe -m tech_monitoring.collectors.search_engine
./.venv/Scripts/python.exe -m tech_monitoring.analysis.keyword_extraction  # 후보 목록만 확인(저장 안 함)
./.venv/Scripts/python.exe -m tech_monitoring.analysis.keyword_merge       # Gemini 병합 + market_keywords 저장
./.venv/Scripts/python.exe -m pytest tests/ -q
```

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

`db/migrations/001_market_keywords_schema.sql` 참고. 테이블 4개:

| 테이블 | 역할 | 매주 wipe? |
| --- | --- | --- |
| `fixed_keywords` | 사용자가 지정한 고정 키워드(모니터링 대상 시장) | 아니오 — 설정값 |
| `weekly_runs` | 주간 배치 실행 메타. 다른 수집 테이블은 전부 이 테이블에 cascade 연결 | 예(wipe 트리거) |
| `search_results` | 검색엔진에서 가져온 이번 주 원본 기사(top20 고정 없음) | 예 |
| `market_keywords` | 동의어 병합까지 끝난 "이번 주 주요 키워드" 최종 목록 | 예 |

`market_keywords.canonical_phrase` + `variant_phrases`(병합된 원본 표기들,
예: `{"OpenAI","오픈AI","오픈 ai"}`)로 `search_results`를 필터링하면 그게
그대로 "이 키워드 관련 주간 기사 목록"이 된다.

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
  collectors/search_engine.py   # Custom Search API 수집(dateRestrict=w1)
  analysis/keyword_extraction.py  # TF-IDF 후보 추출(코드, 정확한 카운팅)
  analysis/keyword_merge.py       # Gemini 동의어 병합 + market_keywords 확정
  utils/keyword_text.py           # 구(phrase)+TF-IDF 로직(v1에서 이관, 실사용 검증 완료)
  db/connection.py, db/migrate.py, db/weekly_run.py
  pipeline_v2.py                  # 오케스트레이터
db/migrations/          # v2 스키마
db/migrations_v1_archive/  # v1 스키마(참고용, 더 이상 적용 안 됨)
scripts/manage_fixed_keywords.py
```

## 진행 현황

- 수집(검색엔진) → 후보추출(TF-IDF) → 병합(Gemini) → 오케스트레이터까지 완료.
- v1 코드(수집기·필터 5단계·MCP 서버·대시보드 데이터 스크립트) 정리 완료.
- Streamlit 대시보드(고정 키워드 탭 + 주요 키워드 랭킹 + 키워드별 주간 기사
  목록 + 직접 검색창)까지 완료.
- **다음**: Claude 연결용 MCP는 v2 스키마 기준으로 아직 재구축 전(당분간 공백).
