# tech-monitoring-mcp
goormEDU 전략기획팀 업무 효율성 개선 및 자동화를 위한 프로젝트 - 산업 동향 모니터링용 사내 mcp 시스템 구현

## 개발 환경 세팅

```bash
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
cp .env.example .env

docker compose up -d          # Postgres + pgvector 컨테이너
./.venv/Scripts/python.exe -m tech_monitoring.db.migrate   # 스키마 적용 + 소스 시딩

./.venv/Scripts/python.exe -m tech_monitoring.collectors.rss   # RSS 수집 실행
./.venv/Scripts/python.exe -m pytest tests/ -q                 # 테스트
```

## 소스 현황 (Day 2 기준)

`db/migrations/002_seed_sources.sql`에 리서치 문서상 피드 URL이 확인된 소스만 시딩. 실제 수집 테스트 결과 3건은 깨져 있어 비활성화(`003_deactivate_broken_feeds.sql`):
- 전자신문: `/rss/`가 HTML을 반환 (실제 피드 주소 재확인 필요)
- a16z: `/feed/` 404 (주소 변경 추정)
- GeekNews: UA 지정에도 403 (nginx 차단, 기존 시스템 B5와 동일 증상)

나머지 국내 매체(ZDNet·AI타임스·블로터·바이라인)와 Anthropic 공식 피드는 리서치 문서에서도 주소가 ◐확인 상태라 이번엔 시딩하지 않음.

## 프로젝트 구조

```
src/tech_monitoring/
  collectors/   # RSS·API 수집기 (Day2~3)
  filters/      # 관련도·중요도 필터 (Day4~6)
  db/           # DB 연결·마이그레이션
  mcp_server/   # 통합 MCP 서버 (Phase 2)
db/migrations/  # SQL 스키마 마이그레이션
```
