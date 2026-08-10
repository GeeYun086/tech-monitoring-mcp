from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://tm_user:tm_pass@localhost:5432/tech_monitoring"
    naver_client_id: str | None = None
    naver_client_secret: str | None = None

    # 필터 튜닝 파라미터 — AX 실데이터 라벨링으로 정밀도 측정 후 조정(설계서 v2.0 §11)
    relevance_cosine_threshold: float = 0.35  # Stage2 τ (AX 시장 관련도)
    # Stage5 이슈 클러스터링 임계값. 2026-08-10: 라벨 없이 0.60~0.85 구간을
    # 실측한 결과, 0.85는 너무 보수적이라 사실상 병합이 거의 안 됐다("여러
    # 매체 동시보도" 신호가 죽음). 그렇다고 그냥 낮추면 서로 다른 실적발표가
    # 정형화된 문장 구조 때문에 잘못 묶였다(예: DoorDash·Duolingo가 "X reports
    # Q2 revenue up N% YoY..."로 겹침). stage5_cluster.py에 제목의 고유명사가
    # 실제로 겹치는지 보는 추가 게이트를 넣어서(라벨 불필요 — 객관적 신호),
    # 0.70까지 안전하게 낮췄다. 남은 한계: Stratechery류 "X Earnings, Y's Z,
    # ..." 식 다중 클로즈 제목은 대문자 비율이 너무 높아 이 게이트 신뢰도
    # 판정에서 제외되고 코사인만으로 판단되므로, 이 소스 하나에 한해 가끔
    # 서로 다른 이슈가 묶이는 경우가 남아있다(실측: 최대 7건, 이전의 53건
    # 폭주에 비하면 훨씬 완화된 수준).
    cluster_similarity_threshold: float = 0.70  # Stage5 이슈 클러스터링 임계값
    # 최신성 감쇠 반감기. 2026-08-10: 다이제스트 스케일(1주)에 맞춰 72h→168h로
    # 완화했다. 단, 이것만으로는 "주간 다이제스트가 마지막 하루로 쏠리는" 문제가
    # 안 풀렸다 — aggregator_signal(속도 기반)이 나이에 훨씬 더 민감하게 떨어져서
    # recency를 느긋하게 해도 눌린다. 그 문제의 실제 해결책은
    # mcp_server/queries.py의 diversify_by_day()(날짜별 라운드로빈)다. 이 값은
    # "다이제스트 스케일에 맞춘 상식적인 기본값" 정도로 남겨둔다.
    recency_half_life_hours: float = 168  # 7일

    # 파급력 가중치 — 설계서 v2.0 §5: 큐레이션 소스 중심 + 규칙 신호 + 최신성.
    # 감성 분석·회사관점 LLM 중요도 판정은 미도입(주관 중요도는 사용자 판단 영역).
    weight_source_trust: float = 0.35
    weight_aggregator_signal: float = 0.25
    weight_cluster_size: float = 0.20
    weight_recency: float = 0.20

    # HN points가 이 값 이상이면 애그리게이터 반향 신호를 1.0으로 포화.
    # published_at을 몰라 속도 계산이 불가능할 때만 폴백으로 쓰인다(아래 참고).
    hn_points_saturation: float = 500

    # aggregator_signal 속도 정규화 — 2026-08-10 실사용 중 발견: 누적 포인트를
    # 그대로 쓰면(점수/500) 반응이 쌓일 시간이 없었던 신생 기사가 구조적으로
    # 불리했다. 예: 30분 전 15점(막 뜨는 중) vs 72시간 전 400점(이미 다 모임) —
    # 후자가 항상 이겼다. "누적량"이 아니라 "시간당 속도"로 보면 이 편향이 준다.
    # offset은 나이가 0에 가까울 때 속도가 무한대로 튀는 걸 막는 버퍼(시간),
    # saturation은 "이 정도 속도면 충분히 빠르다"는 상한(포인트/시간).
    aggregator_velocity_offset_hours: float = 2.0
    aggregator_velocity_saturation: float = 20.0

    # 소스 최초 수집 시 가져올 최대 소급 기간(일).
    # 일부 피드(OpenAI 등)는 전체 아카이브를 한 번에 내려주는데, 수년치 과거 글이
    # 후보 풀을 점령해 다른 소스를 밀어내므로 최초 적재 범위를 제한한다.
    # 2회차부터는 last_collected_at 기준 증분 수집이라 이 값과 무관하다.
    initial_backfill_days: int = 30

    # BGE-M3는 로컬에 캐시돼 있어도 sentence-transformers가 기본적으로 매 프로세스
    # 첫 로딩 시 HF Hub로 버전 확인 네트워크 요청을 보낸다. 이 요청에 타임아웃이
    # 없어 네트워크 상태에 따라 응답이 몇 분씩 걸리거나 멈출 수 있고, 실제로 MCP
    # stdio 호출이 이 때문에 타임아웃났다(검색어 있는 search_news 첫 호출 500s+).
    # 모델은 이미 로컬에 있으므로 오프라인 모드를 기본값으로 강제한다.
    # 모델을 새로 받거나 갱신해야 하면 .env에서 false로 잠깐 풀어 받은 뒤 되돌린다.
    hf_hub_offline: bool = True


settings = Settings()
