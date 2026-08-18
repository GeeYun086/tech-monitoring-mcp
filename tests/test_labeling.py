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
    def __init__(self, tables: dict, inserts: list, updates: list, deletes: list):
        self._tables = tables
        self._inserts = inserts
        self._updates = updates
        self._deletes = deletes
        self._rows: list[tuple] = []
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _labels(self) -> list[dict]:
        """article_labels 행. labeled_by를 안 적은 픽스처는 기본 라벨러로 본다
        (005 이전에 쓴 테스트가 그대로 통과해야 한다 — DB 기본값도 'local')."""
        return [{"labeled_by": "local", **r} for r in self._tables.get("article_labels", [])]

    def _set(self, cols, rows):
        self.description = [type("Col", (), {"name": n})() for n in cols]
        self._rows = [tuple(r.get(c) for c in cols) for r in rows]

    def execute(self, query, params=()):
        article_cols = ("title", "url", "snippet", "source_domain", "published_at")

        if "INSERT INTO article_labels" in query:
            self._inserts.append(params)
            self._rows = []
        elif "SELECT url_norm FROM article_labels" in query:
            fixed_keyword_id, labeled_by = params
            rows = [r for r in self._labels()
                    if r["fixed_keyword_id"] == fixed_keyword_id
                    and r["labeled_by"] == labeled_by]
            self._set(("url_norm",), rows)
        elif "count(*) FROM article_labels" in query:
            # WHERE 절이 조건에 따라 붙었다 말았다 하므로 질의 문자열을 보고
            # params를 순서대로 꺼낸다(labeling.count_labels와 같은 순서).
            rows, rest = self._labels(), list(params)
            if "fixed_keyword_id = %s" in query:
                wanted = rest.pop(0)   # 컴프리헨션 안에서 꺼내면 행마다 pop된다
                rows = [r for r in rows if r["fixed_keyword_id"] == wanted]
            if "labeled_by = %s" in query:
                wanted = rest.pop(0)
                rows = [r for r in rows if r["labeled_by"] == wanted]
            counts: dict[str, int] = {}
            for r in rows:
                counts[r["label"]] = counts.get(r["label"], 0) + 1
            self._rows = list(counts.items())
        elif "FROM article_labels" in query and "ORDER BY labeled_at DESC" in query:
            fixed_keyword_id, labeled_by, limit = params
            rows = [r for r in self._labels()
                    if r["fixed_keyword_id"] == fixed_keyword_id
                    and r["labeled_by"] == labeled_by]
            rows.sort(key=lambda r: r.get("labeled_at") or 0, reverse=True)
            self._set(("url_norm", "url", "title", "label", "source_domain",
                       "published_at", "labeled_at"), rows[:limit])
        elif "UPDATE article_labels" in query:
            label, url_norm, fixed_keyword_id, labeled_by = params
            self._updates.append((url_norm, fixed_keyword_id, labeled_by, label))
            hit = [r for r in self._labels()
                   if r["url_norm"] == url_norm and r["fixed_keyword_id"] == fixed_keyword_id
                   and r["labeled_by"] == labeled_by]
            self._rows = [(1,)] if hit else []
        elif "DELETE FROM article_labels" in query:
            url_norm, fixed_keyword_id, labeled_by = params
            self._deletes.append((url_norm, fixed_keyword_id, labeled_by))
            hit = [r for r in self._labels()
                   if r["url_norm"] == url_norm and r["fixed_keyword_id"] == fixed_keyword_id
                   and r["labeled_by"] == labeled_by]
            self._rows = [(1,)] if hit else []
        elif "FROM article_labels l" in query:
            keywords = {k["id"]: k["keyword"] for k in self._tables.get("fixed_keywords", [])}
            rows = [{**r, "fixed_keyword": keywords[r["fixed_keyword_id"]]}
                    for r in self._labels() if r["fixed_keyword_id"] in keywords]
            if "l.labeled_by = %s" in query:
                (labeled_by,) = params
                rows = [r for r in rows if r["labeled_by"] == labeled_by]
            self._set(
                ("id", "fixed_keyword_id", "fixed_keyword", "label", "title", "labeled_by"),
                rows,
            )
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

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, **tables):
        self.tables = tables
        self.inserts: list = []
        self.updates: list = []
        self.deletes: list = []

    def cursor(self):
        return _FakeCursor(self.tables, self.inserts, self.updates, self.deletes)


@pytest.fixture(autouse=True)
def _fixed_labeler(monkeypatch):
    """라벨러 기본값을 고정한다 — .env의 LABELED_BY가 채워져 있으면 테스트
    결과가 실행 환경에 따라 달라지기 때문이다(005 참고)."""
    monkeypatch.setattr(labeling.settings, "labeled_by", "local")


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


