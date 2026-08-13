from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://tm_user:tm_pass@localhost:5432/tech_monitoring"

    # Tavily Search API(2026-08-13 도입 — Google Custom Search JSON API가
    # 신규 계정에 폐쇄된 것을 확인한 뒤 교체, collectors/search_engine.py
    # 모듈 docstring 참고). 화이트리스트 사이트 목록은 코드
    # (collectors/search_engine.py의 SITE_INCLUDE_PATTERNS 등)로 직접
    # 관리해 별도 UI 설정이 필요 없다.
    tavily_api_key: str | None = None
    # Tavily time_range 파라미터: day/week/month/year. "week" = 지난 1주일.
    tavily_time_range: str = "week"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"  # 무료 티어


settings = Settings()
