"""모니터링 MCP 서버 (프로젝트 ① · 설계서 v2.0 §6).

마스터 DB(수집·필터를 통과한 AX 시장 기사)를 사내 Claude에 노출한다.
도구 docstring이 곧 Claude가 읽는 도구 설명이므로 판단 기준을 명시한다.
"""

from fastmcp import FastMCP

from tech_monitoring.mcp_server import queries

INSTRUCTIONS = """
AX(AI 전환) 시장 모니터링 데이터에 접근하는 서버.

수집 소스는 큐레이션·애그리게이터·논문·글로벌 매체(Techmeme, Hacker News, arXiv,
TechCrunch 등)이며, 관련도(AX 시장 관련성) × 파급력(반향 크기) 2축 필터를 통과한
기사만 저장되어 있다.

impact_score는 '파급력'이지 '우리 회사에 중요한가'가 아니다.
주관적 중요도는 시스템이 산정하지 않으며 담당자가 판단한다.
답변할 때 impact_score를 중요도로 단정하지 말고 근거(신호·출처·시점)를 함께 제시할 것.
""".strip()

mcp = FastMCP(name="tech-monitoring", instructions=INSTRUCTIONS)


@mcp.tool
def search_news(
    query: str = "",
    since: str | None = None,
    until: str | None = None,
    min_impact: float = 0.0,
    limit: int = 20,
) -> dict:
    """AX 시장 기사를 하이브리드(키워드 BM25 + 의미 임베딩) 검색한다.

    Args:
        query: 검색어(자연어 문장 가능). 비우면 검색 대신 파급력 상위 기사를 반환한다.
        since: 시작 시점. '7d', '24h', '2w', '2026-08-01', ISO8601 중 하나. 생략 시 제한 없음.
        until: 종료 시점(같은 형식). 생략 시 현재까지.
        min_impact: 파급력 하한(0~1). 노이즈를 줄이려면 0.5 이상을 쓴다.
        limit: 최대 건수(최대 50).

    Returns:
        articles 목록. 각 항목은 제목·URL·출처·발행일·요약과 impact_score,
        그 근거인 impact_signals(source_trust·aggregator_signal·cluster_size·recency),
        매칭 방식(matched_by: keyword/semantic/hybrid)을 포함한다.
    """
    return queries.search_news(
        query=query, since=since, until=until, min_impact=min_impact, limit=limit
    )


@mcp.tool
def get_weekly_digest(
    period: str = "last_week", limit: int = 10, min_impact: float = 0.0
) -> dict:
    """지정 구간의 AX 시장 이슈를 파급력 상위 순으로 묶어 반환한다(주간 센싱용).

    같은 사건을 다룬 기사는 하나의 이슈(cluster)로 묶이며, 대표 기사(lead)와
    함께 보도한 매체(related)를 같이 준다. 여러 매체가 동시 보도했다는 사실
    자체가 파급력 신호다.

    Args:
        period: 'last_week'(기본, 전주 월~일) · 'this_week' · '7d' 같은 상대 기간 ·
            '2026-08-01..2026-08-07' 형식의 명시 구간.
        limit: 반환할 이슈 수(최대 50).
        min_impact: 파급력 하한(0~1).

    Returns:
        period(구간), total_articles, total_clusters, clusters(이슈 목록).
        주간 인사이트 보고서를 쓸 때는 clusters를 근거로 삼고,
        무엇이 회사에 중요한지는 단정하지 말고 담당자 판단에 맡길 것.
    """
    return queries.get_weekly_digest(period=period, limit=limit, min_impact=min_impact)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
