from tech_monitoring.utils.url_normalize import normalize_url


def test_strips_utm_params():
    a = normalize_url("https://example.com/article?utm_source=rss&utm_medium=feed")
    b = normalize_url("https://example.com/article")
    assert a == b


def test_collapses_www_and_amp_subdomain():
    a = normalize_url("https://www.example.com/article")
    b = normalize_url("https://amp.example.com/article")
    assert a == b


def test_collapses_amp_path_suffix():
    a = normalize_url("https://example.com/article/amp")
    b = normalize_url("https://example.com/article")
    assert a == b


def test_strips_trailing_slash_and_fragment():
    a = normalize_url("https://example.com/article/#comments")
    b = normalize_url("https://example.com/article")
    assert a == b


def test_keeps_meaningful_query_params():
    a = normalize_url("https://example.com/article?id=123")
    b = normalize_url("https://example.com/article?id=456")
    assert a != b


def test_distinct_articles_stay_distinct():
    a = normalize_url("https://example.com/article-a")
    b = normalize_url("https://example.com/article-b")
    assert a != b
