"""labeling.py 테스트. test_dashboard_queries.py와 같은 방식으로 실제 DB 없이
딕셔너리 리스트를 흉내내는 스텁 conn/cursor를 쓴다 — 어떤 SQL이 나갔는지가
아니라 "무엇이 후보로 남고 무엇이 걸러지는지"에 집중한다.

여기서 반드시 지켜야 하는 두 가지(둘 다 004 마이그레이션 헤더의 함정):
  - 중복 판정은 원본 행 id가 아니라 정규화된 URL로 한다(id는 매주 재사용됨).
  - 라벨은 기사 단위가 아니라 "기사 × 고정 키워드" 단위다.
"""

from datetime import date, datetime

import pytest

from tech_monitoring import labeling


class _FakeCursor:
    def __init__(self, tables: dict, inserts: list):
        self._tables = tables
        self._inserts = inserts
        self._rows: list[tuple] = []
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _set(self, cols, rows):
        self.description = [type("Col", (), {"name": n})() for n in cols]
        self._rows = [tuple(r.get(c) for c in cols) for r in rows]

    def execute(self, query, params=()):
        article_cols = ("title", "url", "snippet", "source_domain", "published_at")

        if "INSERT INTO article_labels" in query:
            self._inserts.append(params)
            self._rows = []
        elif "SELECT url_norm FROM article_labels" in query:
            (fixed_keyword_id,) = params
            rows = [r for r in self._tables.get("article_labels", [])
                    if r["fixed_keyword_id"] == fixed_keyword_id]
            self._set(("url_norm",), rows)
        elif "count(*) FROM article_labels" in query:
            rows = self._tables.get("article_labels", [])
            if params:
                rows = [r for r in rows if r["fixed_keyword_id"] == params[0]]
            counts: dict[str, int] = {}
            for r in rows:
                counts[r["label"]] = counts.get(r["label"], 0) + 1
            self._rows = list(counts.items())
        elif "FROM article_labels l" in query:
            keywords = {k["id"]: k["keyword"] for k in self._tables.get("fixed_keywords", [])}
            rows = [{**r, "fixed_keyword": keywords[r["fixed_keyword_id"]]}
                    for r in self._tables.get("article_labels", [])
                    if r["fixed_keyword_id"] in keywords]
            self._set(("id", "fixed_keyword_id", "fixed_keyword", "label", "title"), rows)
        elif "FROM search_results" in query:
            run_id, fixed_keyword_id = params
            rows = [r for r in self._tables.get("search_results", [])
                    if r["run_id"] == run_id and r["fixed_keyword_id"] == fixed_keyword_id]
            self._set(article_cols, rows)
        elif "FROM collected_articles" in query:
            (run_id,) = params
            rows = [r for r in self._tables.get("collected_articles", [])
                    if r["run_id"] == run_id]
            self._set(article_cols, rows)
        else:
            raise AssertionError(f"스텁이 모르는 질의: {query}")

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, **tables):
        self.tables = tables
        self.inserts: list = []

    def cursor(self):
        return _FakeCursor(self.tables, self.inserts)


def _article(url, *, run_id=1, fixed_keyword_id=1, title="제목", published_at=None):
    return {
        "run_id": run_id, "fixed_keyword_id": fixed_keyword_id, "title": title,
        "url": url, "snippet": "요약", "source_domain": "example.com",
        "published_at": published_at,
    }


# --- 후보 조회 -------------------------------------------------------------

def test_returns_all_candidates_when_nothing_labeled_yet():
    conn = _FakeConn(search_results=[_article("https://a.com/1"), _article("https://a.com/2")])

    result = labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=1)

    assert [r["url"] for r in result] == ["https://a.com/1", "https://a.com/2"]
    assert all(r["source_table"] == "search_results" for r in result)


def test_already_labeled_article_is_excluded():
    conn = _FakeConn(
        search_results=[_article("https://a.com/1"), _article("https://a.com/2")],
        article_labels=[{"fixed_keyword_id": 1, "url_norm": "https://a.com/1", "label": "relevant"}],
    )

    result = labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=1)

    assert [r["url"] for r in result] == ["https://a.com/2"]


def test_dedup_uses_normalized_url_not_raw_string():
    """utm 파라미터·www./모바일 변형이 붙어도 같은 기사로 보고 다시 안 띄운다
    (utils/url_normalize의 정규화를 그대로 탄다)."""
    conn = _FakeConn(
        search_results=[_article("https://www.a.com/1?utm_source=newsletter")],
        article_labels=[{"fixed_keyword_id": 1, "url_norm": "https://a.com/1", "label": "relevant"}],
    )

    assert labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=1) == []