def test_another_persons_label_does_not_hide_the_article():
    """공용 DB(005)에서 남이 이미 판단한 기사도 내 후보로는 남아야 한다 —
    빼버리면 같은 기사에 대한 내 기준이 학습 데이터에 들어갈 길이 없다."""
    conn = _FakeConn(
        search_results=[_article("https://a.com/1")],
        article_labels=[{"fixed_keyword_id": 1, "url_norm": "https://a.com/1",
                         "label": "relevant", "labeled_by": "동료"}],
    )

    result = labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=1)

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


def test_save_label_records_who_labeled_it():
    """005 — 라벨 주체가 함께 저장돼야 나중에 개인 모델/통합 모델을 갈라
    채점할 수 있다. 안 적어두면 소급이 불가능하다."""
    conn = _FakeConn()
    article = {**_article("https://a.com/1"), "source_table": "search_results"}

    labeling.save_label(conn, 1, article, labeling.LABEL_RELEVANT, date(2026, 8, 10),
                        labeled_by="지윤")

    (params,) = conn.inserts
    assert params[10] == "지윤"


def test_save_label_falls_back_to_configured_labeler():
    conn = _FakeConn()
    article = {**_article("https://a.com/1"), "source_table": "search_results"}

    labeling.save_label(conn, 1, article, labeling.LABEL_RELEVANT, date(2026, 8, 10))

    (params,) = conn.inserts
    assert params[10] == "local"


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


def test_count_labels_counts_only_my_labels_by_default():
    """진행률이 남의 작업량까지 더해 부풀면 내가 얼마나 했는지 알 수 없다."""
    conn = _FakeConn(article_labels=[
        {"fixed_keyword_id": 1, "url_norm": "u1", "label": "relevant"},
        {"fixed_keyword_id": 1, "url_norm": "u2", "label": "relevant", "labeled_by": "동료"},
    ])

    assert labeling.count_labels(conn) == {"relevant": 1, "irrelevant": 0, "total": 1}
    assert labeling.count_labels(conn, labeled_by=labeling.ALL_LABELERS) == {
        "relevant": 2, "irrelevant": 0, "total": 2,
    }


def test_fetch_all_labels_can_learn_from_everyone_or_just_me():
    """개인 모델(기본)과 통합 모델을 같은 함수로 뽑을 수 있어야, 나중에
    어느 쪽이 나은지 같은 채점 틀로 비교할 수 있다(005)."""
    conn = _FakeConn(
        fixed_keywords=[{"id": 1, "keyword": "교육"}],
        article_labels=[
            {"id": 10, "fixed_keyword_id": 1, "url_norm": "u1", "label": "relevant",
             "title": "내 판단"},
            {"id": 11, "fixed_keyword_id": 1, "url_norm": "u2", "label": "irrelevant",
             "title": "동료 판단", "labeled_by": "동료"},
        ],
    )

    mine = labeling.fetch_all_labels(conn)
    everyone = labeling.fetch_all_labels(conn, labeled_by=labeling.ALL_LABELERS)

    assert [r["title"] for r in mine] == ["내 판단"]
    assert [r["labeled_by"] for r in everyone] == ["local", "동료"]


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


# --- 라벨의 주차 그룹(작업 4: 소급 수집) ---------------------------------

def test_label_period_comes_from_article_publication_week():
    """소급 수집분이 이번 주 run에 함께 담기므로 run 기준으로 묶으면 전부 같은
    주가 된다 — 그러면 주차 단위로 fold를 나눌 수 없다(relevance_model.
    build_groups). 기사 발행 주로 묶어야 소급분이 여러 주로 갈린다."""
    conn = _FakeConn()
    article = {**_article("https://a.com/1", published_at=datetime(2026, 8, 6)),
               "source_table": "collected_articles"}

    labeling.save_label(conn, 1, article, labeling.LABEL_RELEVANT, date(2026, 8, 17))

    (params,) = conn.inserts
    assert params[9] == date(2026, 8, 3)      # 8/6은 8/3(월) 주


def test_label_period_falls_back_to_run_week_without_publication_date():
    """Tavily가 published_date를 안 주는 기사도 있다 — 그때는 수집 주를 쓴다."""
    conn = _FakeConn()
    article = {**_article("https://a.com/1"), "source_table": "collected_articles"}

    labeling.save_label(conn, 1, article, labeling.LABEL_RELEVANT, date(2026, 8, 17))

    (params,) = conn.inserts
    assert params[9] == date(2026, 8, 17)


# --- 라벨 검토·수정(작업 5) -------------------------------------------------

def _label_row(url_norm, *, label="relevant", fixed_keyword_id=1, labeled_by="local",
               labeled_at=None, title="제목"):
    return {"url_norm": url_norm, "url": url_norm, "title": title, "label": label,
            "fixed_keyword_id": fixed_keyword_id, "labeled_by": labeled_by,
            "source_domain": "example.com", "published_at": None, "labeled_at": labeled_at}


