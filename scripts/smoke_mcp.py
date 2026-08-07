"""모니터링 MCP 스모크 검증.

Claude에 연결하기 전에 도구 목록과 질문형 응답을 로컬에서 확인한다(개발계획서 Phase 2).
fastmcp in-memory 전송이라 별도 프로세스 없이 실제 도구 호출 경로를 그대로 탄다.

    ./.venv/Scripts/python.exe scripts/smoke_mcp.py
"""

import asyncio

from fastmcp import Client

from tech_monitoring.mcp_server.server import mcp

# 담당자가 실제로 물어볼 법한 질문 → 도구 호출로 옮긴 것 (PRD v2.0 §6 시나리오 B)
CASES = [
    ("지난 7일 파급력 큰 AX 이슈는?", "get_weekly_digest", {"period": "7d", "limit": 3}),
    ("전주 이슈 다이제스트(스케줄 센싱 형태)", "get_weekly_digest", {"period": "last_week", "limit": 3}),
    ("AI 에이전트 기업 도입 관련 기사", "search_news", {"query": "AI 에이전트 기업 도입", "limit": 3}),
    ("질의어 없이 파급력 상위만", "search_news", {"query": "", "min_impact": 0.5, "limit": 3}),
]


def _preview(name: str, data: dict) -> None:
    if name == "get_weekly_digest":
        period = data["period"]
        print(f"    구간 {period['start']} ~ {period['end']} "
              f"| 기사 {data['total_articles']}건 / 이슈 {data['total_clusters']}개")
        items = [(c["lead"], c["size"]) for c in data["clusters"]]
    else:
        print(f"    검색 결과 {data['count']}건 (since={data['since']})")
        items = [(a, None) for a in data["articles"]]

    if not items:
        print("    (결과 없음 — 수집·필터 파이프라인을 먼저 실행했는지 확인)")
        return
    for article, size in items:
        tag = f" +{size - 1}건 동시보도" if size and size > 1 else ""
        print(f"    - [{article['impact_score']}] {article['title'][:60]} "
              f"({article['source']}){tag}")


async def main() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print(f"도구 {len(tools)}개: {', '.join(t.name for t in tools)}\n")

        for question, tool, args in CASES:
            print(f"[{question}] → {tool}({args})")
            result = await client.call_tool(tool, args)
            _preview(tool, result.data)
            print()


if __name__ == "__main__":
    asyncio.run(main())
