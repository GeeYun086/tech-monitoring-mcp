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
    # "gemini-2.0-flash" 고정(404) → "gemini-flash-latest" 별칭(2026-08-13)을
    # 거쳐, 같은 날 실측으로 "gemini-3.5-flash"에 재고정했다. 별칭 대신
    # 명시적 모델명을 쓰는 이유: alias가 실제로 어떤 모델을 가리키는지
    # API로 확인할 방법이 없어(models.get()도 display_name/description만
    # 반환), 나중에 유료 전용 모델로 넘어가도 알아챌 수 없다. 실측(2026-08-13,
    # 이 계정 기준) 결과:
    #   - gemini-2.5-flash/2.5-flash-lite: 404 "no longer available to new
    #     users" — 최근 생성 계정은 2.5 계열 자체에 접근 불가(구조가 Google
    #     Custom Search 신규계정 폐쇄와 동일한 패턴).
    #   - gemini-3.1-flash-lite/3-flash-preview/3.5-flash: 429(계정 결제
    #     동기화 버그 — 모델 접근 권한 자체는 있다는 신호, 404가 아니므로).
    # 3.5-flash를 택한 이유: 공식 가격 페이지에서 무료 확인, "-preview"
    # 접미사가 없어 3-flash-preview보다 GA 전환 시 이름이 안 바뀔 가능성이
    # 높다(preview 모델은 정식 출시되며 모델 ID가 바뀌어 또 깨질 수 있음).
    gemini_model: str = "gemini-3.5-flash"  # 무료 티어(2026-08-13 실측 기준)


settings = Settings()
