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

from tech_monitoring.config import settings
from tech_monitoring.db.weekly_run import week_bounds_for
from tech_monitoring.utils.url_normalize import normalize_url

# db/migrations/004_article_labels.sql의 CHECK 제약과 같은 값이어야 한다.
LABEL_RELEVANT = "relevant"
LABEL_IRRELEVANT = "irrelevant"
VALID_LABELS = (LABEL_RELEVANT, LABEL_IRRELEVANT)

# count_labels/fetch_all_labels에서 "사람을 가리지 말고 전부"를 뜻하는 값.
# 실제 labeled_by 값으로는 쓰이지 않는다(005 참고).
ALL_LABELERS = "*"


# 발행일이 없는 기사를 담는 주차 버킷(화면의 "날짜 미상"). 날짜 대신 쓰는
# 값이라 date와 절대 겹치지 않는 문자열이어야 한다.
UNDATED = "undated"


def article_week_start(article: dict):
    """기사 발행 주의 월요일. 발행일이 없으면 None.

    라벨의 주차 그룹(period_start)과 화면의 주차 필터가 **같은 기준**을 써야
    한다 — 한쪽은 발행 주, 다른 쪽은 수집 주로 묶으면 "8/10 주만 골라
    라벨했는데 학습에서는 다른 주로 잡히는" 어긋남이 생긴다."""
    published = article.get("published_at")
    if published is None:
        return None
    day = published.date() if hasattr(published, "date") else published
    monday, _end = week_bounds_for(day)
    return monday


def _matches_week(article: dict, week_start) -> bool:
    """주차 필터 판정. week_start가 None이면 전체, UNDATED면 발행일 없는 것만."""
    if week_start is None:
        return True
    if week_start == UNDATED:
        return article.get("published_at") is None
    return article_week_start(article) == week_start


def _label_period_start(article: dict, run_period_start):
    """이 라벨을 어느 주로 묶을지 — **기사 발행 주**(월요일)를 쓴다.

    처음엔 run의 period_start(수집한 주)를 그대로 넣었는데, 최초 라벨링용
    소급 수집(scripts/backfill_past_weeks.py)이 지난 몇 주 기사를 이번 주
    run에 함께 담기 때문에 그러면 전부 같은 주가 된다. 그러면
    relevance_model.build_groups가 주차 단위로 fold를 나눌 수 없어
    ("다음 주 기사에도 통하는가"를 재는 가장 엄격한 평가) 기사 단위 분리로
    떨어진다. 발행 주를 쓰면 소급 수집분이 자연히 여러 주로 갈린다.

    발행일이 없는 기사(Tavily가 published_date를 안 주는 경우)는 run의
    period_start로 폴백한다 — 최소한 수집 시점은 항상 알 수 있다.
    """
    return article_week_start(article) or run_period_start


def _labeler(labeled_by: str | None) -> str:
    """라벨 주체(db/migrations/005_label_owner.sql). None이면 설정값을 쓴다.

    기본 인자에 settings.labeled_by를 직접 쓰지 않는 이유: 기본값은 def 시점에
    한 번만 바인딩돼서 테스트가 설정을 바꿔도 반영되지 않는다(keyword_merge.py의
    run_for_all_keywords가 같은 이유로 None 센티널을 쓴다).
    """
    return labeled_by if labeled_by is not None else settings.labeled_by


def _sort_by_published_at_desc_nulls_last(rows: list[dict]) -> list[dict]:
    """대시보드 기사 목록과 같은 정렬(최신순, 날짜 없는 건 뒤로) — 라벨링
    화면과 기사 목록의 순서가 달라 헷갈리는 일이 없게 맞춘다."""
    dated = sorted(
        (r for r in rows if r.get("published_at") is not None),
        key=lambda r: r["published_at"],
        reverse=True,
    )
    return dated + [r for r in rows if r.get("published_at") is None]


def fetch_labeled_url_norms(conn, fixed_keyword_id: int, labeled_by: str | None = None) -> set[str]:
    """이 사람이 이 고정 키워드에 대해 이미 라벨을 매긴 정규화 URL 집합.

    키워드로 좁히는 게 핵심 — 같은 기사라도 다른 키워드에선 아직 안 본 것이다.
    **사람으로도 좁힌다**: 공용 DB에서 남이 이미 라벨한 기사를 내 화면에서
    빼버리면 내 기준이 학습 데이터에 들어갈 기회를 잃는다(005 참고).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT url_norm FROM article_labels "
            "WHERE fixed_keyword_id = %s AND labeled_by = %s",
            (fixed_keyword_id, _labeler(labeled_by)),
        )
        return {row[0] for row in cur.fetchall()}


def fetch_label_map(conn, fixed_keyword_id: int, labeled_by: str | None = None) -> dict[str, str]:
    """이 사람이 이 고정 키워드에 매긴 라벨 맵(url_norm -> label).

    fetch_labeled_url_norms는 "있다/없다"만 알려줘서 부족하다 — 인라인
    👍/👎 버튼(2026-08-24, 🏷️ 라벨링 탭을 대체)이 지금 상태를 알아야
    "같은 버튼을 다시 누르면 취소, 반대 버튼을 누르면 뒤집기"를 판정할 수
    있다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT url_norm, label FROM article_labels "
            "WHERE fixed_keyword_id = %s AND labeled_by = %s",
            (fixed_keyword_id, _labeler(labeled_by)),
        )
        return dict(cur.fetchall())


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


