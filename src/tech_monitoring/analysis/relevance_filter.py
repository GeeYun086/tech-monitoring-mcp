"""v3: "이 기사가 이 고정 키워드(모니터링 대상 시장)와 관련 있는가" 판단 —
v3 파이프라인에서만 필요한 단계다(v2는 화이트리스트 자체가 관련도를
보장한다는 전제라 이 단계가 아예 없다. README "v2 vs v3 비교 실험" 참고).

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

**판단 주체(2026-08-18 변경)**: 사람이 매긴 라벨로 학습한 로컬 분류기
(relevance_model)가 있으면 그걸 쓰고, 없으면 기존 Gemini 경로로 폴백한다.
분류기 쪽은 API 호출이 0이라 429·크레딧 상태와 무관하게 동작한다 — 이
단계가 Gemini에 묶여 있던 게 "그 주 관련 기사 0건"의 원인이었다.
어느 쪽으로 판단했는지는 결과의 "method"에 남는다(모델이 있는 줄 알았는데
조용히 Gemini를 쓰고 있는 상황을 알아챌 수 있게).

    ./.venv/Scripts/python.exe -m tech_monitoring.analysis.relevance_filter
"""

import json

from tech_monitoring.db.weekly_run import get_active_fixed_keywords
from tech_monitoring.llm_client import call_gemini_json

# 분류기가 "도움됨"으로 볼 확률 기준선. 0.5는 학습 때와 같은 기준이다
# (relevance_model.evaluate도 0.5로 채점하므로 화면에서 본 Precision/Recall이
# 실제 파이프라인 동작과 일치한다).
RELEVANCE_THRESHOLD = 0.5

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
    conn, run_id: int, fixed_keyword_id: int, articles: list[dict],
    relevant_indices: set[int], scores: list[float] | None = None,
) -> int:
    """판단 결과를 저장한다 — 관련 있는 것만이 아니라 **모든 기사**에 대해.

    순위를 매기려면 낮은 점수도 남아 있어야 한다(007 헤더). 재판단 시에는
    점수가 갱신돼야 하므로 DO NOTHING이 아니라 DO UPDATE다 — 라벨을 더 모아
    모델을 다시 학습했으면 같은 기사의 점수가 달라지는 게 정상이다.
    """
    saved = 0
    with conn.cursor() as cur:
        for i, article in enumerate(articles):
            cur.execute(
                """
                INSERT INTO article_keyword_relevance
                    (run_id, article_id, fixed_keyword_id, is_relevant, score)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (article_id, fixed_keyword_id) DO UPDATE SET
                    is_relevant = EXCLUDED.is_relevant,
                    score = EXCLUDED.score,
                    judged_at = now()
                RETURNING id
                """,
                (
                    run_id, article["id"], fixed_keyword_id,
                    i in relevant_indices,
                    None if scores is None else float(scores[i]),
                ),
            )
            if cur.fetchone() is not None:
                saved += 1
    return saved


def score_with_classifier(bundle: dict, fixed_keyword: dict, articles: list[dict]) -> list[float]:
    """학습된 로컬 분류기로 기사마다 "이 시장에 도움될 확률"을 매긴다.

    relevance_model.build_text가 읽을 수 있는 모양으로 맞춰서 넘긴다 —
    학습 때와 **똑같은 입력 형식**이어야 한다(고정 키워드를 앞에 붙인
    "키워드 [SEP] 제목 요약"). 여기서 형식이 어긋나면 정확도가 조용히
    무너지므로 build_text를 직접 재사용한다.

    확률을 그대로 돌려주는 게 핵심이다(예전에는 여기서 0.5로 잘라 집합만
    돌려줘 순위 정보를 버렸다 — 007 헤더 참고).
    """
    from tech_monitoring.relevance_model import predict_proba

    rows = [
        {"fixed_keyword": fixed_keyword["keyword"], "title": a["title"], "snippet": a.get("snippet")}
        for a in articles
    ]
    return [float(p) for p in predict_proba(bundle, rows)]


def judge_keyword(conn, run_id: int, fixed_keyword: dict, articles: list[dict], bundle=None) -> dict:
    """bundle(학습된 분류기)이 있으면 그걸로, 없으면 Gemini로 판단한다.

    호출부(judge_all)가 모델을 한 번만 불러와 넘겨준다 — 키워드마다 다시
    불러오면 임베딩 방식일 때 모델 로드가 키워드 수만큼 반복된다.
    """
    if not articles:
        return {"fixed_keyword": fixed_keyword["keyword"], "judged": 0, "relevant": 0,
                "method": None, "error": None}

    method = f"classifier:{bundle['method']}" if bundle else "gemini"
    scores = None
    try:
        if bundle is not None:
            scores = score_with_classifier(bundle, fixed_keyword, articles)
            relevant_indices = {i for i, p in enumerate(scores) if p >= RELEVANCE_THRESHOLD}
        else:
            relevant_indices = parse_relevant_indices(
                call_gemini(build_prompt(fixed_keyword["keyword"], articles)), len(articles),
            )
    except Exception as exc:  # google-genai가 다양한 예외 타입(ClientError 등)을 던짐
        return {
            "fixed_keyword": fixed_keyword["keyword"], "judged": 0, "relevant": 0,
            "method": method, "error": f"{type(exc).__name__}: {exc}",
        }

    _save_relevance(conn, run_id, fixed_keyword["id"], articles, relevant_indices, scores)
    return {
        "fixed_keyword": fixed_keyword["keyword"],
        "judged": len(articles),
        "relevant": len(relevant_indices),
        "method": method,
        "error": None,
    }


def judge_all(conn, run_id: int, *, allow_llm_fallback: bool = False) -> list[dict]:
    """이번 run에 수집된 기사 전체를 딱 한 번 가져와서, 활성 고정 키워드
    각각에 대해 배치 판단한다(기사 목록 자체는 고정 키워드마다 재사용 —
    수집은 한 번, 판단만 키워드 수만큼).

    **모델이 없으면 판단을 건너뛴다**(2026-08-19 결정). 예전에는 Gemini로
    폴백했는데, 라벨이 아직 없는 첫 주에 굳이 LLM을 부를 이유가 없다 —
    점수가 없으면 화면이 최신순 전체를 보여주고, 그 주 결과물은 사람이
    라벨링한 것 자체가 된다. Gemini 경로는 지우지 않고 allow_llm_fallback로
    남겨둔다(비교 실험용).

    어느 쪽으로 판단했는지는 결과의 "method"에 남는다 — 모델이 있는 줄
    알았는데 조용히 다른 경로를 타는 상황을 알아챌 수 있게.
    """
    from tech_monitoring.relevance_model import load_model

    bundle = load_model()
    keywords = get_active_fixed_keywords(conn)

    if bundle is None and not allow_llm_fallback:
        return [
            {"fixed_keyword": kw["keyword"], "judged": 0, "relevant": 0,
             "method": "skipped:모델 없음", "error": None}
            for kw in keywords
        ]

    articles = fetch_collected_articles(conn, run_id)
    return [judge_keyword(conn, run_id, kw, articles, bundle) for kw in keywords]


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
