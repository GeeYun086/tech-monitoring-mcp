import psycopg

from tech_monitoring.config import settings

# 2026-08-13: Streamlit 대시보드를 실제로 띄워보다가 발견 — DB가 응답 없으면
# (docker-compose 안 띄웠거나, Supabase 무료 티어가 7일 비활성 후 자동
# 일시정지된 경우 등) psycopg.connect()가 기본값으로는 몇 분씩 매달려서
# 화면이 "Running _conn()."에서 멈춘 채 아무 메시지도 없이 무한 로딩처럼
# 보였다. connect_timeout으로 실패를 빨리 드러내 상위(app/streamlit_app.py)가
# 친절한 에러 메시지를 보여줄 수 있게 한다.
CONNECT_TIMEOUT_SECONDS = 10


def get_connection() -> psycopg.Connection:
    # v2(검색엔진+Gemini 동의어 병합 기반, 2026-08-13 피벗)부터는 임베딩을 쓰지
    # 않아 pgvector 익스텐션이 필요 없다. register_vector(conn)는 v1 전용이었고
    # (pgvector 미설치 Supabase DB에서는 타입 조회 자체가 실패한다), v2 스키마엔
    # vector 컬럼이 없으므로 제거했다.
    return psycopg.connect(
        settings.database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )
