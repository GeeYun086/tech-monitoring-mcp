"""v3: LLM 기반 "이 기사가 이 고정 키워드(모니터링 대상 시장)와 관련
있는가" 판단 — v3 파이프라인에서만 필요한 단계다(v2는 화이트리스트
자체가 관련도를 보장한다는 전제라 이 단계가 아예 없다. README "v2 vs v3
비교 실험" 참고).

analysis/keyword_merge.py와 같은 원칙: LLM은 판단만 하고 그 결과를 그대로
믿지 않는다 — 목록 범위 밖 번호(환각)는 버리고, 파싱 자체가 실패하면 전부
"관련 없음"으로 안전하게 폴백한다. v2가 "노이즈를 원천 차단"하기 위해
화이트리스트 밖은 아예 안 본 것과 같은 방향 — 애매하면 포함시키지 않는다
(과다 포함보다 과소 포함이 안전).

배치 단위: 고정 키워드 하나당 이번 주 수집된 기사 전체(collected_articles)
를 한 프롬프트에 넣어 한 번에 판단시킨다(기사 하나하나 호출하지 않음) —
keyword_merge.py의 배치 원칙과 같은 이유(호출 수 자체를 줄여 rate limit
리스크를 피한다 — 2026-08-13 Gemini 429를 겪은 뒤 확립한 원칙).

fetch_relevant_articles()는 analysis/keyword_extraction.py의
fetch_search_results()와 같은 모양(title/snippet/source_domain)의 행을
돌려주도록 맞췄다 — analysis/keyword_merge.py의 run_for_all_keywords에
fetch_rows로 그대로 꽂아 넣으면 TF-IDF 후보추출·Gemini 동의어 병합 로직은
무변경으로 재사용된다.

    ./.venv/Scripts/python.exe -m tech_monitoring.analysis.relevance_filter
"""

import json

from tech_monitoring.db.weekly_run import get_active_fixed_keywords
from tech_monitoring.llm_client import call_gemini_json

_PROMPT_TEMPLATE = """다음은 이번 주 여러 사이트에서 수집한 기사 목록이다(번호, 제목, 요약/카테고리).

"{fixed_keyword}" 시장 모니터링과 실질적으로 관련 있는 기사의 번호만 골라라.

규칙:
- 반드시 아래 목록에 있는 번호만 쓴다. 목록에 없는 번호를 만들어내지 않는다.
- 애매하면 포함시키지 않는다(과다 포함보다 과소 포함이 안전하다).

기사 목록:
{article_list}

아래 JSON 형식으로만 답하라(설명 문장 없이):
{{"relevant_indices": [0, 3, 7]}}
"""


def call_gemini(prompt: str) -> str:
    """llm_client.call_gemini_json의 얇은 wrapper — 테스트에서 이 이름
    (relevance_filter.call_gemini)을 monkeypatch로 대체한다(keyword_merge.py
    와 동일한 패턴)."""
    return call_gemini_json(prompt)


def build_prompt(fixed_keyword: str, articles: list[dict]) -> str:
    lines = [f"{i}. {a['title']} | {a.get('snippet') or ''}" for i, a in enumerate(articles)]
    return _PROMPT_TEMPLATE.format(fixed_keyword=fixed_keyword, article_list="\n".join(lines))


def parse_relevant_indices(raw_json: str, num_articles: int) -> set[int]:
    """환각 방지: 목록 범위 밖 번호·타입이 이상한 값은 버린다. 파싱 자체가
    실패하면 빈 집합(전부 "관련 없음"으로 안전하게 폴백)."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return set()
    indices = data.get("relevant_indices") if isinstance(data, dict) else None
    if not isinstance(indices, list):
        return set()
    return {i for i in indices if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < num_articles}


def fetch_collected_articles(conn, run_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, snippet, source_domain FROM collected_articles WHERE run_id = %s ORDER BY id",
            (run_id,),
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def fetch_relevant_articles(conn, run_id: int, fixed_keyword_id: int) -> list[dict]:
    """analysis/keyword_extraction.py의 fetch_search_results와 같은 모양
    (title/snippet/source_domain)으로 맞춘 행을 돌려준다 — keyword_merge.py의
    run_for_all_keywords(fetch_rows=...)에 그대로 꽂아 쓴다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ca.title, ca.snippet, ca.source_domain
            FROM collected_articles ca
            JOIN article_keyword_relevance r ON r.article_id = ca.id
            WHERE ca.run_id = %s AND r.fixed_keyword_id = %s AND r.is_relevant
            """,
            (run_id, fixed_keyword_id),
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _save_relevance(
    conn, run_id: int, fixed_keyword_id: int, articles: list[dict], relevant_indices: set[int],
) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for i, article in enumerate(articles):
            cur.execute(
                """
                INSERT INTO article_keyword_relevance (run_id, article_id, fixed_keyword_id, is_relevant)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (article_id, fixed_keyword_id) DO NOTHING
                RETURNING id
                """,
                (run_id, article["id"], fixed_keyword_id, i in relevant_indices),
            )
            if cur.fetchone() is not None:
                inserted += 1
    return inserted


def judge_keyword(conn, run_id: int, fixed_keyword: dict, articles: list[dict]) -> dict:
    if not articles:
        return {"fixed_keyword": fixed_keyword["keyword"], "judged": 0, "relevant": 0, "error": None}

    prompt = build_prompt(fixed_keyword["keyword"], articles)
    try:
        raw_response = call_gemini(prompt)
    except Exception as exc:  # google-genai가 다양한 예외 타입(ClientError 등)을 던짐
        return {
            "fixed_keyword": fixed_keyword["keyword"], "judged": 0, "relevant": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    relevant_indices = parse_relevant_indices(raw_response, len(articles))
    _save_relevance(conn, run_id, fixed_keyword["id"], articles, relevant_indices)
    return {
        "fixed_keyword": fixed_keyword["keyword"],
        "judged": len(articles),
        "relevant": len(relevant_indices),
        "error": None,
    }


def judge_all(conn, run_id: int) -> list[dict]:
    """이번 run에 수집된 기사 전체를 딱 한 번 가져와서, 활성 고정 키워드
    각각에 대해 배치 판단한다(기사 목록 자체는 고정 키워드마다 재사용 —
    수집은 한 번, 판단만 키워드 수만큼)."""
    articles = fetch_collected_articles(conn, run_id)
    return [judge_keyword(conn, run_id, kw, articles) for kw in get_active_fixed_keywords(conn)]


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from tech_monitoring.db.connection import get_connection

    _conn = get_connection()
    try:
        with _conn.cursor() as _cur:
            _cur.execute("SELECT id FROM weekly_runs ORDER BY id DESC LIMIT 1")
            _row = _cur.fetchone()
        if _row is None:
            print("weekly_runs가 비어 있음 — 먼저 수집기(rss_collector 등)를 실행할 것")
        else:
            for _result in judge_all(_conn, _row[0]):
                print(_result)
    finally:
        _conn.close()
