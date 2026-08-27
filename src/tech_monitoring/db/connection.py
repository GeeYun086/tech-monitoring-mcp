import time

import psycopg

from tech_monitoring.config import settings

# 2026-08-13: Streamlit 대시보드를 실제로 띄워보다가 발견 — DB가 응답 없으면
# (docker-compose 안 띄웠거나, Supabase 무료 티어가 7일 비활성 후 자동
# 일시정지된 경우 등) psycopg.connect()가 기본값으로는 몇 분씩 매달려서
# 화면이 "Running _conn()."에서 멈춘 채 아무 메시지도 없이 무한 로딩처럼
# 보였다. connect_timeout으로 실패를 빨리 드러내 상위(app/streamlit_app.py)가
# 친절한 에러 메시지를 보여줄 수 있게 한다.
CONNECT_TIMEOUT_SECONDS = 10

# 재시도 횟수·간격(2026-08-24 추가). 계기: 그 주 GitHub Actions 자동 수집이
# "마이그레이션 적용" 단계에서 딱 이 연결 타임아웃으로 실패했다(run 32682904454).
# 원인은 Supabase 무료 티어(Nano 컴퓨트)의 세션 풀러 동시 연결 한도가 15개뿐이라 —
# 그 순간 대시보드·MCP 서버·로컬 스크립트가 겹쳐 슬롯을 다 쓰고 있으면 새 연결이
# 타임아웃난다. 몇 초 뒤 재시도하면 그 사이 슬롯이 반납돼 대개 풀린다.
#
# 재시도를 3번·3초 간격으로 짧게 유지하는 이유: 위 CONNECT_TIMEOUT_SECONDS
# 주석의 "빠른 실패" 원칙과 충돌하면 안 된다 — Supabase가 진짜로 일시정지된
# 경우(재시도로 안 풀리는 상황)까지 대시보드가 몇 분씩 먹통으로 보이면 안
# 되므로, 최악의 경우에도 "10초 연결 타임아웃 x 3회 + 대기 6초" 이내(약 40초)
# 로 실패가 확정되게 한다. 순간적인 풀 경합만 구제하는 게 목적이지, 진짜
# 장애까지 숨기려는 게 아니다.
CONNECT_MAX_ATTEMPTS = 3
CONNECT_RETRY_DELAY_SECONDS = 3


def get_connection() -> psycopg.Connection:
    # v2(검색엔진+Gemini 동의어 병합 기반, 2026-08-13 피벗)부터는 임베딩을 쓰지
    # 않아 pgvector 익스텐션이 필요 없다. register_vector(conn)는 v1 전용이었고
    # (pgvector 미설치 Supabase DB에서는 타입 조회 자체가 실패한다), v2 스키마엔
    # vector 컬럼이 없으므로 제거했다.
    last_exc: psycopg.OperationalError | None = None
    for attempt in range(1, CONNECT_MAX_ATTEMPTS + 1):
        try:
            return psycopg.connect(
                settings.database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS,
                # 2026-08-27 발견 — Supabase 트랜잭션 모드 풀러(포트 6543)는 매
                # 트랜잭션마다 다른 백엔드로 연결을 돌려막는다. psycopg3가 반복
                # 실행되는 쿼리를 자동으로 서버 쪽 prepared statement로 캐싱하면
                # (기본 동작) 그 이름이 다른 백엔드에 남아있던 것과 충돌해
                # "DuplicatePreparedStatement" 에러가 난다. prepare_threshold=None
                # 으로 서버 쪽 prepare 자체를 꺼서 회피한다.
                prepare_threshold=None,
            )
        except psycopg.OperationalError as exc:
            last_exc = exc
            if attempt < CONNECT_MAX_ATTEMPTS:
                time.sleep(CONNECT_RETRY_DELAY_SECONDS)
    raise last_exc
