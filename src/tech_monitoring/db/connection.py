import psycopg
from pgvector.psycopg import register_vector

from tech_monitoring.config import settings


def get_connection() -> psycopg.Connection:
    conn = psycopg.connect(settings.database_url, autocommit=True)
    register_vector(conn)
    return conn
