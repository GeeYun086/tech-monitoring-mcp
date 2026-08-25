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

**"이번 주 주요 키워드" 화면·이 Gemini 단계는 2026-08-24부로 `pipeline_v2`
(주간 자동 수집)에서 뺐다** — 품질(대문자 시작 휴리스틱 기반) 대비 매주
Gemini를 호출하는 비용이 아깝다는 담당자 판단. `analysis/keyword_merge.py`
모듈 자체와 `pipeline_v3`(아래 "v2 vs v3 비교 실험", 자동 실행 아님)는
그대로 남아 있다.

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
- `DATABASE_URL` — **팀 공용 Supabase 주소**(담당자에게 받는다). `.env.example`의
  `localhost` 기본값을 그대로 두면 안 된다 — 아래 "새 PC에서 시작할 때" 참고
- `TAVILY_API_KEY` — [tavily.com](https://tavily.com)에서 발급(무료 티어, 카드 등록 불필요).
  화이트리스트 사이트 목록은 `collectors/search_engine.py`의
  `SITE_INCLUDE_PATTERNS`/`SITE_EXCLUDE_PATTERNS`에 코드로 관리(수정 시 이 파일만 고치면 됨).
  현재 **17곳** — 일반 테크 6곳 + 교육 매체 4곳 + 국내 IT 7곳(2026-08-24
  ZDNet Korea·디지털데일리·바이라인네트워크·디지털투데이·테크M 5곳 추가, 아래 참고)
- `GEMINI_API_KEY` — **주간 자동 수집(`pipeline_v2`)은 2026-08-24부로 이 키를
  쓰지 않는다.** `analysis/keyword_merge.py`를 단독 실행하거나 `pipeline_v3`
  비교 실험을 돌릴 때만 필요(동의어 병합용, 비어 있어도 단독 그룹 폴백)

```bash
./.venv/Scripts/python.exe -m tech_monitoring.db.migrate   # 스키마 적용(db/migrations/)
./.venv/Scripts/python.exe -m pytest tests/ -q             # 세팅 확인
```

### 새 PC에서 시작할 때 — DB를 새로 만들지 말 것

**마이그레이션·수집한 기사·라벨은 전부 DB에 묶여 있지 컴퓨터에 묶여 있지
않다.** 그래서 새 PC에서 해야 할 일은 위 세 줄과 `.env`에 **기존 Supabase
주소를 넣는 것**뿐이다. 그러면

- 마이그레이션은 전부 `skipping ... (already applied)`로 건너뛰고,
- 이미 수집된 기사를 그대로 쓰므로 **Tavily 크레딧이 0**이며,
- 라벨이 한 곳에 쌓인다(`labeled_by`로 사람 구분 — 005 마이그레이션).

Supabase는 이미 클라우드에 떠 있으므로 **따로 배포할 것이 없다**. 별도로
배포가 필요한 건 앱(대시보드·MCP)이지 DB가 아니다.

> **2026-08-19 실제 사고**: 새 PC에서 `.env.example`을 그대로 복사해
> `localhost`를 보게 됐다. 그 결과 빈 DB가 하나 더 생겨 마이그레이션을
> 처음부터 다시 적용했고, 기사를 다시 수집하느라 크레딧을 두 번 썼으며,
> 라벨이 두 DB로 갈라질 뻔했다. `.env.example` 상단 주석은 이 재발을 막기
> 위한 것이다.

`docker-compose.yml`의 Postgres는 **로컬 실험 전용**이다. 평소 개발에서 이걸
띄우면 위와 같은 두 번째 DB가 생긴다.

### 이미 두 개의 DB로 갈라졌다면

라벨만 옮기면 된다(기사는 다시 수집하면 되지만 라벨은 사람 손이 들어간
자산이다). 옮긴 뒤 로컬 DB는 버려도 된다.

```bash
pg_dump -t article_labels --data-only <옛DB> | psql <공용Supabase>
```

## Streamlit Cloud 배포 — 담당자와 함께 라벨링하기

라벨을 빨리 모으려면 담당자에게 링크만 주고 브라우저에서 라벨링하게 하는 게
가장 빠르다. **DB(Supabase)가 이미 클라우드에 있으므로 누른 라벨은 곧바로
같은 DB에 쌓인다** — 담당자 쪽에 설치할 것이 없다.

### 앱 하나를 함께 쓴다 — 팀 공용 라벨 풀 (2026-08-19 결정)

앱 하나를 배포하고 링크를 공유한다. secrets의 `LABELED_BY`를 `team` 같은
**공용 값 하나**로 두면 누가 누르든 같은 라벨 풀에 쌓인다 — 지금은 통합 모델
(전체 라벨로 하나를 학습)이라 이 편이 일관된다. 코드 수정도 필요 없다.

배포 설정:
- Main file path: `app/streamlit_app.py`
- Python: 3.12 (`pyproject.toml`의 `requires-python`)
- Secrets: `DATABASE_URL`(필수), `LABELED_BY = "team"`. `TAVILY_API_KEY`는
  **넣지 않는 걸 권장** — 대시보드의 "직접 검색"이 실시간 API를 호출해
  크레딧을 쓴다. 없으면 그 칸만 안내 문구가 뜨고 라벨링은 정상 동작한다.
- 무료 앱은 링크를 아는 누구나 들어오고 DB에 쓸 수 있다. 비공개(이메일 초대)
  설정을 쓰거나 링크를 사내에만 공유할 것.

**이미 라벨한 기사를 서로 덮어쓰지는 않는다.** `fetch_unlabeled_candidates`가
"이 라벨러가 아직 안 본 것"만 후보로 올리는데, 공용 값을 쓰면 두 사람이 같은
라벨러이므로 한쪽이 누른 기사는 다른 쪽 화면에서도 곧바로 사라진다(실측
2026-08-19: 기사 152건 중 60건 라벨 → 남은 후보 92건). 누를 기회 자체가 없다.

남는 노출은 두 가지뿐이고 둘 다 실무에서 문제가 되지 않는다:
- "라벨 검토 및 수정" 패널에서는 남이 매긴 것도 보이고 고칠 수 있다 — 공용
  판단 풀이라는 의도에 오히려 맞다(오클릭을 서로 정정해줄 수 있다).
- 두 사람이 같은 기사를 동시에 띄워둔 채 각각 누르면 나중 것이 이긴다.

**진짜로 감수하는 것은 하나다 — 누가 눌렀는지 기록이 남지 않는다.** 전부 같은
`labeled_by`로 저장되므로 **이 방식으로 쌓은 라벨은 나중에 사람별로 나눌 수
없다**(개인 모델로 바꾸더라도 그때부터 쌓는 라벨만 분리된다). 지금은 통합
모델이라 무관하고, 라벨을 빨리 모으는 게 우선이라 이 손실을 받아들인 것이다.

나중에 사람별로 나누려면 화면에 "지금 라벨링하는 사람" 선택을 붙이고
(`labeling`의 모든 함수가 이미 `labeled_by` 인자를 받는다) 앱마다 다른
`LABELED_BY`를 주는 대신 세션에서 정하게 하면 된다. 앱을 사람 수만큼 배포하는
방법도 있지만 쓰는 사람이 늘 때마다 앱을 만들어야 해서 운영 방식으로 맞지 않는다.

### torch를 넣지 않는다

`requirements.txt`는 이 프로젝트만 설치하고 `[embedding]` extra는 뺀다.
sentence-transformers는 torch(설치 527MB) + 다국어 모델(실행 시 458MB)을
끌고 와서 무료 티어로는 감당이 안 된다. 없으면 임베딩 방식만 건너뛰고
(`relevance_model.evaluate`가 ImportError를 잡아 `reason`에 담는다) TF-IDF로만
채점하며 **라벨링 화면은 그대로 동작한다.**

실측(2026-08-19, requirements.txt만 설치한 새 venv): 597MB, torch 미설치,
라벨링 후보 조회·주차 선택·채점·기사 목록까지 전 경로 정상. 다만 그 환경에서
TF-IDF는 찍기 기준선을 못 넘어(정확도 0.550 vs 0.567) `build_model`이 None을
돌려주고 화면이 최신순을 유지했다 — **배포 앱의 목적은 라벨 수집**이고,
임베딩 비교와 순위 갱신은 개발 PC에서 한다.

### 모델은 파일이 아니라 라벨에서 만든다

배포 환경의 파일시스템은 재시작하면 초기화된다. 그래서 판단 경로가
`models/*.joblib`에 의존하지 않고 DB 라벨에서 그때그때 학습한다(작업 6,
`relevance_filter.get_model`). 라벨만 남아 있으면 언제든 같은 모델이 복원된다.

### 지금은 통합 모델 — 나중에 개인 모델로 분리 가능

현재 `get_model`은 **전체 라벨**로 학습하고, 점수(`article_keyword_relevance`)도
사람 구분 없이 한 벌이라 기사 순서는 모두에게 같다. 라벨 자체는 이미
`labeled_by`로 나뉘어 쌓이므로(005) 나중에 개인 모델로 바꿀 수 있다 —
`get_model`에 `labeled_by`를 넘기고(=`fetch_all_labels`가 이미 지원),
점수 테이블에 사람 구분을 추가하면 된다. 반대 방향(기록 없이 시작한 뒤
분리)은 불가능하므로 순서가 이렇게 된 것이다.

어느 쪽이 맞는지는 감으로 정하지 않는다 — 같은 `(url_norm, fixed_keyword_id)`에
사람마다 다른 label이 남으면 그게 곧 "팀 기준이 얼마나 다른가"이고, 이 불일치가
낮으면 통합, 높으면 개인이 맞다는 근거가 된다(005 헤더 참고).

## MCP 서버 — Claude가 수집 결과를 직접 조회

```bash
./.venv/Scripts/python.exe -m tech_monitoring.mcp_server
```

**읽기 전용이다.** 수집·라벨링·학습은 파이프라인과 대시보드가 하고, 여기서는
이미 DB에 있는 결과를 꺼내 보여주기만 한다. 그래서 이 서버에는 머신러닝
라이브러리가 필요 없다 — 분류기 판단은 파이프라인이 미리 끝내
`article_keyword_relevance.score`에 저장해두기 때문이다(psycopg만 있으면 된다).

| 도구 | 하는 일 |
| --- | --- |
| `get_status` | 기준 기간·기사 수·주차별 건수·시장 목록·라벨 현황(+파이프라인 실패 사유) |
| `get_markets` | 시장 목록과 시장별 라벨 진행 상황 |
| `get_articles(market, limit)` | 한 시장의 기사 — **국내 매체 우선** + 좋아요 수 + 분류기 점수순(모델이 없으면 최신순), 기사마다 `like_count`/`dislike_count` 포함 |
| `get_popular_articles(market, limit)` | 한 시장에서 👍를 가장 많이 받은 기사만(2026-08-25 추가) — 주간 인사이트·보고서를 쓸 때 분류기 점수보다 먼저 참고할 값. 좋아요 0건인 기사는 제외하고, 좋아요는 익명 집계라 "누가"는 알 수 없다는 안내를 응답에 함께 담는다 |
| `get_keywords(market, limit)` | 한 시장의 주요 키워드(보조 지표) — 2026-08-24부로 파이프라인이 `market_keywords`를 더 이상 채우지 않아 빈 결과를 돌려준다 |

응답에 **순서의 근거**(`ordering`)와 **전체 건수**(`total`)를 함께 담는다 —
모델이 없어 최신순인 걸 모르면 추천 순위로 오해하고, 응답 길이 때문에 자른
것을 "이게 전부"로 읽는다. 모르는 시장 이름을 물으면 빈 결과 대신 등록된
시장 목록을 돌려준다.

### 도커로 배포하기 (남에게 줄 때)

받는 사람이 Python·가상환경·의존성을 만질 필요 없이 `docker run` 한 줄이면 된다.

```bash
docker build -t tech-monitoring-mcp .
```

Claude Desktop 등의 MCP 설정에 이렇게 등록한다(`DATABASE_URL`은 컨테이너에
굽지 않고 실행할 때 넘긴다 — 이미지에 비밀번호가 들어가면 안 된다):

```json
{
  "mcpServers": {
    "tech-monitoring": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
               "-e", "DATABASE_URL=postgresql://...",
               "tech-monitoring-mcp"]
    }
  }
}
```

**이미지에 머신러닝 라이브러리를 넣지 않는다.** MCP 서버는 읽기 전용이라
분류기를 돌리지 않는다 — 판단은 파이프라인이 미리 끝내 점수로 저장해둔다.
그래서 psycopg + mcp만 들어가고 **274MB**에 머문다(프로젝트 전체를 설치하면
streamlit·scikit-learn까지 딸려와 쓰지도 않을 것으로 몇 배가 된다). 같은 이유로
`pip install .` 대신 소스만 복사하고 `PYTHONPATH`로 잡는다.

> **mcp 버전 상한(`<2`)은 필수다.** 2.0.0에서 `mcp.server.fastmcp`가 사라져
> `server.py`의 import가 깨진다. 실측 2026-08-19: `>=1.2`로 열어뒀더니 로컬
> `.venv`(1.29.0)는 멀쩡한데 **새로 만든 컨테이너만 2.0.0을 받아** 즉시
> 종료됐다 — 새 환경에서만 터지는 종류의 함정이라 `pyproject.toml`에도 같은
> 상한을 넣었다.

실측 검증(2026-08-19): 컨테이너 안에 torch·scikit-learn·streamlit이 없는 것을
확인하고, 실제 stdio 클라이언트를 붙여 핸드셰이크 → 도구 4개 → `get_status`
(기사 415건·라벨 139건) → `get_articles` 호출까지 통과.

저장소 루트의 `.mcp.json`에 이미 설정돼 있어 Claude Code에서는 그대로 잡힌다.
Claude Desktop 등 다른 클라이언트는 아래처럼 등록한다(`DATABASE_URL`은 `.env`
에서 읽으므로 보통 `env`를 따로 줄 필요가 없다).

```json
{
  "mcpServers": {
    "tech-monitoring": {
      "command": "<프로젝트>/.venv/Scripts/python.exe",
      "args": ["-m", "tech_monitoring.mcp_server"]
    }
  }
}
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

순서: (매주 데이터 wipe) → run 시작 → **검색엔진 수집(사이트별 공용
기사 풀, 006)** → **관련도 점수(분류기, 007 — 모델이 없으면 건너뜀)**.
2026-08-24부로 merge_keywords(Gemini 동의어 병합) 단계는 뺐다(pipeline_v2.py
헤더 참고) — "이번 주 주요 키워드" 화면 자체를 없앴다. 한 사이트가 실패해도
나머지는 계속 진행되고, 실패한 항목은 반환값의 `failed`에 남는다(항목별
`error`도 실패로 집계한다 — 조용히 성공으로 마감되던 문제를 막기 위해).

### 수집원·질의 (2026-08-24 개편)

수집원 **17곳**, 넓은 질의 **언어별 2개**, 주 **34크레딧**(월 136, 무료 한도의 14%).

| 질의 | 대상 |
| --- | --- |
| `AI`, `인공지능` | 한국어 사이트 12곳 |
| `AI`, `enterprise AI` | 영어 사이트 5곳 |

**질의는 하나당 1크레딧이다** — 사이트마다 따로 호출한다(한 번에 묶으면 결과가
큰 사이트로 쏠린다). 결과는 발행일순이 아니라 **Tavily의 관련도(score) 내림차순**
이고 호출당 최대 20건이다.

질의를 이렇게 정한 근거(실측):
- 한국 사이트에 영문 `AI`만 던지면 손해다 — `인공지능`으로 바꾸니 전자신문
  2→10건, AI타임스 4→8건. 003이 기록한 "영어 사이트에 한국어 질의" 문제의
  **반대 방향**인데 아무도 확인한 적이 없었다.
- 영어는 `AI startup`보다 `enterprise AI`가 낫다 — 같은 기간 TechCrunch에서
  3건 vs 7건이고, 잡히는 성격도 도입 사례·실적을 함께 커버한다
  (IBM·OpenAI 제휴, 임직원 지분 매각, 밸류에이션 등).
- **`AX`는 영어 사이트에 쓰면 안 된다** — TechCrunch에서 20건이 잡히는데 전부
  Elon Musk의 X, SpaceX, xAI 같은 "X" 매칭이었다. 한국어에서는 통용어지만
  영어권 대응어는 `AI transformation`이다.

**주의 — Tavily 결과는 호출마다 흔들린다.** 같은 조건으로 두 번 던졌는데
10건/2건, 4건/0건이 나온 적이 있다. 그래서 "이 매체는 물량이 없다" 같은 판단을
한 번 던져본 결과로 확정하면 안 되고(지디넷을 그렇게 잘못 배제할 뻔했다),
질의를 2개 이상 두는 것이 "그 주 그 사이트가 통째로 비는" 사고를 막는
안전장치가 된다.

### 추가한 수집원 (2026-08-19)

### 수집원 — 교육 매체 4곳 추가 (2026-08-19)

원래 일반 테크 매체 6곳만 보고 있어서 **"교육" 시장의 후보가 거의 안 걷혔다.**
실측: 수집 기사 152건 중 제목에 교육이 걸린 건 **4건**뿐이라, 라벨을 30건 매기면
전부 "도움 안 됨"이 나왔다(실제로 그렇게 됐다 — 교육 0:30).

원인을 확인해보니 검색어가 아니라 **창문(수집원)이 문제였다.** 교육 검색어를
직접 던져도 주당 8건뿐이었고, 6곳 중 교육 매체는 AskedTech 하나(주 1건)였다.
"교육 기사가 세상에 없는 게 아니라 우리가 보는 곳에 없었다."

후보를 Tavily로 직접 검증한 뒤(3주치, 질의 1개씩) 물량이 나오는 4곳만 넣었다.

| 매체 | 도메인 | 3주치 실측 |
| --- | --- | --- |
| Inside Higher Ed | `insidehighered.com` | 20건(호출 상한) |
| 교육플러스 | `edpl.co.kr` | 8건 |
| 에듀동아 | `edu.donga.com` | 7건 |
| EdWeek | `edweek.org` | 6건 |

**뺀 곳**: 에듀인뉴스(`eduinnews.co.kr`)는 Tavily가 **0건** — 색인이 안 됐거나
크롤링이 막힌 것으로 보여 넣어도 소용이 없다. Times Higher Education은 1건뿐인
데다 그마저 AI와 무관(대학 입시·비자 중심)이라 뺐다.

`SITE_INCLUDE_PATTERNS`의 경로는 검증 때 실제로 돌아온 URL 형태에 맞췄고,
목록·저자 페이지는 exclude로 막았다(기사가 아니라 라벨링할 내용이 없다).
**국내 IT 매체 2곳**(전자신문 `etnews.com`, 블로터 `bloter.net`)도 함께 넣었다.
한국어 매체가 45%인데 **국내 기업이 언급된 기사는 7%**(152건 중 10건)뿐이었다 —
AI타임스·ITWorld·GeekNews가 한국어지만 주로 글로벌 AI 소식을 전하는 성격이라
국내 기업·AX 시장 소식이 비어 있었다. 두 곳 다 검증에서 "플래티어 2분기 AX
솔루션 매출 89% 증가", "LG CNS, 엔비디아·메타 손잡고 국내 신소재 AX 가속화"
같은 기사가 실제로 나왔다.

**지디넷코리아**는 `AI` 질의에서 0건이라 한 번 배제했다가, `인공지능`으로 다시
던지니 2건이 나와 판단을 보류했다 — 질의 하나로 매체를 배제하면 안 된다는
사례로 남긴다. 에듀인쓰(`eduinnews.co.kr`)는 두 질의 모두 0건이라 제외했다.

### 수집 범위 — 최초 3주치, 그 뒤 매주 직전 주 (2026-08-19)

**같은 명령 하나만 쓰면 된다.** 언제 어디서 실행하든 스스로 판단한다.

| 실행 | 걷는 범위 | 크레딧 |
| --- | --- | --- |
| **최초 1회** | **완료된 최근 3주** | 36 |
| **그 뒤 매주 월요일** | **완료된 직전 주**만 | 12 |

**진행 중인 주는 어느 경우에도 걷지 않는다.** 최초에도 뺀다 — 섞으면
"8/17 주"라는 같은 이름의 데이터가 이번엔 사흘치, 다음 주엔 7일치가 되어
주차별 비교가 어긋나고, 라벨의 주차 그룹도 반쪽짜리 주를 하나 더 만든다.
예) 8/19(수)에 최초 실행하면 7/27\~8/02, 8/03\~8/09, 8/10\~8/16을 걷고,
8/24(월)에 8/17\~8/23을 이어받는다.

이번 주가 아니라 **직전 주**를 걷는 게 핵심이다. 진행 중인 주를 걷으면
월·화에 돌렸을 때 이틀치뿐이라 라벨링 후보가 수십 건에 그친다(실측
2026-08-19: 52건). 월요일에 직전 주를 걷으면 항상 완료된 7일치가 들어온다.

최초 여부는 `pipeline_state` 테이블(008)에 남는다 — `weekly_runs`는 매주
TRUNCATE되어 늘 "최초"로 보이고, `article_labels`가 비었는지로 대신 보면
라벨링을 며칠 미룰 때마다 3주치를 다시 긁어 크레딧을 반복해서 쓴다.
**수집이 실제로 성공했을 때만** 기록하므로, 최초 실행이 실패하면 다음 실행이
다시 3주치를 시도한다.

최초 3주치는 주차별로 따로 호출한다 — 3주를 한 번에 요청하면 Tavily의
호출당 20건 상한에 3주치가 눌린다(페이지네이션 없음). 그리고 라벨의 주차
그룹은 run이 아니라 **기사 발행 주**로 잡히므로(`labeling._label_period_start`),
3주치가 자연히 여러 주로 갈려 "지난주 라벨로 학습한 모델이 이번 주 기사에
통하는가"를 바로 측정할 수 있다(`relevance_model.build_groups`가 주차가 둘
이상이면 주차 단위 평가로 자동 전환).

크레딧: 사이트 6개 × 넓은 질의 2개 = **주 12크레딧**(시장 수와 무관).
무료 티어 월 1,000 기준 최초 36 + 매주 12 = 월 약 84.

### 매주 자동 실행 (GitHub Actions, 작업 9)

`.github/workflows/weekly-collect.yml`이 **매주 월요일 01:00 UTC(=10:00 KST)** 에
`pipeline_v2`를 돌린다. 주 1회 몇 분짜리 배치라 서버를 띄워둘 이유가 없다.
Actions 탭에서 "주간 수집 → Run workflow"로 수동 실행도 된다.

저장소 Settings → Secrets and variables → Actions에 아래를 등록해야 한다.

| 시크릿 | 필수 | 비고 |
| --- | --- | --- |
| `DATABASE_URL` | ✅ | 공용 Supabase 주소 |
| `TAVILY_API_KEY` | ✅ | 없으면 수집이 0건이 되고 잡이 실패한다 |
| `GEMINI_API_KEY` | ❌ | 2026-08-24부로 이 워크플로우는 안 씀(merge_keywords 단계 제거) — 등록 안 해도 됨 |

**실행 시각이 01:00 UTC인 이유**: 러너 시계는 UTC이고 파이프라인은
`date.today()`로 주를 계산한다. 한국 시간 기준으로 잡으면(월요일 08:00 KST =
일요일 23:00 UTC) 러너에서는 아직 일요일이라 직전 주가 한 주 더 과거로 밀린다.
01:00 UTC면 UTC·KST 양쪽 다 월요일이라 안전하다.

**실패는 종료 코드로 드러난다.** `pipeline_v2`의 `__main__`이 `report["failed"]`가
비어 있지 않으면 1로 끝나 잡이 빨간불이 된다 — 자동 실행은 로그를 사람이 안
보므로 종료 코드가 유일한 신호다. 항목 하나라도 `error`면 실패로 세므로
(`pipeline_report.stage_errors`) RSS 한 곳이 죽어도 알림이 온다.

`[embedding]` extra까지 설치한다. 빼면 임베딩이 후보에서 빠져 TF-IDF만 남는데,
그게 찍기 기준선을 못 넘으면 그 주는 점수가 아예 없어 화면이 최신순으로 돌아간다
(실측 2026-08-19: TF-IDF 0.550 < 찍기 0.567). 모델 파일(458MB)은 캐시한다.

동시 실행은 `concurrency`로 막는다 — 파이프라인이 시작할 때 TRUNCATE로 지난주
데이터를 비우므로 둘이 겹치면 한쪽이 다른 쪽의 수집분을 지운다.

### 더 과거까지 걷고 싶을 때 (선택)

최초 3주치는 파이프라인이 알아서 하므로 보통은 필요 없다. 라벨 후보를 더
늘리고 싶을 때만 쓴다(Tavily만 소급 가능 — RSS·AI타임스 스크래핑은 원리적으로 불가).

```bash
./.venv/Scripts/python.exe scripts/backfill_past_weeks.py            # 직전 2주 추가
./.venv/Scripts/python.exe scripts/backfill_past_weeks.py --weeks 3
```

`--weeks N`은 **이번 주를 제외한** 과거 N주다(이번 주는 파이프라인이 담당).
가장 최근 run에 덧붙으므로 파이프라인을 먼저 돌린 뒤 실행할 것.

라벨은 매주 wipe를 타지 않지만 **수집된 기사 자체는 다음 파이프라인 실행 때
지워진다** — 걷은 주에 라벨링을 해두는 게 좋다.

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
   56개 URL이 여러 키워드에 걸쳐 있다). 카드 아래 "라벨 검토 및 수정"에서 판단을
   변경하거나 라벨을 취소해 후보로 되돌릴 수 있다(판단이 안 서는 것을 억지로
   고르면 학습 데이터가 오염된다).
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

직접 검색(라이브 호출, DB 저장 안 함) + 고정 키워드 탭(키워드 선택 시 관련
기사만 필터 — "이번 주 주요 키워드" 막대그래프는 2026-08-24에 제거).
계산은 `dashboard_queries.py`가
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
| `market_keywords` | v2/v3 공용 — "이번 주 주요 키워드" 최종 목록. `pipeline` 컬럼(`search_engine`/`rss_llm`)으로 구분. **v2(주간 자동 수집)는 2026-08-24부로 이 테이블을 안 채운다** — `pipeline_v3`(수동 실행)만 계속 채움 | 예 |

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
