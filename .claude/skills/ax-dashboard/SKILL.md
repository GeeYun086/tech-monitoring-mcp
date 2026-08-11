---
name: ax-dashboard
description: Render a live AX(AI 전환) market monitoring dashboard as a Claude Artifact from the tech-monitoring MCP's data (impact-ranked issues, signal breakdown, keyword trends). Use this whenever the person in charge asks to see, visualize, or get a dashboard/report view of AX 시장 이슈·주간 다이제스트·모니터링 현황 — e.g. "대시보드 보여줘", "이번주 이슈 시각화해줘", "인사이트 대시보드 만들어줘", "지금 상태 한눈에 보고 싶어" — even if they don't say the word "dashboard" but clearly want a visual/at-a-glance view of monitored issues rather than a text answer. Also use this proactively as a diagnostic aid while tuning the relevance/clustering pipeline (Phase 3), so changes are visible rather than described only in text.
---

# AX 대시보드

`tech-monitoring-mcp`가 수집·필터링한 AX 시장 이슈를 파급력 순으로 시각화한다.
목적은 두 가지다: (1) 담당자가 텍스트 설명 대신 한눈에 훑어볼 수 있게, (2) Phase 3
필터 튜닝 중 결과를 눈으로 바로 확인하는 진단 도구로 쓴다. **아직 최종 완성형이
아니다** — 관련도·클러스터링 정확도가 계속 개선되고 있으므로, 매번 지금 데이터
기준으로 새로 만든다.

## 왜 숫자 계산을 직접 하면 안 되는가

과거 한 번, 이 화면을 만들 때 impact_score의 신호별 기여도(가중치×값)를 손으로
계산해서 HTML에 박아 넣은 적이 있다. 데이터가 바뀔 때마다 다시 계산해야 하고
실수하기 쉬워서 재사용이 안 됐다. **이 스킬은 반드시 `scripts/dashboard_data.py`가
숫자를 계산하게 하고, Claude는 번역·문구·레이아웃 판단만 한다.** 이 분리를
지키지 않으면(즉 impact_signals를 보고 직접 퍼센트를 암산하면) 매번 오차가 쌓인다.

## 절차

### 1. 기간·조건 파악

사용자 표현에서 기간을 추출한다. 기본값: `period=last_week`, `limit=20`, `min_impact=0.0`.
"이번 주"→`this_week`, "최근 N일"→`Nd`, 특정 범위 언급 시 `YYYY-MM-DD..YYYY-MM-DD`.
(형식은 MCP `get_weekly_digest`와 동일 — [queries.py](../../../src/tech_monitoring/mcp_server/queries.py)의
`resolve_period`/`parse_since` 참고.)

### 2. 데이터 준비 스크립트 실행

```bash
./.venv/Scripts/python.exe scripts/dashboard_data.py --period last_week --limit 20 --min-impact 0.0
```

stdout으로 JSON 하나를 출력한다. 구조:

```jsonc
{
  "period": {"label": "...", "start": "...", "end": "..."},
  "total_articles": 143,
  "total_clusters": 140,
  "clusters": [
    {
      "cluster_id": "cluster-157",
      "size": 2,
      "lead": {
        "id": 87, "title": "...", "url": "...", "source": "...", "source_type": "...",
        "published_at": "...", "summary": "...",
        "impact_score": 0.8165, "relevance_score": 0.0069,
        "impact_signals": { "source_trust": 0.9, "aggregator_signal": 0.99,
                             "cluster_size": 0.4, "recency": 0.8698,
                             "relevance_rerank_score": 0.0099, "...": "..." },
        "impact_breakdown_pct": {"trust": 38.6, "agg": 30.3, "cluster": 9.8, "recency": 21.3}
      },
      "related": [{"title": "...", "url": "...", "source": "...", "impact_score": 0.58}]
    }
  ],
  "keywords": [{"word": "AI", "count": 12}, {"word": "agent", "count": 7}]
}
```

`impact_breakdown_pct`는 이미 4개 신호(trust/agg/cluster/recency)의 상대 비중을
백분율로 정규화해뒀다 — 이 값을 그대로 막대 폭(%)으로 쓰면 된다. 다시 계산하지 말 것.

`keywords`는 필터를 통과한 실제 기사 제목·요약에서 빈도 집계한 것이다.
이건 **관련도를 좁히는 필터 기준이 아니라 "발견된 주제어" 참고 표시**다 —
당연히 무관한 단어가 섞일 수 있으니 그대로 노출하고 판단은 사용자에게 맡긴다.

