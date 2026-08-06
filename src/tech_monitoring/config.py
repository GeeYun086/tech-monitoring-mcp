from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://tm_user:tm_pass@localhost:5432/tech_monitoring"
    naver_client_id: str | None = None
    naver_client_secret: str | None = None

    # 필터 튜닝 파라미터 — 지금은 PoC 플레이스홀더, 담당자 기준·라벨링 튜닝 후 .env로 조정 [확인 필요]
    relevance_cosine_threshold: float = 0.35  # Stage2 τ
    cluster_similarity_threshold: float = 0.85  # Stage5 이슈 클러스터링 임계값
    recency_half_life_hours: float = 72  # Stage3 최신성 감쇠 반감기

    weight_source_trust: float = 0.25
    weight_aggregator_signal: float = 0.15
    weight_cluster_size: float = 0.15
    weight_recency: float = 0.15
    weight_issue_type: float = 0.15
    weight_sentiment: float = 0.15


settings = Settings()
