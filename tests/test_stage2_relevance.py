from tech_monitoring.filters.stage2_relevance import _build_tsquery


def test_build_tsquery_ors_keywords():
    query = _build_tsquery(["AX", "AI transformation"])
    assert query == "(AX) | (AI & transformation)"


def test_build_tsquery_skips_blank_keywords():
    query = _build_tsquery(["", "  ", "AX"])
    assert query == "(AX)"
