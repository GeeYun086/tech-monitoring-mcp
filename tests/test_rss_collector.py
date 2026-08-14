"""collectors/rss_collector.py 테스트. 실제 네트워크·DB 없이 fetch_rss와
conn.cursor()를 스텁으로 대체한다. XML 샘플은 2026-08-13 실제 응답 구조를
최소 재현한 것(모듈 docstring 참고)."""

from datetime import datetime, timezone

import httpx

from tech_monitoring.collectors import rss_collector


TECHCRUNCH_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>TechCrunch</title>
<item>
    <title>Some Claude users are mad that Anthropic&#8217;s new watermarks will catch them</title>
    <link>https://techcrunch.com/2026/08/12/some-story/</link>
    <pubDate>Wed, 12 Aug 2026 22:26:37 +0000</pubDate>
    <description><![CDATA[Is Anthropic's new watermarking system a travesty?]]></description>
</item>
<item>
    <title>기사 하나 더</title>
    <link>https://techcrunch.com/2026/08/12/another-story/</link>
    <pubDate>Wed, 12 Aug 2026 20:00:00 +0000</pubDate>
    <description>설명 없음</description>
</item>
</channel></rss>
"""

TECHMEME_SAMPLE_WITH_HTML_DESCRIPTION = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Techmeme</title>
<item>
  <title>Over 30 crypto companies say AI safety guardrails hinder security work</title>
  <link>https://www.techmeme.com/260813/p10#a260813p10</link>
  <description><![CDATA[<A HREF="x"><IMG SRC="y"></A><P>More than three dozen crypto companies asked AI labs for tools.</P>]]></description>
  <pubDate>Thu, 13 Aug 2026 02:30:02 -0400</pubDate>
</item>
</channel></rss>
"""

ITEM_MISSING_LINK_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>링크 없는 항목</title><pubDate>Thu, 13 Aug 2026 00:00:00 GMT</pubDate></item>
</channel></rss>
"""


# ---- parse_rss_items ----

def test_parse_rss_items_extracts_title_url_snippet_and_pubdate():
    items = rss_collector.parse_rss_items(TECHCRUNCH_SAMPLE)

    assert len(items) == 2
    assert items[0]["url"] == "https://techcrunch.com/2026/08/12/some-story/"
    assert "Anthropic" in items[0]["title"]
    assert items[0]["snippet"] == "Is Anthropic's new watermarking system a travesty?"
    assert items[0]["published_at"] == datetime(2026, 8, 12, 22, 26, 37, tzinfo=timezone.utc)


def test_parse_rss_items_strips_html_from_description():
    """Techmeme의 description엔 썸네일 이미지·서식 태그가 섞여 온다(실측) — 순수 텍스트만 남아야 한다."""
    items = rss_collector.parse_rss_items(TECHMEME_SAMPLE_WITH_HTML_DESCRIPTION)

    assert len(items) == 1
    assert "<" not in items[0]["snippet"]
    assert "More than three dozen crypto companies" in items[0]["snippet"]


def test_parse_rss_items_skips_items_without_link():
    assert rss_collector.parse_rss_items(ITEM_MISSING_LINK_SAMPLE) == []


def test_parse_rss_items_returns_empty_list_for_empty_channel():
    assert rss_collector.parse_rss_items("<rss version='2.0'><channel></channel></rss>") == []


# ---- collect_source / collect_all ----

class _FakeCursor:
    def __init__(self, inserted_urls: set[str]):
        self._inserted_urls = inserted_urls
        self._last_url = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        assert "INSERT INTO collected_articles" in query
        self._last_url = params[3]  # (run_id, source_name, title, url, ...)

    def fetchone(self):
        if self._last_url in self._inserted_urls:
            return None
        self._inserted_urls.add(self._last_url)
        return (1,)


class _FakeConn:
    def __init__(self):
        self.inserted_urls: set[str] = set()

    def cursor(self):
        return _FakeCursor(self.inserted_urls)


def _item(url: str) -> dict:
    return {"title": "제목", "url": url, "snippet": "요약", "published_at": None}


def test_collect_source_reports_fetched_and_inserted_counts(monkeypatch):
    monkeypatch.setattr(rss_collector, "fetch_rss", lambda feed_url: [
        _item("https://techcrunch.com/a/"), _item("https://techcrunch.com/b/"),
    ])

    result = rss_collector.collect_source(_FakeConn(), run_id=1, source={"name": "TechCrunch", "feed_url": "x"})

    assert result == {"source": "TechCrunch", "fetched": 2, "inserted": 2, "error": None}


def test_collect_source_returns_error_on_http_failure(monkeypatch):
    def fake_fetch(feed_url):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(rss_collector, "fetch_rss", fake_fetch)

    result = rss_collector.collect_source(_FakeConn(), run_id=1, source={"name": "Techmeme", "feed_url": "x"})

    assert result == {"source": "Techmeme", "fetched": 0, "inserted": 0, "error": "boom"}


def test_collect_all_queries_every_rss_source(monkeypatch):
    calls = []

    def fake_collect_source(conn, run_id, source):
        calls.append(source["name"])
        return {"source": source["name"], "fetched": 0, "inserted": 0, "error": None}

    monkeypatch.setattr(rss_collector, "collect_source", fake_collect_source)

    rss_collector.collect_all(_FakeConn(), run_id=1)

    assert calls == [s["name"] for s in rss_collector.RSS_SOURCES]


def test_collect_source_dedups_same_article_on_rerun(monkeypatch):
    """UNIQUE(run_id, url) ON CONFLICT DO NOTHING — 같은 주에 재실행해도 중복 삽입 안 됨."""
    monkeypatch.setattr(rss_collector, "fetch_rss", lambda feed_url: [_item("https://techcrunch.com/a/")])
    conn = _FakeConn()

    first = rss_collector.collect_source(conn, run_id=1, source={"name": "TechCrunch", "feed_url": "x"})
    second = rss_collector.collect_source(conn, run_id=1, source={"name": "TechCrunch", "feed_url": "x"})

    assert first["inserted"] == 1
    assert second["inserted"] == 0
