"""scripts/manage_fixed_keywords.py의 DB 조작 함수 테스트. 실제 DB 없이
fixed_keywords 테이블을 딕셔너리 리스트로 흉내낸 최소 스텁을 쓴다."""

from scripts.manage_fixed_keywords import (
    add_keyword,
    list_keywords,
    remove_keyword,
    set_active,
    set_search_terms,
    set_sites,
)

_COLS = ("id", "keyword", "display_order", "active", "created_at",
         "search_terms_ko", "search_terms_en", "site_domains")


class _FakeCursor:
    def __init__(self, table: list[dict]):
        self._table = table
        self.description = None
        self._result_rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=()):
        if query.startswith("SELECT"):
            rows = sorted(self._table, key=lambda r: (r["display_order"], r["id"]))
            self.description = [type("Col", (), {"name": n})() for n in _COLS]
            self._result_rows = [tuple(r.get(c) for c in _COLS) for r in rows]
        elif "INSERT INTO fixed_keywords" in query:
            keyword, display_order = params
            existing = next((r for r in self._table if r["keyword"] == keyword), None)
            if existing:
                existing["display_order"] = display_order
                existing["active"] = True
            else:
                self._table.append({
                    "id": len(self._table) + 1, "keyword": keyword,
                    "display_order": display_order, "active": True, "created_at": None,
                    "search_terms_ko": [], "search_terms_en": [], "site_domains": None,
                })
        elif query.startswith("UPDATE fixed_keywords SET active"):
            active, keyword = params
            row = next((r for r in self._table if r["keyword"] == keyword), None)
            self._result_rows = []
            if row is not None:
                row["active"] = active
                self._result_rows = [(row["id"],)]
        elif query.startswith("UPDATE fixed_keywords SET search_terms_ko"):
            terms_ko, terms_en, keyword = params
            row = next((r for r in self._table if r["keyword"] == keyword), None)
            self._result_rows = []
            if row is not None:
                row["search_terms_ko"] = terms_ko
                row["search_terms_en"] = terms_en
                self._result_rows = [(row["id"],)]
        elif query.startswith("UPDATE fixed_keywords SET site_domains"):
            sites, keyword = params
            row = next((r for r in self._table if r["keyword"] == keyword), None)
            self._result_rows = []
            if row is not None:
                row["site_domains"] = sites
                self._result_rows = [(row["id"],)]
        elif query.startswith("DELETE FROM fixed_keywords"):
            (keyword,) = params
            row = next((r for r in self._table if r["keyword"] == keyword), None)
            self._result_rows = []
            if row is not None:
                self._table.remove(row)
                self._result_rows = [(row["id"],)]

    def fetchone(self):
        return self._result_rows[0] if self._result_rows else None

    def fetchall(self):
        return self._result_rows


class _FakeConn:
    def __init__(self, table=None):
        self.table = table if table is not None else []

    def cursor(self):
        return _FakeCursor(self.table)


def test_add_keyword_inserts_new_row():
    conn = _FakeConn()
    add_keyword(conn, "AX 시장", 1)
    rows = list_keywords(conn)
    assert len(rows) == 1
    assert rows[0]["keyword"] == "AX 시장"
    assert rows[0]["active"] is True


def test_add_keyword_reactivates_existing_inactive_keyword():
    """deactivate 해뒀던 키워드를 다시 add하면 active=True로 되살아나야 한다."""
    conn = _FakeConn(table=[
        {"id": 1, "keyword": "AX 시장", "display_order": 0, "active": False, "created_at": None},
    ])
    add_keyword(conn, "AX 시장", 5)
    rows = list_keywords(conn)
    assert rows[0]["active"] is True
    assert rows[0]["display_order"] == 5


def test_set_active_returns_false_for_unknown_keyword():
    conn = _FakeConn()
    assert set_active(conn, "없는 키워드", False) is False


def test_deactivate_then_activate_roundtrip():
    conn = _FakeConn(table=[
        {"id": 1, "keyword": "AX 시장", "display_order": 0, "active": True, "created_at": None},
    ])
    assert set_active(conn, "AX 시장", False) is True
    assert list_keywords(conn)[0]["active"] is False
    assert set_active(conn, "AX 시장", True) is True
    assert list_keywords(conn)[0]["active"] is True


def test_remove_keyword_deletes_row():
    conn = _FakeConn(table=[
        {"id": 1, "keyword": "AX 시장", "display_order": 0, "active": True, "created_at": None},
    ])
    assert remove_keyword(conn, "AX 시장") is True
    assert list_keywords(conn) == []


def test_remove_keyword_returns_false_for_unknown_keyword():
    conn = _FakeConn()
    assert remove_keyword(conn, "없는 키워드") is False


def test_set_search_terms_updates_both_languages():
    conn = _FakeConn(table=[
        {"id": 1, "keyword": "교육", "display_order": 0, "active": True, "created_at": None,
         "search_terms_ko": [], "search_terms_en": []},
    ])
    assert set_search_terms(conn, "교육", ["AI 교육", "에듀테크"], ["AI education", "edtech"]) is True

    row = list_keywords(conn)[0]
    assert row["search_terms_ko"] == ["AI 교육", "에듀테크"]
    assert row["search_terms_en"] == ["AI education", "edtech"]


def test_set_search_terms_returns_false_for_unknown_keyword():
    conn = _FakeConn()
    assert set_search_terms(conn, "없는 키워드", ["a"], ["b"]) is False


def test_set_sites_updates_site_domains():
    conn = _FakeConn(table=[
        {"id": 1, "keyword": "콘텐츠팀", "display_order": 0, "active": True, "created_at": None,
         "search_terms_ko": [], "search_terms_en": [], "site_domains": None},
    ])
    assert set_sites(conn, "콘텐츠팀", ["aitimes.com", "techcrunch.com"]) is True

    row = list_keywords(conn)[0]
    assert row["site_domains"] == ["aitimes.com", "techcrunch.com"]


def test_set_sites_empty_list_falls_back_to_none():
    """빈 목록은 NULL로 저장돼야 "전체 화이트리스트 사용"으로 폴백한다
    (collect_all의 `site_domains or SITE_DOMAINS` 참고)."""
    conn = _FakeConn(table=[
        {"id": 1, "keyword": "콘텐츠팀", "display_order": 0, "active": True, "created_at": None,
         "search_terms_ko": [], "search_terms_en": [], "site_domains": ["aitimes.com"]},
    ])
    assert set_sites(conn, "콘텐츠팀", []) is True

    assert list_keywords(conn)[0]["site_domains"] is None


def test_set_sites_returns_false_for_unknown_keyword():
    conn = _FakeConn()
    assert set_sites(conn, "없는 키워드", ["aitimes.com"]) is False
