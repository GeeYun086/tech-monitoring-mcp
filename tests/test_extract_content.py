from tech_monitoring.collectors import extract_content
from tech_monitoring.collectors.extract_content import _should_skip


class _FakeCursor:
    def __init__(self, select_result):
        self._select_result = select_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return self._select_result


class _FakeConn:
    """실제 DB 없이 backfill_content()의 흐름만 태우기 위한 최소 스텁."""

    def __init__(self, select_result):
        self._select_result = select_result

    def cursor(self):
        return _FakeCursor(self._select_result)

    def close(self):
        pass


def test_fetch_always_passes_an_explicit_timeout(monkeypatch):
    """실사용 중 발견(2026-08-11): trafilatura.fetch_url()의 다운로드
    타임아웃이 이 환경에서 실제로 안 걸려서, 응답 없는 서버 하나 때문에
    파이프라인 전체가 18시간 넘게 멈췄다(예외조차 안 떠서 try/except도
    무용지물). httpx로 직접 받아오면서 반드시 timeout을 명시해야 한다 —
    이 회귀 테스트는 그 타임아웃 인자가 실수로 빠지는 걸 막는다."""
    captured = {}

    def fake_get(url, headers=None, timeout=None, follow_redirects=None):
        captured["timeout"] = timeout
        raise Exception("네트워크 호출은 안 함 — 인자만 확인")

    monkeypatch.setattr(extract_content, "get_connection", lambda: _FakeConn([(1, "https://example.com/a")]))
    monkeypatch.setattr(extract_content.httpx, "get", fake_get)

    extract_content.backfill_content(batch_size=1)

    assert captured["timeout"] == extract_content.REQUEST_TIMEOUT_SECONDS
    assert captured["timeout"] is not None


def test_skips_techmeme_river_page_urls():
    """Techmeme URL은 하나의 날짜별 리버 페이지를 #fragment로 가리키는 헤드라인
    앵커다. trafilatura는 fragment를 못 보고 페이지에서 아무 블록이나 골라오는데,
    실측 결과 Techmeme 15건 전부가 제목과 무관한 본문으로 오염됐었다
    (예: "Nikita Bier 퇴사" 기사에 "Meta Muse Code 출시" 본문이 들어감).
    """
    assert _should_skip("https://www.techmeme.com/260805/p52#a260805p52")
    assert _should_skip("https://techmeme.com/260805/p52#a260805p52")


def test_does_not_skip_direct_article_urls():
    """HN 등 애그리게이터는 원본 기사의 실제 URL로 링크하므로(리버 페이지가
    아님) 본문 추출을 그대로 시도해야 한다."""
    assert not _should_skip("https://blog.cloudflare.com/cloudflare-os/")
    assert not _should_skip("https://www.theverge.com/tech/975677/some-article")


def test_delay_between_requests_is_randomized_within_configured_range(monkeypatch):
    """고정 0.5초 간격은 규칙적인 요청 패턴을 만들어 봇 탐지에 취약하다.
    REQUEST_DELAY_RANGE_SECONDS 범위에서 매번 무작위로 뽑아 쓰는지 확인한다."""
    sleeps = []

    def fake_get(url, headers=None, timeout=None, follow_redirects=None):
        raise Exception("네트워크 호출은 안 함 — 지연만 확인")

    monkeypatch.setattr(
        extract_content, "get_connection",
        lambda: _FakeConn([(1, "https://example.com/a"), (2, "https://example.com/b")]),
    )
    monkeypatch.setattr(extract_content.httpx, "get", fake_get)
    monkeypatch.setattr(extract_content.random, "uniform", lambda a, b: 0.42)
    monkeypatch.setattr(extract_content.time, "sleep", lambda s: sleeps.append(s))

    extract_content.backfill_content(batch_size=2)

    assert sleeps == [0.42, 0.42]
