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
    # "gemini-2.0-flash"로 고정해뒀다가 2026-08-13 실전 파이프라인 실행 중
    # 404("model ... is no longer available")로 발견 — 버전 고정 모델은
    # Google이 예고 없이 은퇴시킬 수 있다. "-latest" 별칭은 Google이 항상
    # 현재 권장 flash 모델로 갱신해주므로 같은 문제가 재발하지 않는다.
    gemini_model: str = "gemini-flash-latest"  # 무료 티어


settings = Settings()