스크립트가 있는데도 못 찾겠으면 먼저 그것부터 확인한다(새로 만들지 않는다) —
없다면 데이터 스키마는 위 예시와 [queries.py](../../../src/tech_monitoring/mcp_server/queries.py)의
`get_weekly_digest`/`search_news` 반환값을 참고해 최소한으로 만든다.

### 2-1. 트렌드 데이터 (2026-08-11 확장)

담당자 지적: "파급력 상위 기사 나열"만으로는 트렌드나 산업 동향을 한눈에
보기 어렵다. 그래서 위 `clusters`(상위 N건으로 잘린 목록) 옆에, JSON
최상위 `trends` 키로 **필터를 통과한 전체 데이터셋**(`--trend-days`로
지정한 기간, 기본 14일, 자르지 않음) 기준 집계가 함께 나온다:

```jsonc
"trends": {
  "window_days": 14,
  "total_domestic": 110, "total_global": 142,
  "volume_trend": [{"date": "2026-08-10", "domestic": 57, "global": 20}, ...],
  "source_distribution": [{"source": "arXiv cs.AI", "region": "global", "count": 49}, ...],
  "impact_distribution": [{"bucket_start": 0.4, "domestic": 59, "global": 86}, ...],
  "keywords_domestic": [{"word": "AI", "count": 87}, ...],
  "keywords_global": [{"word": "language models", "count": 13, "doc_freq": 13, "score": 11.9}, ...]
}
```

`keywords_domestic`과 `keywords_global`의 필드가 다르다 — 2026-08-11 3차
확장에서 해외만 구(phrase)+TF-IDF로 바뀌었기 때문이다(`doc_freq`·`score`
필드는 해외에만 있다). 이유는 아래 2-3절 참고.

`region`(국내/해외)은 `dashboard_data.py`의 `DOMESTIC_SOURCES` — **등록된
소스 이름 목록** 기반이다. 언어 자동 감지가 아니다(언어 감지는 새 소스가
추가될 때마다 오분류 위험이 있어 명시적 목록 방식을 택했다). 새 소스를
추가하면 이 목록도 같이 갱신해야 국내/해외 집계가 정확하다.

수치는 이미 다 계산되어 있다 — Claude는 이 JSON의 좌표(SVG path,
막대 폭 %, 원 위치 등)를 **직접 계산해서** HTML에 넣어야 한다(스크립트가
숫자는 주지만 차트 픽셀 좌표까지는 만들어주지 않는다). 암산하지 말고
필요하면 `python -c "..."`로 작은 변환 스크립트를 짜서 좌표를 뽑는다 —
"숫자 계산은 직접 하지 않는다"는 이 스킬의 원칙은 여기도 그대로 적용된다.

### 2-2. 산업 동향 신호 (2026-08-11 2차 확장)

담당자 재지적: "지금 지표는 수집 방법(얼마나 모았는가) 지표지 산업 동향
지표가 아니다." `--compare-days`(기본 7일)로 지정한 두 구간(이번 기간 vs
그 직전 같은 길이 기간, 겹치지 않음)을 비교해 `trends`에 7개 필드가
추가된다:

```jsonc
"trends": {
  "compare_days": 7,
  "keyword_network": {"nodes": [{"word": "AI", "count": 84}, ...],
                       "edges": [{"source": "AI", "target": "모델", "weight": 17}, ...]},
  "rising_keywords": [{"word": "LLM", "share_now_pct": 8.18, "baseline_avg_pct": 0.0,
                        "z_score": 163.6, "is_new": true}, ...],
  "keyword_bubbles": [{"word": "LLM", "mention_count": 25, "growth_rate": 24.0,
                        "source_count": 9, "is_new": false}, ...],
  "keyword_gap": {"global_only": [{"word": "models", "domestic_share": 0.0, "global_share": 22.76}, ...],
                  "domestic_only": [{"word": "AX", "domestic_share": 12.15, "global_share": 0.0}, ...]},
  "entity_ranking": [{"entity": "OpenAI", "count": 6}, ...],
  "cross_region_lag": {"count": 1, "avg_lag_hours": 122.4,
                        "pairs": [{"cluster_id": "...", "global_source": "...", "global_title": "...",
                                   "global_published_at": "...", "domestic_source": "...",
                                   "domestic_title": "...", "domestic_published_at": "...",
                                   "lag_hours": 122.4}]},
  "co_report_intensity": [{"date": "2026-08-10", "big_cluster_count": 2}, ...]
}
```

