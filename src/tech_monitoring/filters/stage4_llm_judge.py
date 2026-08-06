"""Stage 4 LLM-as-judge 틀.

설계 원칙(기술설계서 §5, §6): LLM 판단은 별도 API 과금이 아니라 팀의 **구독형 Claude**를
통합 MCP(Phase 2)로 호출해 수행한다. 따라서 이 모듈은 실제 LLM 호출을 하지 않고,
① 상위 후보 선정 ② 프롬프트 조립까지만 담당하는 스켈레톤이다.
실제 채점은 Phase 2에서 MCP 도구(예: judge_importance)로 노출되어 Claude가 수행한다.

criteria(회사 관점 중요도 기준)는 담당자 확인 전까지 비워둔다 [확인 필요].
"""

from tech_monitoring.db.connection import get_connection
from tech_monitoring.utils.text import strip_html

PROMPT_TEMPLATE = """다음 뉴스가 회사 관점에서 얼마나 중요하고 파급력이 큰지 판단해줘.

# 판단 기준
{criteria}

# 뉴스
제목: {title}
본문: {content}

# 출력
- score: 0~1 사이 중요도 점수
- reason: 판단 근거 한두 문장
"""

DEFAULT_CRITERIA = "[확인 필요] 담당자의 회사 관점 중요도·파급력 기준이 아직 수신되지 않음."


def select_candidates(top_n: int = 20) -> list[dict]:
    """리랭커 재정렬 결과(rerank_score)가 있으면 우선, 없으면 importance_score 기준 상위 N건."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, summary, content, importance_score, importance_signals
            FROM articles
            WHERE status = 'new'
            ORDER BY
                (importance_signals->>'rerank_score')::float DESC NULLS LAST,
                importance_score DESC NULLS LAST
            LIMIT %s
            """,
            (top_n,),
        )
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def build_prompt(article: dict, criteria: str = DEFAULT_CRITERIA) -> str:
    return PROMPT_TEMPLATE.format(
        criteria=criteria,
        title=article["title"],
        content=strip_html((article.get("content") or article.get("summary") or ""))[:3000],
    )


def judge(article: dict, criteria: str = DEFAULT_CRITERIA) -> dict:
    """Phase 2에서 MCP 도구를 통해 Claude가 대신 수행. 지금은 프롬프트만 반환."""
    raise NotImplementedError(
        "LLM-as-judge는 Phase 2 통합 MCP를 통해 Claude가 호출한다. "
        "여기서는 build_prompt()로 프롬프트만 확인할 수 있다."
    )


if __name__ == "__main__":
    candidates = select_candidates(top_n=3)
    for c in candidates:
        print(build_prompt(c))
        print("---")