def test_label_for_another_keyword_does_not_hide_the_article():
    """핵심 — 라벨은 "기사 × 키워드" 단위다. "교육"에서 이미 본 기사라도
    "비즈니스 실적"에선 아직 판단한 적이 없으므로 후보로 남아야 한다."""
    conn = _FakeConn(
        search_results=[_article("https://a.com/1", fixed_keyword_id=2)],
        article_labels=[{"fixed_keyword_id": 1, "url_norm": "https://a.com/1", "label": "relevant"}],
    )

    result = labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=2)

    assert [r["url"] for r in result] == ["https://a.com/1"]


def test_same_article_collected_by_both_pipelines_appears_once():
    conn = _FakeConn(
        search_results=[_article("https://a.com/1")],
        collected_articles=[_article("https://www.a.com/1/")],
    )

    result = labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=1)

    assert len(result) == 1


def test_candidates_are_sorted_newest_first_with_undated_last():
    conn = _FakeConn(search_results=[
        _article("https://a.com/old", published_at=datetime(2026, 8, 10)),
        _article("https://a.com/none"),
        _article("https://a.com/new", published_at=datetime(2026, 8, 15)),
    ])

    result = labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=1)

    assert [r["url"].rsplit("/", 1)[-1] for r in result] == ["new", "old", "none"]


def test_candidates_carry_url_norm_for_saving():
    """화면이 save_label에 행을 그대로 넘기면 되도록 정규화 결과를 실어 보낸다."""
    conn = _FakeConn(search_results=[_article("https://www.a.com/1?utm_source=x")])

    (row,) = labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=1)

    assert row["url_norm"] == "https://a.com/1"


# --- 저장 -----------------------------------------------------------------

def test_save_label_snapshots_article_fields():
    conn = _FakeConn()
    article = {**_article("https://www.a.com/1?utm_source=x"), "source_table": "search_results"}

    labeling.save_label(conn, 1, article, labeling.LABEL_RELEVANT, date(2026, 8, 10))

    (params,) = conn.inserts
    assert params[0] == 1                       # fixed_keyword_id
    assert params[1] == "https://a.com/1"       # url_norm(정규화됨)
    assert params[2] == "relevant"
    assert params[3] == "제목"                   # 원본이 wipe돼도 남을 스냅샷
    assert params[9] == date(2026, 8, 10)       # period_start


def test_save_label_rejects_unknown_label():
    """DB CHECK 제약에 도달하기 전에 코드에서 먼저 막는다(오타로 조용히 들어가면
    학습 데이터가 오염된다)."""
    conn = _FakeConn()
    article = {**_article("https://a.com/1"), "source_table": "search_results"}

    with pytest.raises(ValueError):
        labeling.save_label(conn, 1, article, "maybe", date(2026, 8, 10))

    assert conn.inserts == []


# --- 집계 -----------------------------------------------------------------

def test_count_labels_reports_class_distribution():
    """학습 전 쏠림 확인용 — relevant로 크게 쏠리면 지표가 무의미해진다."""
    conn = _FakeConn(article_labels=[
        {"fixed_keyword_id": 1, "url_norm": "u1", "label": "relevant"},
        {"fixed_keyword_id": 1, "url_norm": "u2", "label": "relevant"},
        {"fixed_keyword_id": 2, "url_norm": "u3", "label": "irrelevant"},
    ])

    assert labeling.count_labels(conn) == {"relevant": 2, "irrelevant": 1, "total": 3}
    assert labeling.count_labels(conn, fixed_keyword_id=2) == {
        "relevant": 0, "irrelevant": 1, "total": 1,
    }


def test_fetch_all_labels_joins_keyword_text():
    """분류기 입력에 "어느 시장 기준인지"가 들어가야 하므로 키워드 문자열이 필요하다."""
    conn = _FakeConn(
        fixed_keywords=[{"id": 1, "keyword": "교육"}],
        article_labels=[{"id": 10, "fixed_keyword_id": 1, "url_norm": "u1",
                         "label": "relevant", "title": "제목"}],
    )

    (row,) = labeling.fetch_all_labels(conn)

    assert row["fixed_keyword"] == "교육"
    assert row["label"] == "relevant"