각 지표가 무엇을 근사하는지, 왜 그렇게 계산했는지는 `scripts/dashboard_data.py`의
`compute_*` 함수 docstring에 있다 — 특히 다음 셋은 반드시 읽고 화면에 반영한다:

- **`keyword_bubbles`는 "AI"·"인공지능"을 반드시 화면에서 제외한다.** 이
  데이터셋의 주제어 자체라 항상 언급량 1위를 차지해서 다른 신호를 다
  가린다(스크립트는 원값을 그대로 반환하므로, 이 필터링은 Claude가
  렌더링 시점에 한다). `rising_keywords`는 2026-08-11 3차 확장에서
  비중(share) 기반 anomaly score로 바뀌면서 이 문제가 공식 안에서 자연히
  해소됐다 — 별도 제외 없이 그대로 상위 N개를 써도 된다(아래 2-3절 참고).
- **`keyword_gap`은 영문 토큰만 비교한 결과다** — "모델"(국내)과
  "model"(해외) 같은 번역어 차이로 인한 착시를 막기 위해서다. 그 결과
  `domestic_only`가 짧거나 비어 있을 수 있는데, 이건 버그가 아니라
  "국내 매체가 아직 다루지 않는 해외발 화제가 많다"는 정직한 결과다.
- **`cross_region_lag`은 반드시 스팟체크한다.** 클러스터링이 한글 제목에는
  고유명사 겹침 게이트를 적용하지 않는 기존 한계 때문에, 표본이 적을 때
  (`count`가 1~2건일 때 특히) 서로 다른 주제가 우연히 같은 클러스터로
  묶인 오탐일 수 있다. `pairs`의 `global_title`/`domestic_title`을 읽고
  실제로 같은 이슈인지 판단한 뒤, 아니라고 판단되면 그 예시를 그대로 보여주지
  말고 "이번 기간엔 신뢰할 사례가 없음 + 이유"로 대체한다(실사용 중 발견:
  McKinsey의 일반적인 "AI as force multiplier" 글과 GeekNews의 무관한
  "AI 코딩 에이전트 개발환경" 글이 이렇게 잘못 묶인 적이 있다).

### 2-3. 워드클라우드·급상승 키워드 재설계 (2026-08-11 3차 확장)

담당자 3차 재지적: "결과가 너무 일반적이다." 원인 진단 — 단어 하나
(unigram) 단위 토큰화라 "AI"·"모델" 같은 최상위 개념어가 항상 지배하고,
`rising_keywords`(당시 이름)는 직전 1개 기간과의 절대 증가폭만 봐서
수집량 자체가 급변하면(8월 초 새 소스 추가로 주간 수집량이 6~8배 급증)
착시가 생겼다. 두 가지를 바꿨다:

1. `keywords_global`은 이제 **구(phrase, 1~2단어) 후보 + TF-IDF**로
   순위를 매긴다. `keywords_domestic`은 **그대로 유니그램+원시 빈도**다.
   **비대칭인 이유**: 한국어는 형태소 분석이 없어 "기술을"·"모델을"처럼
   조사가 붙은 채로 별도 토큰이 되는데, TF-IDF가 "너무 흔하지도 너무
   드물지도 않은" 중간 빈도를 우대하는 특성과 만나면 이런 조사 결합형이
   대거 상위권을 차지해 버린다(실사용 중 발견 — 시도했다가 되돌림).
   영어는 단어가 이미 공백으로 분리돼 있어 이 문제가 없다. 국내 워드클라우드에
   같은 처리를 적용하려면 형태소 분석기가 먼저 필요하다.
2. `rising_keywords`는 절대 증가폭 대신 **비중(share, %) 기반 z-score**로
   바뀌었다 — `baseline_avg_pct`(기본 3개 이전 기간의 평균 비중) 대비
   `share_now_pct`가 얼마나 벗어났는지. 표준편차까지 반영해 "원래 들쭉날쭉한
   단어"와 "진짜 새로 뜬 단어"를 구분한다.
