"""analysis/keyword_merge.py 테스트. 실제 Gemini 호출·DB 없이 call_gemini와
fetch_search_results/extract_candidates/build_term_sets를 monkeypatch로 대체한다."""

import json

from tech_monitoring.analysis import keyword_merge as km


# ---- parse_and_validate_groups ----

def test_parse_and_validate_groups_happy_path():
    raw = json.dumps({"groups": [
        {"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI", "오픈AI", "오픈 ai"]},
        {"canonical_phrase": "GPT-5", "variant_phrases": ["GPT-5"]},
    ]})
    valid = {"OpenAI", "오픈AI", "오픈 ai", "GPT-5"}
    assert km.parse_and_validate_groups(raw, valid) == [
        {"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI", "오픈AI", "오픈 ai"]},
        {"canonical_phrase": "GPT-5", "variant_phrases": ["GPT-5"]},
    ]


def test_parse_and_validate_groups_returns_empty_on_invalid_json():
    assert km.parse_and_validate_groups("이건 JSON이 아님", {"a"}) == []


def test_parse_and_validate_groups_drops_hallucinated_phrases():
    """원본 후보 목록에 없는 phrase(환각)는 걸러져야 한다."""
    raw = json.dumps({"groups": [
        {"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI", "가짜표기"]},
    ]})
    groups = km.parse_and_validate_groups(raw, valid_phrases={"OpenAI"})
    assert groups == [{"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"]}]


def test_parse_and_validate_groups_drops_group_with_no_valid_variants():
    raw = json.dumps({"groups": [{"canonical_phrase": "가짜", "variant_phrases": ["가짜"]}]})
    assert km.parse_and_validate_groups(raw, valid_phrases={"OpenAI"}) == []


def test_parse_and_validate_groups_first_group_wins_on_duplicate_assignment():
    """Gemini가 실수로 같은 phrase를 두 그룹에 넣으면 먼저 나온 그룹이 가져간다."""
    raw = json.dumps({"groups": [
        {"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"]},
        {"canonical_phrase": "다른그룹", "variant_phrases": ["OpenAI", "다른그룹"]},
    ]})
    groups = km.parse_and_validate_groups(raw, valid_phrases={"OpenAI", "다른그룹"})
    assert groups == [
        {"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"]},
        {"canonical_phrase": "다른그룹", "variant_phrases": ["다른그룹"]},
    ]


def test_parse_and_validate_groups_replaces_invalid_canonical_with_valid_variant():
    """canonical_phrase 자체가 원본에 없으면(환각) 유효한 변형 중 하나로 대체해야 한다."""
    raw = json.dumps({"groups": [
        {"canonical_phrase": "Gemini가 지어낸 이름", "variant_phrases": ["OpenAI", "오픈AI"]},
    ]})
    groups = km.parse_and_validate_groups(raw, valid_phrases={"OpenAI", "오픈AI"})
    assert groups[0]["canonical_phrase"] in {"OpenAI", "오픈AI"}
    assert groups[0]["variant_phrases"] == ["OpenAI", "오픈AI"]


# ---- add_ungrouped_singletons ----

def test_add_ungrouped_singletons_adds_missing_phrases():
    groups = [{"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"]}]
    result = km.add_ungrouped_singletons(groups, ["OpenAI", "Anthropic"])
    assert {"canonical_phrase": "Anthropic", "variant_phrases": ["Anthropic"]} in result
    assert len(result) == 2


def test_add_ungrouped_singletons_does_not_duplicate_existing():
    groups = [{"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"]}]
    result = km.add_ungrouped_singletons(groups, ["OpenAI"])
    assert len(result) == 1


# ---- compute_merged_stats ----

def test_compute_merged_stats_counts_union_not_sum():
    """한 문서에 여러 변형이 동시에 나와도 doc_count는 그 문서를 1건으로만 세야 한다."""
    group = {"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI", "오픈AI"]}
    term_sets = [
        {"OpenAI", "오픈AI", "model"},  # 둘 다 있는 문서 — 1건으로만 카운트
        {"오픈AI"},
        {"unrelated"},
    ]
    candidates_by_phrase = {"OpenAI": {"tfidf_score": 2.5}, "오픈AI": {"tfidf_score": 1.0}}

    stats = km.compute_merged_stats(group, term_sets, candidates_by_phrase)

    assert stats["doc_count"] == 2
    assert stats["tfidf_score"] == 2.5  # 변형들 중 최댓값
    assert stats["variant_phrases"] == ["OpenAI", "오픈AI"]


def test_compute_merged_stats_tfidf_score_none_when_all_variants_frequency_based():
    group = {"canonical_phrase": "삼성전자", "variant_phrases": ["삼성전자"]}
    stats = km.compute_merged_stats(group, [{"삼성전자"}], {"삼성전자": {"tfidf_score": None}})
    assert stats["tfidf_score"] is None


# ---- merge_candidates_for_keyword (오케스트레이션) ----

