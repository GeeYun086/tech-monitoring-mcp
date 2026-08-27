# 기사 모니터링 (tech-monitoring-mcp)

팀이 관심 있는 주제에 대해 **매주 자동으로 관련 기사를 모아 보여주는
도구**입니다. 기사에 👍/👎를 누르면 그 판단을 학습한 분류기가 팀의 취향에
맞춰 기사 순위를 스스로 조정하고, Claude는 MCP로 이 데이터를 직접 읽어
주간 인사이트를 만들어줍니다.

원래 goormEDU 전략기획팀이 AX(AI 전환) 시장을 모니터링하려고 만들었지만,
어떤 팀이든 검색어·수집 사이트·팀 이름만 정하면 이 저장소를 포크해 자기
팀 전용으로 쓸 수 있도록 일반화되어 있습니다.

## 무엇을 하는 도구인가요?

- **자동 수집** — 팀이 정한 검색어로, 팀이 고른 뉴스 사이트에서 매주 월요일
  기사를 자동으로 모읍니다(Tavily Search API).
- **좋아요 기반 순위 학습** — 기사마다 👍/👎를 누르면, 그 판단을 학습 데이터
  삼아 로컬 분류기(scikit-learn)가 "이 팀에 도움되는 기사"를 자동으로 위로
  올려줍니다. 좋아요는 익명 집계라 누가 눌렀는지는 기록되지 않습니다.
- **국내 매체 우선 정렬** — 해외 기사에 묻히지 않도록 국내 매체 기사를
  먼저 보여줍니다.
- **키워드 검색** — 이미 모아둔 이번 주 기사 안에서 제목·요약을 검색합니다.
- **성능 확인** — 분류기가 실제로 얼마나 잘 맞히는지 정확도·정밀도·재현율로
  확인할 수 있습니다.
- **Claude 연동(MCP)** — Claude가 이번 주 기사·인기 기사를 직접 조회해
  주간 보고서나 인사이트를 써줄 수 있습니다.
- **팀별 독립 배포** — 이 저장소를 포크해 각자 배포하면, 팀마다 완전히
  독립된 검색어·기사·좋아요·학습 데이터를 갖습니다. 한 팀의 배포 링크에
  접속한 사람들끼리만 데이터를 공유합니다.

## 빠른 시작 — 우리 팀 배포 만들기

**git이나 코드를 몰라도 아래 순서만 그대로 따라가면 됩니다** — 로컬에
아무것도 설치할 필요 없이, 전부 웹 화면에서 끝납니다. 계정 생성과 발급받은
키를 GitHub/Streamlit Secrets에 입력하는 것만 사람이 직접 해야 하고(AI
에이전트가 대신할 수 없는 영역입니다), 그 외 코드 작업(포크 손보기, 새
사이트 추가 등)은 Claude Code에 맡겨도 됩니다.

0. **GitHub 계정을 준비합니다.** 이미 있으면 그대로 쓰고, 팀 전용 계정을
   새로 만들어도 됩니다.