3. `keyword_lifecycle`(신규): 상위 구의 일별 언급 추이. `{"terms": [...],
   "series": [{"date": "...", "<term>": count, ...}, ...]}` 형태.

**시도했다가 되돌린 것**: `_article_text()`가 title+summary 대신
content(전체 본문)를 쓰게 해봤다. 실사용 검증 결과 (1) 일부 페이지의
본문 추출 과정에서 섞여 들어온 UI 네비게이션 텍스트("PDF"·"Explorer"·
"Finder" 등)가 급상승 키워드 상위를 오염시켰고, (2) 흔한 서술어·부사가
summary 전용으로 만든 스톱워드 목록으로 걸러지지 않아 워드클라우드
상위를 대신 차지했다 — 결과가 오히려 더 나빠져서 되돌렸다. **본문을
쓰는 건 다시 시도하지 말 것** — 먼저 스톱워드 목록을 본문 분량에 맞게
대폭 확장하거나 품사 태깅 같은 실제 NLP가 있어야 한다.

### 3. Claude가 하는 일 — 번역과 문구

스크립트는 숫자만 다룬다. 원문 영어 제목·요약을 **간결한 한국어**로 옮기는 건
Claude 몫이다(담당자가 "영어라 뭐가 유의미한지 모르겠다"고 명시적으로 지적한 지점).
직역보다 핵심이 바로 들어오는 의역을 우선한다. 원문 URL은 그대로 링크로 남겨
원문을 확인할 수 있게 한다.

### 4. HTML 작성 — 기존에 검증된 디자인을 따른다

첫 데모(2026-08-07)에서 이미 컨셉을 잡아 담당자 확인을 거쳤다 — 새로 고민하지
말고 그대로 이어간다("AX 시장 모니터링 콘솔" 컨셉).

**컬러 토큰** (라이트/다크 둘 다 정의, `:root` + `@media (prefers-color-scheme: dark)`
+ `:root[data-theme="dark"]`/`[data-theme="light"]` 오버라이드 패턴 — 뷰어 토글이
미디어쿼리를 이겨야 한다):

| 역할 | 라이트 | 다크 |
| --- | --- | --- |
| 페이지 배경 | `#eef1f3` | `#0c1012` |
| 카드/패널 | `#f8f9fb` | `#141a1d` |
| 본문 잉크 | `#0d1416` | `#eef2f3` |
| 보조 잉크 | `#4c5c60` | `#a3b3b6` |
| 헤어라인 | `#c7d0d2` | `#2a3436` |
| 액센트(신호/aggregator) | `#2a78d6` | `#3987e5` |

**4개 신호 색 — impact_breakdown_pct 막대의 세그먼트 색 (고정, 매번 같은 색):**

| 신호 | 의미 | 라이트 | 다크 |
| --- | --- | --- | --- |
| `agg` | 반향(aggregator_signal) | `#2a78d6` | `#3987e5` |
| `trust` | 소스 신뢰도(source_trust) | `#eb6834` | `#d95926` |
| `cluster` | 동시보도(cluster_size) | `#1baf7a` | `#199e70` |
| `recency` | 최신성 | `#eda100` | `#c98500` |

**국내/해외 2색 — 트렌드 섹션(추이·소스분포·파급력분포·워드클라우드) 전용,
고정 카테고리 순서(파란색=1번, 주황색=2번)를 그대로 재사용:**

| 구분 | 라이트 | 다크 |
| --- | --- | --- |
| 국내(`--region-domestic`) | `#2a78d6` | `#3987e5` |
| 해외(`--region-global`) | `#eb6834` | `#d95926` |

`--sig-agg`/`--sig-trust`와 같은 hex 값을 재사용하지만 의미가 다른
별도 CSS 변수(`--region-domestic`/`--region-global`)로 정의한다 — 두
섹션이 같은 페이지에 있으므로 각 차트마다 범례를 반드시 붙여 색의
의미가 섞이지 않게 한다(dataviz 스킬: "2개 이상 시리즈는 항상 범례").

**타이포그래피**: 기사 제목(번역문)만 세리프(`Georgia, "Iowan Old Style", "Palatino Linotype", serif`),
UI 텍스트는 시스템 산세리프(`-apple-system, "Segoe UI", sans-serif`), **모든 숫자·날짜·점수는
모노스페이스**(`ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace`) +
`font-variant-numeric: tabular-nums`.

