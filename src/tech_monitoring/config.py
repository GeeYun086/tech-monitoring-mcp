from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://tm_user:tm_pass@localhost:5432/tech_monitoring"
    naver_client_id: str | None = None
    naver_client_secret: str | None = None

    # 필터 튜닝 파라미터 — AX 실데이터 라벨링으로 정밀도 측정 후 조정(설계서 v2.0 §11)
    relevance_cosine_threshold: float = 0.35  # Stage2 τ (AX 시장 관련도)
    cluster_similarity_threshold: float = 0.85  # Stage5 이슈 클러스터링 임계값
    recency_half_life_hours: float = 72  # 최신성 감쇠 반감기

    # 파급력 가중치 — 설계서 v2.0 §5: 큐레이션 소스 중심 + 규칙 신호 + 최신성.
    # 감성 분석·회사관점 LLM 중요도 판정은 미도입(주관 중요도는 사용자 판단 영역).
    weight_source_trust: float = 0.35
    weight_aggregator_signal: float = 0.25
    weight_cluster_size: float = 0.20
    weight_recency: float = 0.20

    # HN points가 이 값 이상이면 애그리게이터 반향 신호를 1.0으로 포화
    hn_points_saturation: float = 500

    # 소스 최초 수집 시 가져올 최대 소급 기간(일).
    # 일부 피드(OpenAI 등)는 전체 아카이브를 한 번에 내려주는데, 수년치 과거 글이
    # 후보 풀을 점령해 다른 소스를 밀어내므로 최초 적재 범위를 제한한다.
    # 2회차부터는 last_collected_at 기준 증분 수집이라 이 값과 무관하다.
    initial_backfill_days: int = 30


settings = Settings()
