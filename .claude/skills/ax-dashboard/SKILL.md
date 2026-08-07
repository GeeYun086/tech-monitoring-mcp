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

**타이포그래피**: 기사 제목(번역문)만 세리프(`Georgia, "Iowan Old Style", "Palatino Linotype", serif`),
UI 텍스트는 시스템 산세리프(`-apple-system, "Segoe UI", sans-serif`), **모든 숫자·날짜·점수는
모노스페이스**(`ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace`) +
`font-variant-numeric: tabular-nums`.

**레이아웃** (위→아래):
1. 마스트헤드 — 제목, 기간, 스탯 리드아웃(스캔 기사 수·클러스터 수·표시 건수, 전부 mono)
2. **"신호 품질 안내" 배너**(경고색) — 관련도 필터가 아직 넓고 클러스터링에 한계가
   있다는 걸 **항상** 명시한다. 데이터 품질이 좋아 보이는 날에도 생략하지 않는다 —
   담당자가 실제로 필요로 하는 신뢰도 판단 정보이지 장식이 아니다.
3. 4색 범례
4. 발견된 주요 키워드(칩, count 비례 크기/굵기)
5. 이슈 목록 — 순위·번역 제목(원문 링크)·출처/날짜/동시보도 배지·번역 요약·
   4색 세그먼트 막대(hover 시 정확한 하위 점수 툴팁)·`relevance_rerank_score`가 있으면
   "관련성 참고: 상대적으로 낮음/보통/높음" 정도로만 표시(절대 수치를 그대로 보여주면
   0.005 같은 작은 값을 보고 오해할 수 있다 — [server.py](../../../src/tech_monitoring/mcp_server/server.py)의
   INSTRUCTIONS와 동일한 이유)
6. 푸터 — 어떤 명령으로 만들었는지(재현성), 조회 시각

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

자세한 배경은 [README.md](../../../README.md)의 "Phase 3 조사 결과" 절 참고.