def test_merge_candidates_for_keyword_returns_empty_when_no_candidates(monkeypatch):
    monkeypatch.setattr(km, "fetch_search_results", lambda conn, run_id, kw_id: [])
    monkeypatch.setattr(km, "extract_candidates", lambda rows: [])
    result = km.merge_candidates_for_keyword(conn=None, run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"})
    assert result == []


def test_merge_candidates_for_keyword_groups_and_fills_singletons(monkeypatch):
    fake_candidates = [
        {"phrase": "OpenAI", "doc_count": 2, "tfidf_score": 2.5, "method": "tfidf"},
        {"phrase": "오픈AI", "doc_count": 1, "tfidf_score": 1.0, "method": "tfidf"},
        {"phrase": "Anthropic", "doc_count": 1, "tfidf_score": 0.9, "method": "tfidf"},
    ]
    fake_term_sets = [{"OpenAI", "오픈AI"}, {"Anthropic"}]

    monkeypatch.setattr(km, "fetch_search_results", lambda conn, run_id, kw_id: [{"title": "dummy"}])
    monkeypatch.setattr(km, "extract_candidates", lambda rows: fake_candidates)
    monkeypatch.setattr(km, "build_term_sets", lambda rows: fake_term_sets)
    monkeypatch.setattr(km, "call_gemini", lambda prompt: json.dumps({
        "groups": [{"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI", "오픈AI"]}]
    }))

    result = km.merge_candidates_for_keyword(conn=None, run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"})
    by_canonical = {r["canonical_phrase"]: r for r in result}

    assert by_canonical["OpenAI"]["doc_count"] == 1  # 문서 1건에 둘 다 있었음(합집합)
    assert by_canonical["OpenAI"]["variant_phrases"] == ["OpenAI", "오픈AI"]
    assert "Anthropic" in by_canonical  # 그룹에 안 들어간 후보도 단독 그룹으로 남아야 함
    assert by_canonical["Anthropic"]["variant_phrases"] == ["Anthropic"]


def test_merge_candidates_for_keyword_falls_back_to_singletons_on_gemini_failure(monkeypatch):
    """Gemini 응답이 파싱 불가능해도(장애·이상 응답) 파이프라인이 죽지 않고
    전부 단독 그룹으로 폴백해야 한다."""
    fake_candidates = [{"phrase": "OpenAI", "doc_count": 1, "tfidf_score": 1.0, "method": "tfidf"}]

    monkeypatch.setattr(km, "fetch_search_results", lambda conn, run_id, kw_id: [{"title": "dummy"}])
    monkeypatch.setattr(km, "extract_candidates", lambda rows: fake_candidates)
    monkeypatch.setattr(km, "build_term_sets", lambda rows: [{"OpenAI"}])
    monkeypatch.setattr(km, "call_gemini", lambda prompt: "이상한 응답 (JSON 아님)")

    result = km.merge_candidates_for_keyword(conn=None, run_id=1, fixed_keyword={"id": 1, "keyword": "AX 시장"})

    assert result == [
        {"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"], "doc_count": 1, "tfidf_score": 1.0},
    ]


# ---- _save_market_keywords / run_for_all_keywords (DB 저장 경로) ----

class _FakeCursor:
    def __init__(self, inserted_canonicals: set[str]):
        self._inserted = inserted_canonicals
        self._last_canonical = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        assert "INSERT INTO market_keywords" in query
        self._last_canonical = params[2]  # (run_id, fixed_keyword_id, canonical_phrase, ...)

    def fetchone(self):
        if self._last_canonical in self._inserted:
            return None  # ON CONFLICT DO NOTHING
        self._inserted.add(self._last_canonical)
        return (1,)


class _FakeConn:
    def __init__(self):
        self.inserted: set[str] = set()

    def cursor(self):
        return _FakeCursor(self.inserted)


def test_save_market_keywords_inserts_all_new_rows():
    conn = _FakeConn()
    merged = [
        {"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"], "doc_count": 2, "tfidf_score": 1.5},
        {"canonical_phrase": "Anthropic", "variant_phrases": ["Anthropic"], "doc_count": 1, "tfidf_score": None},
    ]
    inserted = km._save_market_keywords(conn, run_id=1, fixed_keyword_id=1, merged=merged)
    assert inserted == 2


def test_save_market_keywords_skips_duplicate_canonical_phrase():
    """UNIQUE(run_id, fixed_keyword_id, canonical_phrase) — 재실행해도 중복 삽입 안 됨."""
    conn = _FakeConn()
    merged = [{"canonical_phrase": "OpenAI", "variant_phrases": ["OpenAI"], "doc_count": 1, "tfidf_score": None}]
    km._save_market_keywords(conn, run_id=1, fixed_keyword_id=1, merged=merged)
    second_pass = km._save_market_keywords(conn, run_id=1, fixed_keyword_id=1, merged=merged)
    assert second_pass == 0


def test_run_for_all_keywords_covers_every_active_keyword(monkeypatch):
    monkeypatch.setattr(km, "get_active_fixed_keywords", lambda conn: [
        {"id": 1, "keyword": "AX 시장"}, {"id": 2, "keyword": "생성형 AI"},
    ])
    monkeypatch.setattr(km, "merge_candidates_for_keyword", lambda conn, run_id, kw: [
        {"canonical_phrase": f"kw-{kw['id']}", "variant_phrases": [f"kw-{kw['id']}"], "doc_count": 1, "tfidf_score": None},
    ])
    monkeypatch.setattr(km, "_save_market_keywords", lambda conn, run_id, fixed_keyword_id, merged: len(merged))

    results = km.run_for_all_keywords(conn=None, run_id=1)

    assert [r["fixed_keyword"] for r in results] == ["AX 시장", "생성형 AI"]
    assert all(r["groups"] == 1 and r["inserted"] == 1 for r in results)