def test_recent_labels_show_last_clicked_first():
    """실수는 방금 누른 것에서 찾는 게 가장 빠르다 — labeled_at 내림차순."""
    conn = _FakeConn(article_labels=[
        _label_row("u1", labeled_at=datetime(2026, 8, 19, 10, 0), title="먼저"),
        _label_row("u2", labeled_at=datetime(2026, 8, 19, 11, 0), title="나중"),
    ])

    rows = labeling.fetch_recent_labels(conn, fixed_keyword_id=1)

    assert [r["title"] for r in rows] == ["나중", "먼저"]


def test_recent_labels_only_mine_and_this_market():
    conn = _FakeConn(article_labels=[
        _label_row("mine", labeled_at=datetime(2026, 8, 19, 10, 0)),
        _label_row("other-person", labeled_by="동료", labeled_at=datetime(2026, 8, 19, 11, 0)),
        _label_row("other-market", fixed_keyword_id=2, labeled_at=datetime(2026, 8, 19, 12, 0)),
    ])

    rows = labeling.fetch_recent_labels(conn, fixed_keyword_id=1)

    assert [r["url_norm"] for r in rows] == ["mine"]


def test_recent_labels_respects_limit():
    conn = _FakeConn(article_labels=[
        _label_row(f"u{i}", labeled_at=datetime(2026, 8, 19, 10, i)) for i in range(30)
    ])

    assert len(labeling.fetch_recent_labels(conn, fixed_keyword_id=1, limit=5)) == 5


def test_update_label_flips_only_my_row():
    conn = _FakeConn(article_labels=[_label_row("u1", label="relevant")])

    changed = labeling.update_label(conn, "u1", 1, labeling.LABEL_IRRELEVANT)

    assert changed is True
    assert conn.updates == [("u1", 1, "local", "irrelevant")]


def test_update_label_does_not_touch_another_persons_judgement():
    """공용 DB에서 남의 판단을 내가 뒤집으면 그 사람 학습 데이터가 조용히 바뀐다(005)."""
    conn = _FakeConn(article_labels=[_label_row("u1", labeled_by="동료")])

    assert labeling.update_label(conn, "u1", 1, labeling.LABEL_IRRELEVANT) is False


def test_update_label_rejects_unknown_label():
    conn = _FakeConn(article_labels=[_label_row("u1")])

    with pytest.raises(ValueError):
        labeling.update_label(conn, "u1", 1, "maybe")

    assert conn.updates == []


def test_delete_label_returns_the_article_to_the_candidate_pool():
    """눌러보고 나서 판단이 안 서면 지우는 게 맞다 — 억지로 한쪽을 고르면
    학습 데이터가 오염된다(카드의 "판단 보류"와 같은 논리)."""
    conn = _FakeConn(article_labels=[_label_row("u1")])

    assert labeling.delete_label(conn, "u1", 1) is True
    assert conn.deletes == [("u1", 1, "local")]


def test_delete_label_scoped_to_me():
    conn = _FakeConn(article_labels=[_label_row("u1", labeled_by="동료")])

    assert labeling.delete_label(conn, "u1", 1) is False


def test_candidates_can_be_scoped_to_one_publication_week():
    """소급 수집분이 한 run에 여러 주 섞여 있다 — 후보가 최신순이라 필터가
    없으면 라벨이 최신 주에만 몰려 주차 단위 교차검증이 성립하지 않는다."""
    conn = _FakeConn(collected_articles=[
        _article("https://a.com/this-week", published_at=datetime(2026, 8, 18)),
        _article("https://a.com/last-week", published_at=datetime(2026, 8, 11)),
    ])

    result = labeling.fetch_unlabeled_candidates(
        conn, run_id=1, fixed_keyword_id=1, week_start=date(2026, 8, 10),
    )

    assert [r["url"] for r in result] == ["https://a.com/last-week"]


def test_candidates_can_be_scoped_to_undated_articles():
    conn = _FakeConn(collected_articles=[
        _article("https://a.com/dated", published_at=datetime(2026, 8, 18)),
        _article("https://a.com/undated"),
    ])

    result = labeling.fetch_unlabeled_candidates(
        conn, run_id=1, fixed_keyword_id=1, week_start=labeling.UNDATED,
    )

    assert [r["url"] for r in result] == ["https://a.com/undated"]


def test_candidates_without_week_filter_include_every_week():
    conn = _FakeConn(collected_articles=[
        _article("https://a.com/1", published_at=datetime(2026, 8, 18)),
        _article("https://a.com/2", published_at=datetime(2026, 8, 11)),
        _article("https://a.com/3"),
    ])

    assert len(labeling.fetch_unlabeled_candidates(conn, run_id=1, fixed_keyword_id=1)) == 3
