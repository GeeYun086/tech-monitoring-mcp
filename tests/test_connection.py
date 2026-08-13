"""db/connection.py 테스트. 실제 DB 연결 없이 psycopg.connect 호출 인자만 검증한다."""

from tech_monitoring.db import connection


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