**레이아웃** (위→아래, 2026-08-11 트렌드 섹션 추가로 갱신):
1. 마스트헤드 — 제목, 기간(이슈 목록 기간 + 트렌드 집계 기간을 둘 다 명시),
   스탯 리드아웃(스캔 기사 수·클러스터 수·표시 건수·14일 국내:해외 비율, 전부 mono)
2. **"트렌드 & 산업 동향" 섹션** — `trends` 데이터로 채운다. 상위 N건이 아니라
   전체 데이터셋 기준임을 소제목 밑에 한 줄로 명시한다.
   - 일별 수집량 추이: 국내/해외 2계열 라인차트(+10% 오퍼시티 영역 채움), 끝점에
     values, 각 점에 `<title>` 툴팁, 날짜 라벨은 2~3일 간격으로만(빽빽하면 스킵)
   - 소스별 수집 건수: 최다 소스 대비 비율로 막대 폭 계산한 가로 막대 목록(상위 12),
     국내/해외 색 구분
   - 파급력 점수 분포: 0.1 단위 10개 구간 그룹 막대(국내/해외 나란히), 데이터 없는
     구간도 축에 전부 표시(생략 금지 — `compute_impact_distribution`이 항상 10개 반환)
   - 국내/해외 키워드: 두 컬럼, count 구간별 칩 크기(4단계: t1~t4)
   - 국내/해외 키워드는 형태소 분석 없는 단순 토큰화 결과라 "AI가"처럼 조사가
     붙은 항목이 섞일 수 있음을 신호 품질 배너에서 짚어준다(감추지 않는다)
3. **"산업 동향 신호" 섹션** (2026-08-11 2차 확장) — 위 트렌드가 "수집 방법"
   지표라면 이건 "산업 동향" 지표다. 소제목 밑에 "감성분석·기술/지역 태깅
   없이 지금 데이터로 만든 근사치"임을 한 줄로 명시한다.
   - 급상승 키워드: `rising_keywords`를 칩으로(단어 + `+delta`), NEW는 다른
     색 테두리. "AI"·"인공지능" 제외.
   - 키워드 부상도 버블차트: `keyword_bubbles`를 X=growth_rate, Y=mention_count,
     반지름=source_count로. "AI"·"인공지능" 제외(척도가 깨질 만큼 큼). 좌표가
     정확히 겹치는 항목(작은 데이터셋에서 흔함)은 같은 그룹끼리 수평으로 살짝
     퍼뜨려야 한다(안 그러면 겹쳐서 하나로 보여 데이터가 있다는 사실 자체가
     안 보인다).
   - 연관 키워드: `keyword_network`의 엣지를 표로(가중치 내림차순 상위 10개
     정도). **노드-엣지 그래프로 그리지 않는다** — "AI"가 거의 모든 기사에
     등장해 허브 하나로 쏠린 그래프는 표보다 오히려 읽기 어렵다.
   - 국내/해외 화제 격차: `keyword_gap`의 두 리스트를 기존 키워드 클라우드와
     같은 2컬럼 레이아웃으로.
   - 기업·인물 언급 랭킹: `entity_ranking`을 칩으로. 한글 제목엔 적용 안
     된다는 한계를 반드시 같이 적는다.
   - 국내-해외 보도 시차: `cross_region_lag` — 위 2-2절의 스팟체크를 거친
     뒤 표시(신뢰 못 할 예시면 "이번 기간엔 사례 없음 + 이유"로 대체).
   - 동시보도 강도 추이: `co_report_intensity`를 단색(중립색, 국내/해외
     구분 아님) 막대 그래프로 — 빈 날도 0으로 표시.
4. **"이번 주 파급력 상위 이슈" 소제목** — 위 트렌드/동향 지표와 달리
   상위 N건만 추린 목록임을 한 줄로 구분해준다.
5. **"신호 품질 안내" 배너**(경고색) — 관련도 필터가 아직 넓고 클러스터링에 한계가
   있다는 걸 **항상** 명시한다. 데이터 품질이 좋아 보이는 날에도 생략하지 않는다 —
   담당자가 실제로 필요로 하는 신뢰도 판단 정보이지 장식이 아니다.
