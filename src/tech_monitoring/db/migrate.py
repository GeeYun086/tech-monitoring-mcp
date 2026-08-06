from pathlib import Path

import psycopg

from tech_monitoring.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"


def run_migrations() -> None:
    # 마이그레이션 자체가 vector 익스텐션을 생성하므로, 여기서는
    # pgvector 타입 등록(register_vector) 없이 순수 연결만 사용한다.
    conn = psycopg.connect(settings.database_url, autocommit=True)
    with conn.cursor() as cur:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            print(f"applying {path.name}")
            cur.execute(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    run_migrations()
    print("migrations applied")
