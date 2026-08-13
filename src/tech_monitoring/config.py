from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://tm_user:tm_pass@localhost:5432/tech_monitoring"

    # 사용자가 직접 구성한 Custom Search Engine(cx) — 화이트리스트 사이트만
    # 검색되므로 관련도 필터링이 따로 필요 없다(아래 google_search_cx가 그 cx).
    google_search_api_key: str | None = None
    google_search_cx: str | None = None
    # Custom Search API 공식 파라미터(비공식 스크래핑 아님). "w1" = 지난 1주일.
    google_search_date_restrict: str = "w1"
    # 고정 키워드 하나당 목표 수집 건수. top20처럼 인위적으로 자르지 않고
    # 넓게 모아서 이후 단계(TF-IDF+Gemini 동의어 병합)가 주요 키워드를 골라내게 한다.
    search_results_per_keyword: int = 50
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"  # 무료 티어


settings = Settings()
