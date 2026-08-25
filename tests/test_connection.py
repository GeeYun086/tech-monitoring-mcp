"""db/connection.py 테스트. 실제 DB 연결 없이 psycopg.connect 호출 인자만 검증한다."""

import psycopg
import pytest

from tech_monitoring.db import connection


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """재시도 테스트가 CONNECT_RETRY_DELAY_SECONDS만큼 실제로 기다리지 않게
    한다 — 재시도 로직 자체(몇 번 부르는지, 언제 포기하는지)만 확인하면
    된다."""
    monkeypatch.setattr(connection.time, "sleep", lambda _seconds: None)


def test_get_connection_passes_connect_timeout(monkeypatch):
    """DB가 응답 없을 때(docker 미기동, Supabase 일시정지 등) 무한 대기 대신
    빨리 실패하도록 connect_timeout이 항상 전달돼야 한다 — 실제로 Streamlit
    대시보드를 띄워보다가 타임아웃 없이 몇 분씩 매달리는 걸 발견하고 추가함."""
    captured = {}

    def fake_connect(conninfo, **kwargs):
        captured.update(kwargs)
        return "fake-conn"

    monkeypatch.setattr(connection.psycopg, "connect", fake_connect)

    result = connection.get_connection()

    assert result == "fake-conn"
    assert captured["connect_timeout"] == connection.CONNECT_TIMEOUT_SECONDS
    assert captured["autocommit"] is True


# ---- 연결 재시도(2026-08-24 추가) ----------------------------------------
# 계기: GitHub Actions 자동 수집이 "마이그레이션 적용" 단계에서 DB 연결
# 타임아웃으로 실패했다(Supabase 무료 티어 세션 풀러 동시 연결 15개 한도가
# 순간적으로 다 찼던 것으로 추정). 몇 초 뒤 재시도하면 대개 풀린다.

def test_retries_on_operational_error_and_eventually_succeeds(monkeypatch):
    """순간적인 풀 경합이면 몇 번 재시도하다가 풀려야 한다."""
    calls = []

    def flaky_connect(conninfo, **kwargs):
        calls.append(1)
        if len(calls) < connection.CONNECT_MAX_ATTEMPTS:
            raise psycopg.errors.ConnectionTimeout("connection timeout expired")
        return "fake-conn"

    monkeypatch.setattr(connection.psycopg, "connect", flaky_connect)

    assert connection.get_connection() == "fake-conn"
    assert len(calls) == connection.CONNECT_MAX_ATTEMPTS


def test_gives_up_after_max_attempts(monkeypatch):
    """진짜 장애(Supabase 일시정지 등)면 재시도로도 안 풀리므로, 무한정
    기다리지 않고 CONNECT_MAX_ATTEMPTS번 만에 포기하고 마지막 에러를 그대로
    올려야 한다 — 위 CONNECT_TIMEOUT_SECONDS의 "빠른 실패" 원칙과 같은 이유."""
    calls = []

    def always_fails(conninfo, **kwargs):
        calls.append(1)
        raise psycopg.errors.ConnectionTimeout(f"attempt {len(calls)}")

    monkeypatch.setattr(connection.psycopg, "connect", always_fails)

    with pytest.raises(psycopg.errors.ConnectionTimeout, match="attempt 3"):
        connection.get_connection()

    assert len(calls) == connection.CONNECT_MAX_ATTEMPTS


def test_does_not_retry_non_operational_errors(monkeypatch):
    """연결 실패가 아닌 다른 종류의 예외(설정 오류 등)는 재시도해도 안 풀리므로
    즉시 올린다 — OperationalError만 재시도 대상이다."""
    calls = []

    def bad_conninfo(conninfo, **kwargs):
        calls.append(1)
        raise psycopg.ProgrammingError("invalid connection string")

    monkeypatch.setattr(connection.psycopg, "connect", bad_conninfo)

    with pytest.raises(psycopg.ProgrammingError):
        connection.get_connection()

    assert len(calls) == 1