def fetch_unlabeled_candidates(
    conn, run_id: int, fixed_keyword_id: int, labeled_by: str | None = None,
    week_start=None,
) -> list[dict]:
    """이 run에서 이 고정 키워드에 대해 **이 사람이 아직 라벨을 안 매긴** 기사 목록.

    반환 행에는 url_norm이 함께 들어있다 — 화면이 save_label에 그대로
    넘기면 되고, 정규화를 두 번 하지 않는다.

    week_start를 주면 그 발행 주만 남긴다(UNDATED면 발행일 없는 것만).
    소급 수집분이 한 run에 3주치 섞여 있는데 후보가 최신순이라, 필터가 없으면
    최신 주부터 순서대로 라벨하게 된다 — 그러면 라벨이 한 주에 몰려서
    주차 단위 교차검증("다음 주 기사에도 통하는가")이 성립하지 않는다
    (실측 2026-08-19: 라벨 30건이 전부 8/17 주였다).
    """
    labeled = fetch_labeled_url_norms(conn, fixed_keyword_id, labeled_by)

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
        if not _matches_week(row, week_start):
            continue
        unlabeled.append({**row, "url_norm": url_norm})

    return _sort_by_published_at_desc_nulls_last(unlabeled)


def save_label(
    conn, fixed_keyword_id: int, article: dict, label: str, period_start,
    labeled_by: str | None = None,
) -> None:
    """라벨 저장(**같은 사람이** 같은 기사·키워드를 다시 누르면 덮어쓴다).

    article은 fetch_unlabeled_candidates가 돌려준 행 그대로를 기대한다.
    학습에 필요한 필드를 전부 스냅샷으로 복사해 둔다 — 원본 행은 다음 주
    wipe로 사라지지만 라벨은 남아야 하기 때문이다(004 헤더 (1) 참고).

    UPSERT 대상이 (url_norm, fixed_keyword_id, labeled_by)라 다른 사람의
    판단은 덮어쓰지 않고 별도 행으로 남는다(005 참고).
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
                source_table, period_start, labeled_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url_norm, fixed_keyword_id, labeled_by) DO UPDATE SET
                label = EXCLUDED.label,
                labeled_at = now()
            """,
            (
                fixed_keyword_id, url_norm, label,
                article["title"], article.get("snippet"), article["url"],
                article.get("source_domain"), article.get("published_at"),
                article["source_table"], _label_period_start(article, period_start),
                _labeler(labeled_by),
            ),
        )


def fetch_recent_labels(
    conn, fixed_keyword_id: int, labeled_by: str | None = None, limit: int = 20,
) -> list[dict]:
    """내가 이 시장에서 최근에 매긴 라벨 — 마지막에 누른 것부터.

    검토·수정 화면용이다. 라벨링은 카드 하나씩 빠르게 넘기는 작업이라 잘못
    누르는 일이 생기는데, 그 판단이 그대로 학습 데이터가 되므로 되돌릴
    방법이 있어야 한다. 방금 누른 것부터 보여주는 게 실수를 찾는 데 가장
    효율적이라 labeled_at 내림차순이다(id 순은 처음 라벨한 것부터라 반대).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT url_norm, url, title, label, source_domain, published_at, labeled_at
            FROM article_labels
            WHERE fixed_keyword_id = %s AND labeled_by = %s
            ORDER BY labeled_at DESC
            LIMIT %s
            """,
            (fixed_keyword_id, _labeler(labeled_by), limit),
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def update_label(
    conn, url_norm: str, fixed_keyword_id: int, label: str, labeled_by: str | None = None,
) -> bool:
    """이미 매긴 라벨을 바꾼다(검토 화면의 "반대로 바꾸기").

    save_label은 기사 스냅샷 전체를 요구하지만 여기서 필요한 건 label 하나다 —
    이미 저장된 행의 제목·요약을 다시 넘길 이유가 없다. **labeled_by로 좁혀야
    한다**: 공용 DB에서 남의 판단을 내가 뒤집으면 그 사람 학습 데이터가
    조용히 바뀐다(005).

    바뀐 행이 없으면 False(이미 지웠거나 남의 라벨을 건드리려 한 경우).
    """
    if label not in VALID_LABELS:
        raise ValueError(f"label은 {VALID_LABELS} 중 하나여야 한다: {label!r}")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE article_labels SET label = %s, labeled_at = now()
            WHERE url_norm = %s AND fixed_keyword_id = %s AND labeled_by = %s
            RETURNING id
            """,
            (label, url_norm, fixed_keyword_id, _labeler(labeled_by)),
        )
        return cur.fetchone() is not None


def delete_label(
    conn, url_norm: str, fixed_keyword_id: int, labeled_by: str | None = None,
) -> bool:
    """라벨을 취소한다 — 그 기사는 다시 라벨링 후보로 돌아온다.

    "반대로 바꾸기"와 다른 선택지가 필요한 이유: 눌러보고 나서 **판단이 안
    선다**고 느끼는 경우가 있는데, 억지로 한쪽을 고르면 학습 데이터가
    오염된다(카드의 "판단 보류"와 같은 논리). 지우면 후보 목록에 다시 뜬다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM article_labels "
            "WHERE url_norm = %s AND fixed_keyword_id = %s AND labeled_by = %s "
            "RETURNING id",
            (url_norm, fixed_keyword_id, _labeler(labeled_by)),
        )
        return cur.fetchone() is not None