1. 이 저장소를 [Fork](https://github.com/GeeYun086/tech-monitoring-mcp/fork)합니다
   (우측 상단 Fork 버튼 — 방금 준비한 계정으로 로그인한 상태여야 합니다).
2. [Supabase](https://supabase.com)에서 무료 프로젝트를 만들고 `DATABASE_URL`을 받습니다
   (Connect 화면이 골라주는 연결 문자열을 포트 상관없이 그대로 쓰면 됩니다 —
   이 프로젝트는 6543번 트랜잭션 풀러·5432번 세션 풀러 둘 다 안전하게
   지원합니다).
3. [Tavily](https://tavily.com)에서 무료 API 키를 발급받습니다(카드 등록 불필요).
4. 포크한 저장소를 [Streamlit Community Cloud](https://streamlit.io/cloud)에 배포합니다
   (Main file path는 `app/streamlit_app.py`). 배포 화면의 Secrets에
   `DATABASE_URL`·`TAVILY_API_KEY`를 등록합니다. **DB 테이블은 앱이 처음
   뜰 때 자동으로 만들어지므로 따로 준비할 것이 없습니다.**
5. 배포된 링크를 열면 **"처음 오셨네요 — 팀을 설정해주세요"** 화면이 뜹니다.
   팀 이름, 검색어(한국어/영어), 수집할 사이트를 입력하고 "시작하기"를
   누르면 그 자리에서 첫 수집까지 끝납니다(사이트·검색어 수에 따라 몇 분
   걸릴 수 있습니다).
6. 저장소 Settings → Actions에서 GitHub Actions를 켭니다(포크 저장소는
   기본적으로 꺼져 있습니다 — GitHub 보안 기본값). Settings → Secrets and
   variables → Actions에 같은 두 값을 등록하면 이후 매주 월요일 자동으로
   새 기사를 수집합니다.
7. 배포 링크를 팀원에게 공유하세요. 접속한 사람은 전부 같은 기사·좋아요를
   보고, 좋아요는 이 팀 안에서만 쌓여 학습됩니다.

로컬 PC에서 코드를 직접 손보고 싶다면(예: 새 수집 사이트 추가, 버그 수정)
[로컬 개발 환경 준비](#로컬-개발-환경-준비)를 참고하세요 — 위 순서와는
별개로, 선택 사항입니다.

## 대시보드 사용법

배포된 링크를 열면 세 개의 탭이 있습니다.

- **`<팀 이름>` 탭(기본 화면)** — 이번 주 수집된 기사 전체를 보여줍니다.
  국내 매체 우선, 그다음 좋아요 많은 순, 그다음 분류기 점수순으로
  정렬됩니다. 각 기사 아래 👍/👎로 판단을 남기면 곧바로 저장되고, 라벨이
  5건 쌓일 때마다 분류기가 자동으로 다시 학습해 순위를 갱신합니다.
  상단 검색창에 키워드를 넣으면 이번 주 기사 안에서 제목·요약이 비슷한
  것부터 찾아줍니다.
- **📈 성능 탭** — 지금까지 쌓인 좋아요/싫어요로 분류기를 채점합니다.
  정확도는 항상 "무조건 다수 쪽으로 찍었을 때"의 기준선과 함께 표시되어
  착시 없이 볼 수 있습니다.
- **⚙️ 설정 탭** — 검색어·수집 사이트를 CLI 없이 화면에서 바로 조회·변경합니다.
  저장한 값은 다음 자동 수집(매주 월요일)부터 반영되고, 이번 주 이미
  수집된 기사는 그대로 유지됩니다. 팀 이름 자체를 바꾸려면 아래
  [팀 설정 나중에 바꾸기](#팀-설정-나중에-바꾸기)의 CLI를 씁니다.

## 아키텍처 한눈에 보기

```
팀 설정(이름 · 검색어 · 수집 사이트)
    ↓
Tavily Search API로 사이트별 수집 + URL 화이트리스트로 이중 검증
    ↓
collected_articles (이번 주 기사 풀)
    ↓ 사람이 매긴 👍/👎(anonymous)를 학습 데이터로
로컬 분류기(문자 n-gram TF-IDF 또는 다국어 문장 임베딩) → 관련도 점수
    ↓
대시보드(Streamlit) 정렬·표시, MCP 서버로 Claude가 직접 조회
```

**매주 데이터를 통째로 비우고 다시 모읍니다.** 무료 DB 티어를 유지하기
위한 설계로, `fixed_keywords`(팀 설정)와 `article_labels`(사람이 매긴
판단)만 보존되고 나머지 수집 데이터는 매주 새로 채워집니다. 좋아요·라벨은
절대 사라지지 않습니다.

## 배포 가이드

표준 배포 순서는 위 ["빠른 시작"](#빠른-시작--우리-팀-배포-만들기)이 전부입니다
(git을 몰라도 그대로 따라가면 됩니다). 아래는 **로컬 PC에서 직접 코드를
손보거나 디버깅하고 싶을 때만** 필요한 내용입니다 — 새 배포를 만들 때도,
이미 있는 팀 DB에 개발자로 합류할 때도 같은 방법을 씁니다.

### 로컬 개발 환경 준비

```bash
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env   # DATABASE_URL·TAVILY_API_KEY 채우기 — 새로 만든
                        # Supabase 프로젝트라면 거기서 받은 값, 이미 있는
                        # 팀 DB(예: goormEDU 전략기획팀 배포)에 합류한다면
                        # 담당자에게 받은 값
./.venv/Scripts/python.exe -m tech_monitoring.db.migrate   # 이미 최신이면 전부 건너뜀 —
                                                             # 대시보드 화면도 뜰 때 자동으로
                                                             # 실행하지만, 화면보다 먼저
                                                             # 파이프라인·스크립트를 돌릴
                                                             # 계획이면 미리 한 번 해두면 안전
./.venv/Scripts/python.exe -m pytest tests/ -q              # 세팅 확인
```

**기존 팀 DB에 합류하는 경우, 절대 `.env.example`의 `localhost` 기본값을
그대로 두지 마세요.** 그러면 로컬에만 존재하는 빈 DB를 보게 되어, 기사를
새로 수집하느라 Tavily 크레딧을 낭비하고 라벨(팀의 판단 자산)이 갈라집니다.
항상 팀이 쓰는 Supabase 주소를 넣으세요 — DB는 이미 클라우드에 있으므로
별도로 띄울 것이 없습니다. `docker-compose.yml`의 Postgres는 순수 로컬
실험 전용입니다.

### 설정값(Secrets) 참고

| 값 | 어디에 필요한가 | 비고 |
| --- | --- | --- |
| `DATABASE_URL` | 로컬 `.env`, Streamlit Cloud, GitHub Actions | Supabase Postgres 연결 문자열 |
| `TAVILY_API_KEY` | 로컬 `.env`, Streamlit Cloud, GitHub Actions | 기사 수집용. 무료 티어 월 1,000크레딧, 카드 등록 불필요 |
| `GEMINI_API_KEY` | 로컬 `.env`(선택) | 매주 자동 수집에는 안 쓰임. `analysis/keyword_merge.py`를 단독 실행할 때만 필요 |

## MCP 서버 — Claude가 수집 결과를 직접 조회

```bash
./.venv/Scripts/python.exe -m tech_monitoring.mcp_server
```

읽기 전용입니다 — 수집·학습은 파이프라인과 대시보드가 하고, MCP 서버는
이미 DB에 있는 결과를 꺼내 보여주기만 합니다. 그래서 머신러닝 라이브러리가
필요 없습니다(psycopg만 있으면 됩니다).

| 도구 | 하는 일 |
| --- | --- |
| `get_status` | 기준 기간·기사 수·주차별 건수·시장 목록·라벨 현황 |
| `get_markets` | 시장 목록과 시장별 라벨 진행 상황 |
| `get_articles(market, limit)` | 한 시장의 기사 — 국내 매체 우선 + 좋아요 수 + 분류기 점수순 |
| `get_popular_articles(market, limit)` | 👍를 가장 많이 받은 기사만 — 주간 인사이트·보고서를 쓸 때 분류기 점수보다 먼저 참고할 값 |
| `get_keywords(market, limit)` | 한 시장의 주요 키워드(보조 지표) |

응답에는 정렬 근거(`ordering`)와 전체 건수(`total`)가 함께 담겨, 잘린
결과나 임시 정렬을 추천 순위로 오해하지 않게 합니다.

### 도커로 배포하기 (남에게 줄 때)

저장소를 클론할 필요 없이 `docker run` 한 줄이면 됩니다. `main`에 소스가
바뀔 때마다 GitHub Actions가 자동으로 이미지를 구워 GHCR에 올려둡니다.

```bash
docker pull ghcr.io/geeyun086/tech-monitoring-mcp:latest
```

```json
{
  "mcpServers": {
    "tech-monitoring": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
               "-e", "DATABASE_URL=postgresql://...",
               "ghcr.io/geeyun086/tech-monitoring-mcp:latest"]
    }
  }
}
```

이 저장소가 비공개라 이미지도 비공개입니다. 접근 권한이 있다면 pull 전에
한 번 로그인하세요: `echo <개인 액세스 토큰(read:packages)> | docker login ghcr.io -u <GitHub 아이디> --password-stdin`

이미지는 머신러닝 라이브러리 없이 psycopg + mcp만 담아 가볍습니다(약
274MB). 로컬에서 직접 빌드하려면 `docker build -t tech-monitoring-mcp .`

Claude Code에서는 저장소 루트의 `.mcp.json`으로 자동 연결됩니다. 다른
클라이언트(Claude Desktop 등)는 아래처럼 등록합니다:

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

## 팀 설정 나중에 바꾸기

배포 후에도 팀 이름·검색어·수집 사이트를 CLI로 조정할 수 있습니다:

```bash
./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py list
./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py set-terms "<팀 이름>" --ko "AI 교육,에듀테크" --en "AI education,edtech"
./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py set-sites "<팀 이름>" --sites "aitimes.com,edu.donga.com"
./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py rename "<옛 이름>" "<새 이름>"
```

검색어를 비우면 넓은 질의("AI"/"인공지능")로, 사이트를 비우면 전체
화이트리스트로 동작합니다.

### 새 수집 사이트 추가하기

화면에서도 CLI에서도 **이미 검증된 화이트리스트 사이트 중에서만** 고를 수
있습니다. 완전히 새로운 사이트를 추가하려면 코드 수정이 필요합니다:

1. 그 사이트에서 실제 기사 URL 몇 개를 모아 공통 패턴을 확인합니다(예:
   `articleView.html?idxno=12345`).
2. `collectors/search_engine.py`의 `SITE_INCLUDE_PATTERNS`/
   `SITE_EXCLUDE_PATTERNS`/`SITE_DOMAINS`/`KOREAN_DOMAINS`/`SITE_NAMES`에
   추가합니다(기존 17곳 항목을 참고).
3. `tests/test_search_engine.py`에 그 사이트의 실제 URL로 통과/차단
   테스트를 추가해 패턴을 검증합니다.

패턴 검증 없이 사이트를 추가하면 홈페이지·목록·광고 페이지가 기사인 것처럼
섞여 들어오기 때문에, 이 단계만큼은 자동화하지 않았습니다.

## 파이프라인 상세

```bash
./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v2
```

순서: (매주 데이터 wipe) → run 시작 → **팀 설정대로 사이트별 수집** →
**분류기로 관련도 점수 매기기**(모델이 없으면 건너뜀). 한 사이트가
실패해도 나머지는 계속 진행되고, 실패한 항목은 반환값의 `failed`에
남습니다.

**최초 실행이든 그 이후든 매주 직전 완료된 한 주만** 걷습니다
(`db/weekly_run.py`가 판단, 2026-08-27부로 최초 3주 소급 부트스트랩 폐지).
진행 중인 주(이번 주)는 어느 경우에도 걷지 않습니다 — 완료된 주만 걷어야
주차별 건수가 항상 온전합니다. 라벨 후보를 더 늘리고 싶으면 아래
"더 과거까지 걷고 싶을 때"의 `scripts/backfill_past_weeks.py`를 따로
실행하세요.

**GitHub Actions**(`.github/workflows/weekly-collect.yml`)가 매주 월요일
01:00 UTC(=10:00 KST)에 자동 실행합니다. Actions 탭에서 수동 실행도
가능합니다. 실패는 종료 코드로 드러나 잡이 빨간불이 됩니다.

### Tavily 크레딧 사용량

크레딧은 `사이트 수 × 검색어 수`에 비례합니다(질의 하나당 1크레딧). 예:
사이트 12곳 + 검색어 2개면 주 24크레딧, 무료 티어 월 1,000크레딧 안에서
넉넉합니다. 결과는 발행일순이 아니라 Tavily 자체 관련도 점수순이고
호출당 최대 20건입니다.

같은 조건이라도 Tavily 결과는 호출마다 다소 흔들릴 수 있어서, 검색어를
2개 이상 두는 것을 권합니다 — 한 번의 빈 결과로 "이 사이트는 물량이 없다"고
판단하지 않기 위한 안전장치입니다.

### 더 과거까지 걷고 싶을 때 (선택)

라벨 후보를 늘리고 싶을 때만 씁니다(Tavily로만 소급 가능):

```bash
./.venv/Scripts/python.exe scripts/backfill_past_weeks.py            # 직전 2주 추가
./.venv/Scripts/python.exe scripts/backfill_past_weeks.py --weeks 3
```

`--weeks N`은 이번 주를 제외한 과거 N주입니다. 가장 최근 run에 덧붙으므로
파이프라인을 먼저 돌린 뒤 실행하세요. 수집된 기사 자체는 다음 파이프라인
실행 때 지워지므로, 걷은 주에는 미리 라벨링(👍/👎)을 해두는 게 좋습니다.

## 관련도 분류기 — 사람이 매긴 좋아요로 학습

기사가 "이 팀에 도움되는가"는 LLM이 아니라 **팀이 직접 누른 👍/👎로 학습한
로컬 분류기**가 판단합니다 — API 호출이 없어 요금제·장애와 무관하게
항상 동작합니다.

- 문자 n-gram TF-IDF와 다국어 문장 임베딩 두 방식을 같은 조건으로
  교차검증해 더 나은 쪽을 씁니다(임베딩은 로컬 개발 환경에서만 — 배포
  환경은 용량 때문에 TF-IDF만 씁니다).
- 라벨이 5건 쌓일 때마다 자동으로 다시 학습하고 이번 주 순위를 갱신합니다.
- 정확도는 항상 "무조건 다수 쪽으로 찍었을 때"의 기준선과 함께 표시됩니다.
- 좋아요는 익명입니다 — 클릭마다 무작위 토큰이 발급되어 개수만 쌓이고
  누가 눌렀는지는 저장되지 않습니다.

## 개발하기

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q             # 전체 테스트
./.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py   # 대시보드 로컬 실행
```

### 스키마

마이그레이션은 `db/migrations/`에 001~009로 누적되어 있고 전부
`IF NOT EXISTS`라 여러 번 적용해도 안전합니다.

| 테이블 | 역할 | 매주 wipe? |
| --- | --- | --- |
| `fixed_keywords` | 팀 설정(이름·검색어·수집 사이트) | 아니오 — 설정값 |
| `weekly_runs` | 주간 배치 실행 메타. 다른 수집 테이블은 이 테이블에 cascade 연결 | 예(wipe 트리거) |
| `collected_articles` | 이번 주 수집된 기사 풀 | 예 |
| `article_keyword_relevance` | 기사별 분류기 점수 | 예 |
| `article_labels` | 사람이 매긴 👍/👎(분류기 학습 데이터). 원본을 스냅샷으로 보존 | **아니오 — 학습 자산** |
| `pipeline_state` | 최초 부트스트랩(첫 수집) 완료 여부 | 아니오 — 설정값 |
| `search_results`, `market_keywords` | 예전 파이프라인의 잔존 테이블(현재 자동 수집 경로는 안 씀) | 예 |

### 프로젝트 구조

```
app/streamlit_app.py              # 대시보드 화면(레이아웃만, 계산은 dashboard_queries가 담당)
src/tech_monitoring/
  collectors/search_engine.py     # Tavily 수집 + 화이트리스트 이중 검증
  labeling.py                     # 좋아요 저장/조회
  relevance_model.py              # 라벨로 분류기 학습 + 교차검증 채점
  dashboard_queries.py            # 대시보드가 쓰는 조회·정렬 로직
  pipeline_v2.py                  # 주간 파이프라인 오케스트레이터(매주 wipe 담당)
  mcp_server/                     # Claude용 MCP 서버
  db/connection.py, db/migrate.py, db/weekly_run.py
db/migrations/                    # 001~009 누적 마이그레이션
scripts/manage_fixed_keywords.py  # 팀 설정 CLI(이름·검색어·사이트)
scripts/backfill_past_weeks.py    # 과거 주차 소급 수집
scripts/train_relevance_classifier.py  # 라벨 → 분류기 학습 + 성능 출력
models/                           # 학습된 분류기(.joblib, git 추적 안 함 — 라벨에서 재생성)
```

## 참고: 이전 세대(v3) 비교 실험

`pipeline_v3`는 검색엔진 기반 수집(v2) 대신 RSS·스크래핑으로 소스를 모으고
관련도 판단을 LLM에 맡기는 실험적 경로입니다. 현재 자동 수집(GitHub
Actions)은 v2만 씁니다. v3를 직접 비교해보고 싶다면 `pipeline_v2`를 먼저
실행한 뒤(v3는 wipe를 하지 않고 v2가 만든 run 위에 얹힙니다):

```bash
./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v3
```
