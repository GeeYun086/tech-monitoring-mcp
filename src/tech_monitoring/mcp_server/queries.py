"""MCP 도구가 돌려줄 값을 만드는 조회 계층.

server.py는 도구 이름과 인자만 선언하고 계산은 전부 여기서 한다 —
dashboard_queries/streamlit_app의 역할 분리와 같은 원칙이다. 그래서 이 모듈은
MCP SDK에 의존하지 않고, fake conn으로 그대로 테스트된다.

**새 SQL을 쓰지 않고 dashboard_queries를 재사용한다.** 화면과 MCP가 다른
질의를 쓰면 "대시보드에는 있는데 Claude는 못 보는" 차이가 조용히 생긴다.
시장별 기사 순서(분류기 점수순, 007)나 주차 필터 같은 규칙이 한 곳에만
있어야 두 경로가 어긋나지 않는다.

날짜·Decimal 같은 값은 여기서 문자열·float으로 바꿔 내보낸다 — MCP 응답은
JSON이라 그대로 두면 직렬화가 실패한다.
"""

from tech_monitoring import dashboard_queries as dq
from tech_monitoring import labeling
from tech_monitoring.db.weekly_run import week_bounds_for

# 한 번에 돌려줄 기사 수 상한. Claude가 읽을 분량이라 화면(전체 표시)과 달리
# 잘라야 한다 — 152건을 통째로 주면 대화 맥락을 다 잡아먹는다.
DEFAULT_ARTICLE_LIMIT = 20
MAX_ARTICLE_LIMIT = 100


def _week_range(week_start) -> str:
    """"8/10~8/16" 형태. 화면과 같은 표기를 쓴다 — 사람이 두 곳을 오가며 볼 때
    표기가 다르면 같은 주인지 헷갈린다(app/streamlit_app._week_label과 동일)."""
    if week_start is None:
        return "날짜 미상"
    _monday, sunday = week_bounds_for(week_start)
    return f"{week_start.month}/{week_start.day}~{sunday.month}/{sunday.day}"


def _market_by_name(conn, market: str) -> dict | None:
    """시장 이름으로 고정 키워드를 찾는다. Claude가 "교육"처럼 사람이 쓰는
    이름으로 부를 것이므로 id가 아니라 이름을 받는다(앞뒤 공백은 무시)."""
    wanted = market.strip()
    for keyword in dq.get_fixed_keywords(conn):
        if keyword["keyword"] == wanted:
            return keyword
    return None


def _unknown_market(conn, market: str) -> dict:
    """모르는 시장을 물었을 때 빈 결과 대신 **무엇이 있는지 알려준다** —
    "결과 없음"만 주면 이름을 틀렸는지 데이터가 없는지 구분이 안 된다."""
    names = [k["keyword"] for k in dq.get_fixed_keywords(conn)]
    return {"error": f"'{market}'는 등록된 시장이 아닙니다.", "available_markets": names}


def get_status(conn) -> dict:
    """이번 주 수집 현황 — 다른 도구를 부르기 전에 "지금 무슨 데이터가 있나"를
    먼저 확인할 수 있게 한다."""
    run = dq.get_latest_run(conn)
    if run is None:
        return {"has_data": False, "message": "아직 수집된 데이터가 없습니다."}

    span = dq.get_pool_span(conn, run["id"])
    weeks = dq.get_pool_weeks(conn, run["id"])
    labels = labeling.count_labels(conn)

    return {
        "has_data": True,
        "period": f"{run['period_start']} ~ {run['period_end']}",
        "status": run["status"],
        # 실패를 숨기지 않는다 — 결과가 적은 게 수집 실패 때문인지 원래 그런지
        # 구분되어야 한다(pipeline_report.py와 같은 이유).
        "error_message": run.get("error_message"),
        "article_count": span["total"],
        "article_dates": f"{span['oldest']} ~ {span['newest']}" if span["oldest"] else None,
        "weeks": [{"range": _week_range(w["week_start"]), "count": w["total"]} for w in weeks],
        "markets": [k["keyword"] for k in dq.get_fixed_keywords(conn)],
        "labels": labels,
    }


def get_markets(conn) -> list[dict]:
    """모니터링 중인 시장 목록과 각 시장의 라벨 진행 상황."""
    return [
        {"market": k["keyword"], "labels": labeling.count_labels(conn, k["id"])}
        for k in dq.get_fixed_keywords(conn)
    ]


def get_articles(conn, market: str, limit: int = DEFAULT_ARTICLE_LIMIT) -> dict:
    """한 시장의 주간 기사. **분류기 점수 내림차순**이고 점수가 없으면 최신순이다
    (dashboard_queries.get_pool_articles가 그 규칙을 갖고 있어 그대로 쓴다).

    점수로 자르지 않고 정렬만 한다는 원칙(007)은 여기서도 같다 — 다만 응답
    길이 때문에 상위 limit건만 내보내므로, 전체 건수를 함께 알려 잘렸다는 걸
    분명히 한다.
    """
    run = dq.get_latest_run(conn)
    if run is None:
        return {"error": "아직 수집된 데이터가 없습니다."}

    keyword = _market_by_name(conn, market)
    if keyword is None:
        return _unknown_market(conn, market)

    limit = max(1, min(int(limit), MAX_ARTICLE_LIMIT))
    rows = dq.get_pool_articles(conn, run["id"], keyword["id"])
    scored = any(r.get("score") is not None for r in rows)

    return {
        "market": keyword["keyword"],
        "period": f"{run['period_start']} ~ {run['period_end']}",
        "total": len(rows),
        "returned": min(limit, len(rows)),
        # 순서의 근거를 밝힌다 — 라벨이 부족해 모델이 없으면 최신순인데, 그걸
        # 모르면 "추천 순서"로 오해한다.
        "ordering": "분류기 점수 높은 순" if scored else "최신순(아직 학습된 모델이 없음)",
        "articles": [
            {
                "title": r["title"],
                "url": r["url"],
                "source": r.get("source_domain"),
                "published": str(r["published_at"].date()) if r.get("published_at") else None,
                "summary": r.get("snippet"),
                "score": round(float(r["score"]), 3) if r.get("score") is not None else None,
            }
            for r in rows[:limit]
        ],
    }


def get_keywords(conn, market: str, limit: int = 15) -> dict:
    """한 시장의 이번 주 주요 키워드(언급 문서 수 기준).

    화면에서 "보조 지표"로 접어둔 것과 같은 데이터다 — 대문자 시작 휴리스틱
    기반이라 완벽하지 않다는 한계를 응답에도 적어 보낸다(그대로 사실처럼
    인용되지 않게).
    """
    run = dq.get_latest_run(conn)
    if run is None:
        return {"error": "아직 수집된 데이터가 없습니다."}

    keyword = _market_by_name(conn, market)
    if keyword is None:
        return _unknown_market(conn, market)

    rows = dq.get_market_keywords(conn, run["id"], keyword["id"], limit=limit)
    return {
        "market": keyword["keyword"],
        "period": f"{run['period_start']} ~ {run['period_end']}",
        "note": "개체명 인식이 아니라 근사 규칙(대문자 시작 등)으로 뽑은 보조 지표입니다.",
        "keywords": [
            {"phrase": r["canonical_phrase"], "doc_count": r["doc_count"],
             "variants": list(r.get("variant_phrases") or [])}
            for r in rows
        ],
    }
