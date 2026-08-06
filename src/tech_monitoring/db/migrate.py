from pathlib import Path

import psycopg

from tech_monitoring.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"


def run_migrations() -> None:
    # 마이그레이션 자체가 vector 익스텐션을 생성하므로, 여기서는
    # pgvector 타입 등록(register_vector) 없이 순수 연결만 사용한다.
    conn = psycopg.connect(settings.database_url, autocommit=True)
    with conn.cursor() as cur:
        # 적용 이력을 남겨 같은 마이그레이션을 두 번 실행하지 않는다.
        # (RENAME COLUMN처럼 재실행이 불가능한 구문이 있으므로 필수)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute("SELECT filename FROM schema_migrations")
        applied = {row[0] for row in cur.fetchall()}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                print(f"skipping {path.name} (already applied)")
                continue
            print(f"applying {path.name}")
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))


if __name__ == "__main__":
    run_migrations()
    print("migrations applied")