6. 4색 범례(신호 breakdown용 — 트렌드 섹션의 국내/해외 범례와는 별개)
7. 이슈 목록 — 순위·번역 제목(원문 링크)·출처/날짜/동시보도 배지·번역 요약·
   4색 세그먼트 막대(hover 시 정확한 하위 점수 툴팁)·`relevance_rerank_score`가 있으면
   "관련성 참고: 상대적으로 낮음/보통/높음" 정도로만 표시(절대 수치를 그대로 보여주면
   0.005 같은 작은 값을 보고 오해할 수 있다 — [server.py](../../../src/tech_monitoring/mcp_server/server.py)의
   INSTRUCTIONS와 동일한 이유)
8. 푸터 — 어떤 명령으로 만들었는지(재현성), 조회 시각, 이슈 목록 기간·트렌드
   집계 기간·급상승/버블 비교 기간을 각각 명시(셋이 다른 기간일 수 있으므로
   헷갈리지 않게)

파비콘은 `📡`로 고정한다(이전 데모와 동일 — 사용자가 탭에서 같은 화면임을 알아보게).

### 5. Artifact로 배포

**같은 파일 경로**로 매번 재배포해 링크가 유지되게 한다. 처음 만드는 게 아니라면
`Artifact` 액션에 이전 `url`을 넘겨 같은 아티팩트를 갱신한다(대화가 바뀌었으면
`action: "list"`로 먼저 찾는다).

### 6. 답변에 항상 포함할 것

대시보드를 보여준 뒤 텍스트로 짧게 덧붙인다: **impact_score는 파급력이지
회사 관점 중요도가 아니며, 무엇이 실제로 중요한지는 담당자가 판단한다**
(이 프로젝트의 핵심 원칙 — PRD v2.0, [server.py](../../../src/tech_monitoring/mcp_server/server.py)의
INSTRUCTIONS와 동일). 생략하지 말 것 — 대시보드가 잘 나올수록 사용자가
점수를 그대로 "중요도"로 오해하기 쉽다.

## 데이터 품질에 대한 정직함

이 스킬로 만드는 화면은 현재 알려진 한계를 그대로 반영한다(감추지 않는다):
- 관련도 필터가 넓어 AX와 무관해 보이는 항목이 섞일 수 있다.
- 클러스터링이 다른 뉴스를 잘못 묶는 걸 막기 위해 임계값을 보수적으로 뒀다 —
  그래서 실제로는 같은 사건인데 따로 표시되는 항목도 있을 수 있다.
- `relevance_rerank_score`는 참고용일 뿐 필터링에 쓰이지 않는다.
- 트렌드 섹션의 국내/해외 키워드는 형태소 분석 없는 정규식 토큰화 결과라
  "AI가"·"AI는" 같은 조사 결합형이 "AI"와 별개로 집계될 수 있다 — 오탐을
  걸러내는 필터가 아니라 참고 지표이므로 그대로 노출한다.
- 국내/해외 구분은 `dashboard_data.py`의 등록된 소스 이름 목록 기반이다.
  새 소스를 추가했는데 이 목록을 갱신하지 않으면 그 소스는 기본값(해외)으로
  잘못 집계된다.
- `entity_ranking`(기업·인물 언급 랭킹)은 영문 대문자 표기 기반이라 한글
  제목(대부분의 국내 기사)에는 적용되지 않는다 — 사실상 "영문 제목 기준"
  랭킹이고, "The Information"·"The Download" 같은 매체명·섹션명이 실제
  화제와 섞여 나올 수 있다.
- `cross_region_lag`(국내-해외 보도 시차)은 클러스터링 정확도에 그대로
  의존한다 — 한글 제목은 클러스터링의 고유명사 겹침 오탐 방지 게이트가
  적용되지 않는다는 기존 한계 때문에, 표본이 적을 때 서로 다른 주제가
  우연히 묶인 예시가 나올 수 있다. 화면에 내보내기 전 반드시 스팟체크한다
  (2-2절 참고).
- 2026-08-11: `articles.cluster_id`가 파이프라인 실행(배치)마다 이름을
  1부터 새로 매기던 충돌 버그를 고쳤다(`fix/cluster-id-global-uniqueness`,
  `db/migrations/013_global_cluster_id_sequence.sql`). 이 수정 이전 데이터로
  만든 화면의 "N개 매체 동시보도" 수치·`cross_region_lag`·
  `co_report_intensity`는 무관한 배치의 클러스터가 같은 이름으로 뒤섞여
  부정확했을 수 있다.

자세한 배경은 [README.md](../../../README.md)의 "Phase 3 조사 결과" 절 참고.
