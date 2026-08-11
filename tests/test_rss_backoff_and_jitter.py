"""arXiv 429 백오프(_get_with_rate_limit_backoff)와 collect_all()의 소스 간
무작위 지연에 대한 회귀 테스트. 실제 네트워크·time.sleep은 전부 스텁으로 대체한다.
"""

from tech_monitoring.collectors import rss


class _FakeResponse:
    def __init__(self, status_code, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.text = content.decode() if isinstance(content, bytes) else content


def test_backoff_retries_on_429_then_succeeds(monkeypatch):
    """429가 두 번 온 뒤 세 번째에 성공하면(RATE_LIMIT_MAX_RETRIES=2) 그 응답을 반환해야 한다."""
    responses = [_FakeResponse(429), _FakeResponse(429), _FakeResponse(200)]
    sleeps = []

    def fake_get(url, headers=None, timeout=None, follow_redirects=None):
        return responses.pop(0)

    monkeypatch.setattr(rss.httpx, "get", fake_get)
    monkeypatch.setattr(rss.time, "sleep", lambda s: sleeps.append(s))

    resp = rss._get_with_rate_limit_backoff("https://export.arxiv.org/rss/cs.AI")

    assert resp.status_code == 200
    # 3초 → 6초로 지수 백오프 (RATE_LIMIT_BASE_DELAY_SECONDS=3.0 기준)
    assert sleeps == [3.0, 6.0]


def test_backoff_gives_up_after_max_retries(monkeypatch):
    """재시도를 다 써도 여전히 429면 마지막 429 응답을 그대로 반환한다(예외 아님)."""
    monkeypatch.setattr(rss.httpx, "get", lambda *a, **k: _FakeResponse(429))
    monkeypatch.setattr(rss.time, "sleep", lambda s: None)

    resp = rss._get_with_rate_limit_backoff("https://export.arxiv.org/rss/cs.AI")

    assert resp.status_code == 429


def test_backoff_does_not_retry_non_429_errors(monkeypatch):
    """403 같은 영구 차단은 즉시 반환해야 한다 — 재시도로 시간을 낭비하던
    문제(72267e0)가 이 변경으로 재발하면 안 된다."""
    calls = []

    def fake_get(url, headers=None, timeout=None, follow_redirects=None):
        calls.append(url)
        return _FakeResponse(403)

    monkeypatch.setattr(rss.httpx, "get", fake_get)
    monkeypatch.setattr(rss.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("sleep 호출됨")))

    resp = rss._get_with_rate_limit_backoff("https://openai.com/news/rss.xml")

    assert resp.status_code == 403
    assert len(calls) == 1


def test_backoff_honors_retry_after_header(monkeypatch):
    """서버가 Retry-After를 주면 고정 백오프 대신 그 값을 써야 한다."""
    responses = [_FakeResponse(429, headers={"Retry-After": "5"}), _FakeResponse(200)]
    sleeps = []

    monkeypatch.setattr(rss.httpx, "get", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(rss.time, "sleep", lambda s: sleeps.append(s))

    rss._get_with_rate_limit_backoff("https://export.arxiv.org/rss/cs.AI")

    assert sleeps == [5.0]


def test_collect_source_reports_rate_limited_after_exhausting_retries(monkeypatch):
    """collect_all()이 보는 최종 에러 메시지는 재시도를 다 썼다는 걸 구분할 수 있어야 한다."""
    monkeypatch.setattr(rss.httpx, "get", lambda *a, **k: _FakeResponse(429))
    monkeypatch.setattr(rss.time, "sleep", lambda s: None)

    source = {
        "id": 1, "name": "arXiv cs.AI", "source_type": "api",
        "feed_url": "https://export.arxiv.org/rss/cs.AI",
        "source_trust": 0.8, "last_collected_at": None,
    }

    result = rss.collect_source(conn=None, source=source)

    assert result["error"] == "rate limited (HTTP 429, retries exhausted)"
    assert result["fetched"] == 0 and result["inserted"] == 0


def test_collect_all_sleeps_between_sources_but_not_after_the_last(monkeypatch):
    """소스 N개면 지연은 N-1번만 발생해야 한다(마지막 뒤에 기다릴 이유 없음)."""

    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows
            self.description = [
                type("Col", (), {"name": n})()
                for n in ("id", "name", "source_type", "feed_url", "source_trust", "last_collected_at")
            ]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return self._rows

    class _FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def cursor(self):
            return _FakeCursor(self._rows)

        def close(self):
            pass

    rows = [
        (1, "Source A", "rss", "https://a.example.com/feed", 0.7, None),
        (2, "Source B", "rss", "https://b.example.com/feed", 0.7, None),
        (3, "Source C", "rss", "https://c.example.com/feed", 0.7, None),
    ]
    sleeps = []

    monkeypatch.setattr(rss, "get_connection", lambda: _FakeConn(rows))
    monkeypatch.setattr(
        rss, "collect_source",
        lambda conn, source: {"source": source["name"], "fetched": 0, "inserted": 0, "error": None},
    )
    monkeypatch.setattr(rss.random, "uniform", lambda a, b: 1.5)
    monkeypatch.setattr(rss.time, "sleep", lambda s: sleeps.append(s))

    rss.collect_all()

    assert sleeps == [1.5, 1.5]
