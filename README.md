# tech-monitoring-mcp
goormEDU 전략기획팀 업무 효율성 개선 및 자동화를 위한 프로젝트 - 산업 동향 모니터링용 사내 mcp 시스템 구현

## 개발 환경 세팅

```bash
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
cp .env.example .env

docker compose up -d          # Postgres + pgvector 컨테이너
./.venv/Scripts/python.exe -m tech_monitoring.db.migrate   # 스키마 적용
```

## 프로젝트 구조

```
src/tech_monitoring/
  collectors/   # RSS·API 수집기 (Day2~3)
  filters/      # 관련도·중요도 필터 (Day4~6)
  db/           # DB 연결·마이그레이션
  mcp_server/   # 통합 MCP 서버 (Phase 2)
db/migrations/  # SQL 스키마 마이그레이션
```
