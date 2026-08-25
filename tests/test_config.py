"""config.py 테스트.

**배경(2026-08-25)**: GitHub 저장소 시크릿에 DATABASE_URL을 등록할 때 값 끝에
개행이 하나 섞여 들어가서, "주간 수집" 마이그레이션 스텝이
`FATAL: database "postgres\\n" does not exist`로 실패했다 — Supabase는
멀쩡한데 시크릿 값에 섞인 눈에 안 보이는 개행 하나 때문에 매주 자동 수집이
통째로 실패하는, 원인을 알아채기 매우 어려운 오류였다. 값을 붙여넣을 때
흔한 실수라 코드에서 한 번 걸러준다.
"""

from tech_monitoring.config import Settings


def test_database_url_strips_a_trailing_newline():
    settings = Settings(database_url="postgresql://a:b@c:5432/postgres\n")

    assert settings.database_url == "postgresql://a:b@c:5432/postgres"


def test_database_url_strips_surrounding_whitespace():
    settings = Settings(database_url="  postgresql://a:b@c:5432/postgres  ")

    assert settings.database_url == "postgresql://a:b@c:5432/postgres"


def test_database_url_without_whitespace_is_unchanged():
    settings = Settings(database_url="postgresql://a:b@c:5432/postgres")

    assert settings.database_url == "postgresql://a:b@c:5432/postgres"
