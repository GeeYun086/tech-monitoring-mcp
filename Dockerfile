# MCP 서버 컨테이너(2026-08-19, 작업 8).
#
# **MCP 서버만 담는다.** 다른 구성요소는 컨테이너가 필요 없다:
#   - DB        : Supabase(이미 클라우드) — 배포할 것이 없다
#   - 대시보드  : Streamlit Cloud가 저장소를 연결해 직접 띄운다
#   - 파이프라인: 주 1회 배치라 GitHub Actions가 돌린다(.github/workflows)
# 남는 건 "사람 PC에서 Claude가 붙어 쓰는 MCP 서버"뿐이고, 그건 도커로 주는 게
# 가장 편하다 — 받는 사람이 Python·가상환경·의존성을 만질 필요가 없다.
#
# **머신러닝 라이브러리를 넣지 않는다.** MCP 서버는 읽기 전용이라 분류기를
# 돌리지 않는다 — 판단은 파이프라인이 미리 끝내 article_keyword_relevance.score에
# 저장해두고, 여기서는 그 값을 꺼내 보여주기만 한다(mcp_server/server.py 헤더).
# 그래서 psycopg + mcp만 있으면 되고 이미지가 수백 MB에 머문다. 프로젝트 전체를
# 설치하면 streamlit·scikit-learn까지 딸려와 쓰지도 않을 것들로 몇 배가 된다.
FROM python:3.12-slim

WORKDIR /app

# 의존성을 먼저 넣어 레이어 캐시를 살린다 — 소스만 바뀌면 재설치를 건너뛴다.
# pyproject.toml의 dependencies에서 이 둘만 골라 적은 것이라, 그쪽 버전 하한이
# 바뀌면 여기도 같이 손봐야 한다(중복이지만 이미지 크기를 위해 감수한다).
# mcp는 상한을 둔다. 2.0.0에서 mcp.server.fastmcp가 사라져(모듈 구성이 통째로
# 바뀌었다) server.py의 import가 깨진다 — 실측 2026-08-19: ">=1.2"로 열어뒀더니
# 컨테이너만 2.0.0을 받아 ModuleNotFoundError로 즉시 종료됐다(로컬 .venv는
# 1.29.0이라 멀쩡했다 — 새 환경에서만 터지는 종류의 함정).
# 2.x로 올릴 때는 server.py를 새 API에 맞춰 고친 뒤 상한을 푼다.
RUN pip install --no-cache-dir "psycopg[binary]>=3.2" "mcp>=1.2,<2" \
    "pydantic>=2.8" "pydantic-settings>=2.4" "python-dotenv>=1.0"

# 패키지로 설치하지 않고 소스만 복사한다 — pip install .을 하면 pyproject의
# 의존성 전체(streamlit·scikit-learn 등)가 함께 들어온다. PYTHONPATH로 대신한다.
COPY src/tech_monitoring /app/tech_monitoring
ENV PYTHONPATH=/app

# stdio 전송이라 표준출력이 곧 프로토콜 채널이다. 파이썬이 출력을 버퍼링하면
# 클라이언트가 응답을 늦게 받거나 핸드셰이크가 멈춘 것처럼 보인다.
ENV PYTHONUNBUFFERED=1

# DATABASE_URL은 실행할 때 -e로 준다(이미지에 비밀번호를 굽지 않는다).
ENTRYPOINT ["python", "-m", "tech_monitoring.mcp_server"]