def count_labels(
    conn, fixed_keyword_id: int | None = None, labeled_by: str | None = None,
) -> dict:
    """진행률 표시용 집계이자 **학습 전에 반드시 확인해야 하는 클래스 분포**.

    후보가 이미 검색·큐레이션을 통과한 기사들이라 relevant로 크게 쏠릴
    가능성이 높은데, 그러면 "무조건 relevant"라고만 답하는 분류기도 정확도가
    높게 나와 지표가 무의미해진다. 학습 스크립트가 이 값을 먼저 찍어서
    쏠림을 눈으로 확인하고 넘어가게 한다.

    labeled_by=ALL_LABELERS면 사람을 가리지 않고 전부 센다(팀 통합 관점).
    기본값은 내 라벨만 — 라벨링 화면의 진행률이 남의 작업량까지 더해
    부풀어 보이면 내가 얼마나 했는지 알 수 없다.
    """
    conditions: list[str] = []
    params: list = []
    if fixed_keyword_id is not None:
        conditions.append("fixed_keyword_id = %s")
        params.append(fixed_keyword_id)
    if labeled_by != ALL_LABELERS:
        conditions.append("labeled_by = %s")
        params.append(_labeler(labeled_by))

    query = "SELECT label, count(*) FROM article_labels"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " GROUP BY label"

    with conn.cursor() as cur:
        cur.execute(query, tuple(params))
        counts = dict(cur.fetchall())

    relevant = counts.get(LABEL_RELEVANT, 0)
    irrelevant = counts.get(LABEL_IRRELEVANT, 0)
    return {"relevant": relevant, "irrelevant": irrelevant, "total": relevant + irrelevant}


def fetch_all_labels(conn, labeled_by: str | None = None) -> list[dict]:
    """학습용 전체 라벨. 고정 키워드 문자열을 JOIN해서 함께 돌려준다 —
    분류기 입력이 제목/요약만이 아니라 "어느 시장 기준인지"를 포함해야
    하기 때문이다(004 헤더 (3) 참고). fixed_keywords는 매주 wipe 대상이
    아니라 JOIN이 항상 성립한다.

    url_norm·period_start도 함께 — 평가에서 fold를 나눌 그룹 키로 쓴다.
    같은 기사가 여러 키워드에 걸쳐 라벨돼 있어(실측 262건 중 56개 URL) 그냥
    무작위로 쪼개면 같은 기사가 학습·검증 양쪽에 들어가 점수가 부풀려진다.
    relevance_model.build_groups 참고.

    기본은 내 라벨만 학습한다(개인 모델). labeled_by=ALL_LABELERS로 부르면
    팀 전체 라벨로 학습한다(통합 모델) — 어느 쪽이 나은지는 라벨이 쌓인 뒤
    같은 채점 틀로 비교해서 정한다(005 참고). labeled_by를 함께 돌려주므로
    나중에 그 비교나 사람 간 불일치 측정에 그대로 쓸 수 있다.
    """
    query = """
        SELECT l.id, l.fixed_keyword_id, fk.keyword AS fixed_keyword,
               l.label, l.title, l.snippet, l.url, l.url_norm, l.source_domain,
               l.published_at, l.source_table, l.period_start, l.labeled_at,
               l.labeled_by
        FROM article_labels l
        JOIN fixed_keywords fk ON fk.id = l.fixed_keyword_id
    """
    params: tuple = ()
    if labeled_by != ALL_LABELERS:
        query += " WHERE l.labeled_by = %s"
        params = (_labeler(labeled_by),)
    query += " ORDER BY l.id"

    with conn.cursor() as cur:
        cur.execute(query, params)
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
