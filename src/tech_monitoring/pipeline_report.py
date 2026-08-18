"""파이프라인 단계 결과에서 "예외는 안 났지만 실제로는 실패한" 항목을 찾아낸다.

2026-08-18에 발견한 문제: 각 단계(collect / judge_relevance)는 개별 항목이
실패해도 예외를 올리지 않고 결과 dict의 "error" 필드에 사유만 담아
정상 리턴하는 관례를 쓴다(collectors/rss_collector.py, collectors/
search_engine.py, analysis/relevance_filter.py 전부 동일). 그런데
pipeline_v2/v3의 run_pipeline은 **예외만** 실패로 취급했기 때문에,

  - Gemini 429로 모든 고정 키워드의 관련도 판단이 실패해도(그 주 관련
    기사가 통째로 0건이 돼도),
  - TAVILY_API_KEY가 비어 수집이 한 건도 안 돼도,

로그엔 "judge_relevance ok" / "collect ok"가 찍히고 weekly_runs.status는
'completed'로 남았다. 실패를 알 방법이 대시보드에 결과가 비어 보이는 것
말고는 없었다 — "왜 결과가 흐지부지되지?"의 직접적인 원인.

그래서 단계가 정상 리턴하더라도 그 안의 항목별 error를 훑어서 부분 실패를
드러낸다. 판단은 여기서만 하고, 실패를 어떻게 처리할지(failed 목록에 넣고
weekly_runs를 failed로 마감)는 기존대로 각 파이프라인의 run_pipeline이 한다.

**의도적으로 엄격하게 잡는다**: 항목 하나라도 error면 그 단계를 실패로 본다
(RSS 피드 4개 중 1개만 죽어도 실패). 조용히 넘어가서 몇 주를 놓치는 것보다,
과하게 알리고 로그에서 사유를 확인하는 쪽이 낫다는 판단(2026-08-18 담당자
확인). 완화하려면 "전부 실패한 경우만"으로 조건을 바꾸면 된다.
"""


def stage_errors(result: object) -> list[str]:
    """단계 결과 {"results": [{...,"error": ...}, ...]}에서 실패 항목을
    "이름: 사유" 문자열 목록으로 뽑는다. 이 모양이 아니거나(예:
    merge_keywords처럼 error 관례가 없는 단계) 실패가 없으면 빈 목록."""
    if not isinstance(result, dict):
        return []

    items = result.get("results")
    if not isinstance(items, list):
        return []

    errors = []
    for item in items:
        if not isinstance(item, dict):
            continue
        message = item.get("error")
        if not message:
            continue
        # 수집기는 "source", 관련도 판단·검색엔진은 "fixed_keyword"로
        # 항목을 식별한다 — 둘 중 있는 쪽을 라벨로 쓴다.
        label = item.get("source") or item.get("fixed_keyword") or "?"
        errors.append(f"{label}: {message}")
    return errors
