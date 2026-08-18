"""사람이 직접 매기는 관련도 라벨의 저장/조회 계층(db/migrations/
004_article_labels.sql). 관련도 판단을 Gemini(analysis/relevance_filter.py)
에서 로컬 분류기로 옮기기 위한 학습 데이터를 쌓는다.

dashboard_queries.py와 같은 원칙: **판단·필터링은 여기서 끝내고 UI는 보여주기만
한다.** Streamlit에 의존하지 않으므로 fake conn으로 그대로 테스트 가능하다.

라벨링 단위는 "기사 하나"가 아니라 **"기사 × 고정 키워드" 쌍**이다 — 같은
기사가 "교육"엔 알짜여도 "비즈니스 실적"엔 무관할 수 있다(실측: 이번 주
262건 중 56개 URL이 여러 키워드에 동시에 걸려 있다). search_results는 애초에
고정 키워드별로 검색한 결과라 행 하나가 곧 이 쌍이고, collected_articles(v3)는
키워드 무관 수집이라 활성 키워드마다 한 번씩 후보로 올린다.

**"이미 라벨함" 판정은 반드시 정규화된 URL로 한다 — 원본 행 id로 하면 안 된다.**
매주 도는 TRUNCATE weekly_runs RESTART IDENTITY CASCADE가 id 시퀀스를 1부터
다시 시작시켜서, "이번 주 42번 기사"와 "다음 주 42번 기사"가 서로 다른 글이기
때문이다(004 마이그레이션 헤더 (2) 참고). 그래서 후보 조회는 SQL만으로 끝낼 수
없다 — normalize_url이 파이썬 함수라 DB에서 못 부른다. 행을 가져와 파이썬에서
정규화한 뒤 이미 라벨된 집합과 빼는 구조인 이유다(라벨 수·후보 수가 주당 수백
단위라 이 방식으로 충분하다).

**snippet은 자르지 않고 원문 그대로 저장한다** — dashboard_queries는 화면용으로
150자에 자르지만(truncate_summary), 이건 학습 입력이라 길수록 좋다. 자르는 건
UI가 보여줄 때만 한다.

    ./.venv/Scripts/python.exe -m tech_monitoring.labeling
"""

from tech_monitoring.utils.url_normalize import normalize_url

# db/migrations/004_article_labels.sql의 CHECK 제약과 같은 값이어야 한다.
LABEL_RELEVANT = "relevant"
LABEL_IRRELEVANT = "irrelevant"
VALID_LABELS = (LABEL_RELEVANT, LABEL_IRRELEVANT)


def _sort_by_published_at_desc_nulls_last(rows: list[dict]) -> list[dict]:
    """대시보드 기사 목록과 같은 정렬(최신순, 날짜 없는 건 뒤로) — 라벨링
    화면과 기사 목록의 순서가 달라 헷갈리는 일이 없게 맞춘다."""
    dated = sorted(
        (r for r in rows if r.get("published_at") is not None),
        key=lambda r: r["published_at"],
        reverse=True,
    )
    return dated + [r for r in rows if r.get("published_at") is None]


def fetch_labeled_url_norms(conn, fixed_keyword_id: int) -> set[str]:
    """이 고정 키워드에 대해 이미 라벨이 매겨진 정규화 URL 집합.
    키워드로 좁히는 게 핵심 — 같은 기사라도 다른 키워드에선 아직 안 본 것이다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT url_norm FROM article_labels WHERE fixed_keyword_id = %s",
            (fixed_keyword_id,),
        )
        return {row[0] for row in cur.fetchall()}


def _fetch_search_result_candidates(conn, run_id: int, fixed_keyword_id: int) -> list[dict]:
    """v2 수집분. search_results는 고정 키워드별로 검색한 결과라 행 하나가
    이미 "기사 × 키워드" 쌍이다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT title, url, snippet, source_domain, published_at "
            "FROM search_results WHERE run_id = %s AND fixed_keyword_id = %s",
            (run_id, fixed_keyword_id),
        )
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    for row in rows:
        row["source_table"] = "search_results"
    return rows


