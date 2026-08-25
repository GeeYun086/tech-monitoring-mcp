"""AX 시장 모니터링 MCP 서버 — Claude가 이번 주 수집 결과를 직접 조회한다.

**읽기 전용이다.** 수집·라벨링·학습은 파이프라인과 대시보드가 하고, 여기서는
이미 DB에 있는 결과를 꺼내 보여주기만 한다. 그래서 이 서버에는 머신러닝
라이브러리가 전혀 필요 없다 — 분류기 판단은 파이프라인이 미리 끝내
article_keyword_relevance.score에 저장해두기 때문이다(psycopg만 있으면 된다).

도구 구성은 "먼저 무엇이 있는지 보고, 그다음 좁혀 들어간다"는 순서다:
    get_status           → 이번 주 기간·기사 수·주차·시장 목록·라벨 현황
    get_markets          → 시장별 라벨 진행 상황
    get_articles         → 한 시장의 기사(분류기 점수순, 좋아요 수 포함)
    get_popular_articles → 한 시장에서 👍를 가장 많이 받은 기사만(주간 인사이트용)
    get_keywords         → 한 시장의 주요 키워드

계산은 전부 queries.py가 한다 — 여기는 도구 선언과 연결만 담당한다
(dashboard_queries/streamlit_app의 역할 분리와 같은 원칙).

연결 설정(Claude Desktop/Code의 mcp 설정):
    {
      "mcpServers": {
        "tech-monitoring": {
          "command": "<프로젝트>/.venv/Scripts/python.exe",
          "args": ["-m", "tech_monitoring.mcp_server"],
          "env": {"DATABASE_URL": "postgresql://..."}
        }
      }
    }

    ./.venv/Scripts/python.exe -m tech_monitoring.mcp_server
"""

from mcp.server.fastmcp import FastMCP

from tech_monitoring.db.connection import get_connection
from tech_monitoring.mcp_server import queries

mcp = FastMCP(
    "tech-monitoring",
    instructions=(
        "goormEDU 전략기획팀의 AX 시장 모니터링 데이터입니다. "
        "매주 큐레이션된 기술 매체에서 기사를 수집하고, 사람이 매긴 라벨로 학습한 "
        "분류기가 시장별로 점수를 매깁니다. "
        "무엇이 있는지 모를 때는 get_status를 먼저 부르세요 — 이번 주 기간, 기사 수, "
        "시장 목록이 한 번에 나옵니다. "
        "기사 순서가 '최신순'으로 표시되면 아직 학습된 모델이 없다는 뜻이므로 "
        "그 순서를 추천 순위로 해석하지 마세요. "
        "주간 인사이트·보고서를 쓸 때는 get_popular_articles로 담당자들이 직접 "
        "👍를 누른 기사부터 확인하세요 — 분류기 점수보다 신뢰도가 높습니다. "
        "좋아요는 익명 집계라 누가 눌렀는지는 알 수 없으니 사람을 특정해 인용하지 마세요."
    ),
)


def _with_connection(fn, *args, **kwargs):
    """도구마다 연결을 열고 반드시 닫는다.

    모듈 수준에서 연결을 하나 열어두지 않는 이유: Supabase 무료 티어는 7일
    미사용 시 자동 정지되고 유휴 연결도 끊긴다. 오래 떠 있는 MCP 서버가 죽은
    연결을 붙들고 있으면 그 뒤 모든 호출이 실패한다(db/connection.py의
    connect_timeout 주석과 같은 맥락).
    """
    conn = get_connection()
    try:
        return fn(conn, *args, **kwargs)
    finally:
        conn.close()


@mcp.tool()
def get_status() -> dict:
    """이번 주 수집 현황을 확인한다.

    기준 기간, 수집된 기사 수와 발행일 범위, 주차별 건수, 모니터링 중인 시장
    목록, 라벨 진행 상황을 한 번에 돌려준다. 다른 도구를 부르기 전에 먼저
    확인하기 좋다. 파이프라인이 실패했다면 그 사유도 함께 나온다.
    """
    return _with_connection(queries.get_status)


@mcp.tool()
def get_markets() -> list[dict]:
    """모니터링 중인 시장(고정 키워드) 목록과 시장별 라벨 진행 상황을 돌려준다.

    get_articles·get_keywords에 넘길 시장 이름을 여기서 확인한다.
    """
    return _with_connection(queries.get_markets)


@mcp.tool()
def get_articles(market: str, limit: int = queries.DEFAULT_ARTICLE_LIMIT) -> dict:
    """한 시장의 이번 주 기사 목록을 돌려준다.

    학습된 분류기가 있으면 "이 시장에 도움될 확률"이 높은 순으로, 없으면
    최신순으로 정렬된다(응답의 ordering 필드로 어느 쪽인지 알 수 있다).
    점수로 잘라내지 않고 정렬만 하므로 낮은 점수의 기사도 목록에 남는다.

    Args:
        market: 시장 이름(예: "교육"). 정확한 이름은 get_markets로 확인한다.
        limit: 돌려줄 기사 수(기본 20, 최대 100). 전체 건수는 total에 담긴다.
    """
    return _with_connection(queries.get_articles, market, limit)


@mcp.tool()
def get_popular_articles(market: str, limit: int = 10) -> dict:
    """한 시장에서 담당자들이 👍(도움됨)를 가장 많이 누른 이번 주 기사를 돌려준다.

    분류기 점수(추정치)와 달리 사람이 직접 누른 값이라 신뢰도가 높다 —
    주간 인사이트나 보고서를 쓸 때는 이 결과부터 참고한다. 좋아요를 하나도
    못 받은 기사는 목록에서 빠진다(0건까지 섞으면 순위가 있는 것처럼
    오해한다).

    좋아요는 익명 집계다 — "몇 명이 도움된다고 표시했는지"만 알 수 있고
    누가 눌렀는지는 알 수 없다(응답의 note 참고, 보고서에 사람을 특정해
    인용하지 말 것).

    Args:
        market: 시장 이름(예: "기사"). 정확한 이름은 get_markets로 확인한다.
        limit: 돌려줄 기사 수(기본 10, 최대 100).
    """
    return _with_connection(queries.get_popular_articles, market, limit)


@mcp.tool()
def get_keywords(market: str, limit: int = 15) -> dict:
    """한 시장의 이번 주 주요 키워드를 언급 문서 수 기준으로 돌려준다.

    개체명 인식이 아니라 근사 규칙으로 뽑은 **보조 지표**다 — 사실처럼 단정해
    인용하지 말고, 기사 내용을 함께 확인하는 게 좋다.

    Args:
        market: 시장 이름(예: "교육").
        limit: 돌려줄 키워드 수(기본 15).
    """
    return _with_connection(queries.get_keywords, market, limit)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
