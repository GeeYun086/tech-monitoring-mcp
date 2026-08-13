import psycopg

from tech_monitoring.config import settings


def get_connection() -> psycopg.Connection:
    # v2(검색엔진+Gemini 동의어 병합 기반, 2026-08-13 피벗)부터는 임베딩을 쓰지
    # 않아 pgvector 익스텐션이 필요 없다. register_vector(conn)는 v1 전용이었고
    # (pgvector 미설치 Supabase DB에서는 타입 조회 자체가 실패한다), v2 스키마엔
    # vector 컬럼이 없으므로 제거했다.
    return psycopg.connect(settings.database_url, autocommit=True)