def _fetch_collected_article_candidates(conn, run_id: int) -> list[dict]:
    """v3 수집분. 키워드 무관 수집이라 어느 키워드 후보로 올릴지는 호출부가
    정한다(활성 키워드마다 한 번씩)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT title, url, snippet, source_domain, published_at "
            "FROM collected_articles WHERE run_id = %s",
            (run_id,),
        )
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    for row in rows:
        row["source_table"] = "collected_articles"
    return rows


def fetch_unlabeled_candidates(conn, run_id: int, fixed_keyword_id: int) -> list[dict]:
    """이 run에서 이 고정 키워드에 대해 **아직 라벨을 안 매긴** 기사 목록.

    반환 행에는 url_norm이 함께 들어있다 — 화면이 save_label에 그대로
    넘기면 되고, 정규화를 두 번 하지 않는다.
    """
    labeled = fetch_labeled_url_norms(conn, fixed_keyword_id)

    candidates = (
        _fetch_search_result_candidates(conn, run_id, fixed_keyword_id)
        + _fetch_collected_article_candidates(conn, run_id)
    )

    unlabeled: list[dict] = []
    seen: set[str] = set()
    for row in candidates:
        url_norm = normalize_url(row["url"])
        # 이미 라벨했거나(다른 주에 봤을 수도 있다), v2·v3가 같은 글을 각각
        # 수집해 이번 목록 안에서 겹치는 경우를 한 번만 보여준다.
        if url_norm in labeled or url_norm in seen:
            continue
        seen.add(url_norm)
        unlabeled.append({**row, "url_norm": url_norm})

    return _sort_by_published_at_desc_nulls_last(unlabeled)


def save_label(conn, fixed_keyword_id: int, article: dict, label: str, period_start) -> None:
    """라벨 저장(같은 기사·키워드를 다시 누르면 덮어쓴다).

    article은 fetch_unlabeled_candidates가 돌려준 행 그대로를 기대한다.
    학습에 필요한 필드를 전부 스냅샷으로 복사해 둔다 — 원본 행은 다음 주
    wipe로 사라지지만 라벨은 남아야 하기 때문이다(004 헤더 (1) 참고).
    """
    if label not in VALID_LABELS:
        raise ValueError(f"label은 {VALID_LABELS} 중 하나여야 한다: {label!r}")

    url_norm = article.get("url_norm") or normalize_url(article["url"])

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO article_labels (
                fixed_keyword_id, url_norm, label,
                title, snippet, url, source_domain, published_at,
                source_table, period_start
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url_norm, fixed_keyword_id) DO UPDATE SET
                label = EXCLUDED.label,
                labeled_at = now()
            """,
            (
                fixed_keyword_id, url_norm, label,
                article["title"], article.get("snippet"), article["url"],
                article.get("source_domain"), article.get("published_at"),
                article["source_table"], period_start,
            ),
        )


def count_labels(conn, fixed_keyword_id: int | None = None) -> dict:
    """진행률 표시용 집계이자 **학습 전에 반드시 확인해야 하는 클래스 분포**.

    후보가 이미 검색·큐레이션을 통과한 기사들이라 relevant로 크게 쏠릴
    가능성이 높은데, 그러면 "무조건 relevant"라고만 답하는 분류기도 정확도가
    높게 나와 지표가 무의미해진다. 학습 스크립트가 이 값을 먼저 찍어서
    쏠림을 눈으로 확인하고 넘어가게 한다.
    """
    query = "SELECT label, count(*) FROM article_labels"
    params: tuple = ()
    if fixed_keyword_id is not None:
        query += " WHERE fixed_keyword_id = %s"
        params = (fixed_keyword_id,)
    query += " GROUP BY label"

    with conn.cursor() as cur:
        cur.execute(query, params)
        counts = dict(cur.fetchall())

    relevant = counts.get(LABEL_RELEVANT, 0)
    irrelevant = counts.get(LABEL_IRRELEVANT, 0)
    return {"relevant": relevant, "irrelevant": irrelevant, "total": relevant + irrelevant}


def fetch_all_labels(conn) -> list[dict]:
    """학습용 전체 라벨. 고정 키워드 문자열을 JOIN해서 함께 돌려준다 —
    분류기 입력이 제목/요약만이 아니라 "어느 시장 기준인지"를 포함해야
    하기 때문이다(004 헤더 (3) 참고). fixed_keywords는 매주 wipe 대상이
    아니라 JOIN이 항상 성립한다.

    period_start도 함께 — 모델 평가 시 (주차 × 키워드) 그룹 단위로 fold를
    나누는 데 쓴다(기사 단위로 쪼개면 같은 주 같은 키워드 기사가 학습·검증에
    나뉘어 들어가 점수가 부풀려진다).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.id, l.fixed_keyword_id, fk.keyword AS fixed_keyword,
                   l.label, l.title, l.snippet, l.url, l.source_domain,
                   l.published_at, l.source_table, l.period_start, l.labeled_at
            FROM article_labels l
            JOIN fixed_keywords fk ON fk.id = l.fixed_keyword_id
            ORDER BY l.id
            """
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from tech_monitoring.db.connection import get_connection
    from tech_monitoring.db.weekly_run import get_active_fixed_keywords

    _conn = get_connection()
    try:
        with _conn.cursor() as _cur:
            _cur.execute("SELECT id FROM weekly_runs ORDER BY id DESC LIMIT 1")
            _row = _cur.fetchone()
        if _row is None:
            print("weekly_runs가 비어 있음 — 먼저 파이프라인을 실행할 것")
        else:
            _run_id = _row[0]
            _overall = count_labels(_conn)
            print(f"전체 라벨: {_overall['total']}건 "
                  f"(관련 {_overall['relevant']} / 무관 {_overall['irrelevant']})")
            for _kw in get_active_fixed_keywords(_conn):
                _todo = fetch_unlabeled_candidates(_conn, _run_id, _kw["id"])
                _done = count_labels(_conn, _kw["id"])
                print(f"  {_kw['keyword']}: 남은 후보 {len(_todo)}건 / 라벨 완료 {_done['total']}건")
    finally:
        _conn.close()
