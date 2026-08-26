"""fixed_keywords(모니터링 대상 시장 고정 키워드) 관리용 CLI.

Streamlit UI가 생기기 전까지 이 테이블을 직접 만지는 유일한 통로다.
매주 TRUNCATE ... CASCADE로 다른 수집 테이블은 다 비워도 fixed_keywords는
설정값이라 보존된다(db/migrations/001_market_keywords_schema.sql 참고) —
그래서 "매번 다시 넣어야 하는" 스크립트가 아니라 "최초 1회 + 가끔 조정"용이다.

    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py list
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py add "콘텐츠팀" --order 1
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py set-terms "콘텐츠팀" --ko "AI 교육,에듀테크" --en "AI education,edtech"
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py set-sites "콘텐츠팀" --sites "aitimes.com,edu.donga.com"
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py deactivate "콘텐츠팀"
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py activate "콘텐츠팀"
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py remove "콘텐츠팀"
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py rename "콘텐츠팀" "콘텐츠제작팀"

**한 배포 = 팀 하나(2026-08-25 재설계)** — 새 배포를 부트스트랩할 때는
`add`로 팀 이름을 만들고, `set-terms`·`set-sites`로 그 팀의 검색어·사이트를
정한 뒤 파이프라인을 처음 한 번 돌린다(README "다른 팀이 독립적으로
배포하기" 참고). 그 뒤로는 화면(app/streamlit_app.py)이 그 설정 그대로
매주 자동 수집한 기사를 보여준다 — 화면에서 팀을 더 추가하는 기능은 없다.

set-terms(2026-08-13 추가): 실제 검색에 쓸 언어별 동의어 목록. keyword
문자열 자체를 그대로 검색어로 쓰면 (1) 표현이 다르면 못 찾고 (2) 영어
사이트엔 한국어라 아예 안 맞는 문제가 실사용 확인됐다(collectors/
search_engine.py 모듈 docstring 참고) — 그래서 keyword는 "표시용 이름"
으로 두고, 검색은 이 목록으로 한다. 둘 다 비워두면(신규 팀 기본값) 넓은
질의("AI"/"인공지능")로 폴백한다(2026-08-25 변경 — 예전엔 keyword 문자열
자체로 폴백했는데, 팀 이름이 검색 의도와 무관한 경우가 많아 바꿨다).

set-sites(2026-08-25 추가): 이 팀이 수집할 사이트. 비워두면 전체
화이트리스트(collectors/search_engine.py의 SITE_DOMAINS)를 쓴다. 이미
등록된 화이트리스트 중에서만 골라야 한다 — 완전히 새 사이트를 추가하려면
코드 수정이 필요하다(README "새 수집 사이트 추가하기" 참고).
"""

import argparse
import sys

from tech_monitoring.db.connection import get_connection

# Windows 콘솔 코드페이지(cp949 등)로는 이 파일의 em dash(—) 같은 문자에서
# UnicodeEncodeError가 난다(scripts/dashboard_data.py와 같은 문제). 어떤
# 환경에서 실행되든 안전하게 UTF-8로 낸다.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def list_keywords(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, keyword, display_order, active, created_at, "
            "search_terms_ko, search_terms_en, site_domains "
            "FROM fixed_keywords ORDER BY display_order, id"
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def set_search_terms(conn, keyword: str, terms_ko: list[str], terms_en: list[str]) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE fixed_keywords SET search_terms_ko = %s, search_terms_en = %s "
            "WHERE keyword = %s RETURNING id",
            (terms_ko, terms_en, keyword),
        )
        return cur.fetchone() is not None


def set_sites(conn, keyword: str, sites: list[str]) -> bool:
    """이 팀이 수집할 사이트 목록(2026-08-25, 009 마이그레이션).

    빈 리스트를 주면 NULL로 저장돼 "전체 화이트리스트 사용"으로 폴백한다
    (collectors/search_engine.py의 collect_all 참고) — 명시적으로 좁히고
    싶을 때만 값을 준다. 도메인 문자열은 검증하지 않는다 — 이미 코드에
    등록된 화이트리스트(collectors/search_engine.py의 SITE_DOMAINS) 중에서
    골라야 실제로 수집된다."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE fixed_keywords SET site_domains = %s WHERE keyword = %s RETURNING id",
            (sites or None, keyword),
        )
        return cur.fetchone() is not None


def add_keyword(conn, keyword: str, display_order: int) -> None:
    # 이미 있는데 비활성화돼 있던 경우 재추가하면 다시 살아나게(active=TRUE) 한다 —
    # remove 대신 deactivate로 꺼둔 걸 그대로 두 번 add해서 되살리는 흐름을 자연스럽게 지원.
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fixed_keywords (keyword, display_order, active)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (keyword)
                DO UPDATE SET display_order = EXCLUDED.display_order, active = TRUE
            """,
            (keyword, display_order),
        )


def set_active(conn, keyword: str, active: bool) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE fixed_keywords SET active = %s WHERE keyword = %s RETURNING id",
            (active, keyword),
        )
        return cur.fetchone() is not None


def rename_keyword(conn, old: str, new: str) -> bool:
    """표시 이름만 바꾼다(2026-08-24, 마켓 구분 제거 추가) — id는 그대로라
    article_labels·article_keyword_relevance 등 기존 데이터와의 연결이
    끊기지 않는다. 순수 화면 표시용 문자열 하나만 바뀐다."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE fixed_keywords SET keyword = %s WHERE keyword = %s RETURNING id",
            (new, old),
        )
        return cur.fetchone() is not None


def remove_keyword(conn, keyword: str) -> bool:
    """하드 삭제. search_results/market_keywords가 fixed_keyword_id를 참조하지만
    ON DELETE CASCADE가 없으므로(설정 삭제로 지난 수집 데이터가 조용히 같이 지워지면
    안 됨), 이번 주 배치가 이미 이 키워드로 데이터를 쌓아둔 상태에서 삭제하면
    FK 위반으로 막힌다 — 그럴 땐 먼저 deactivate로 다음 배치부터만 빼는 걸 권장."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fixed_keywords WHERE keyword = %s RETURNING id", (keyword,))
        return cur.fetchone() is not None


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(등록된 고정 키워드 없음)")
        return
    for row in rows:
        status = "active" if row["active"] else "inactive"
        print(f"[{row['id']:>3}] {row['keyword']:<20} order={row['display_order']} ({status})")
        ko = ", ".join(row.get("search_terms_ko") or []) or "(미등록 — 넓은 질의로 폴백)"
        en = ", ".join(row.get("search_terms_en") or []) or "(미등록 — 넓은 질의로 폴백)"
        sites = ", ".join(row.get("site_domains") or []) or "(미등록 — 전체 화이트리스트)"
        print(f"      ko: {ko}")
        print(f"      en: {en}")
        print(f"      sites: {sites}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="등록된 고정 키워드 전체 조회")

    add_parser = sub.add_parser("add", help="고정 키워드 추가(이미 있으면 갱신)")
    add_parser.add_argument("keyword")
    add_parser.add_argument("--order", type=int, default=0, help="대시보드 탭 표시 순서(작을수록 먼저)")

    deactivate_parser = sub.add_parser("deactivate", help="비활성화(다음 배치부터 수집 제외, 데이터는 보존)")
    deactivate_parser.add_argument("keyword")

    activate_parser = sub.add_parser("activate", help="비활성화된 키워드 재활성화")
    activate_parser.add_argument("keyword")

    remove_parser = sub.add_parser("remove", help="완전 삭제(이번 주 수집 데이터가 이미 있으면 FK 위반으로 실패)")
    remove_parser.add_argument("keyword")

    rename_parser = sub.add_parser("rename", help="표시 이름만 변경(id·기존 라벨·점수는 그대로 유지)")
    rename_parser.add_argument("old_keyword")
    rename_parser.add_argument("new_keyword")

    terms_parser = sub.add_parser("set-terms", help="언어별 실제 검색어(동의어) 목록 설정")
    terms_parser.add_argument("keyword")
    terms_parser.add_argument("--ko", default="", help="한국어 검색어, 쉼표로 구분")
    terms_parser.add_argument("--en", default="", help="영어 검색어, 쉼표로 구분")

    sites_parser = sub.add_parser(
        "set-sites",
        help="이 팀이 수집할 사이트 목록 설정(비우면 전체 화이트리스트로 폴백)",
    )
    sites_parser.add_argument("keyword")
    sites_parser.add_argument(
        "--sites", default="",
        help="쉼표로 구분한 도메인(collectors/search_engine.py의 SITE_DOMAINS 중에서), "
             "예: aitimes.com,techcrunch.com. 비우면 전체 사용",
    )

    args = parser.parse_args()
    conn = get_connection()
    try:
        if args.command == "list":
            _print_table(list_keywords(conn))
        elif args.command == "add":
            add_keyword(conn, args.keyword, args.order)
            print(f"추가/갱신됨: {args.keyword}")
        elif args.command == "deactivate":
            if not set_active(conn, args.keyword, False):
                print(f"'{args.keyword}'를 찾을 수 없음", file=sys.stderr)
                sys.exit(1)
            print(f"비활성화됨: {args.keyword}")
        elif args.command == "activate":
            if not set_active(conn, args.keyword, True):
                print(f"'{args.keyword}'를 찾을 수 없음", file=sys.stderr)
                sys.exit(1)
            print(f"재활성화됨: {args.keyword}")
        elif args.command == "remove":
            if not remove_keyword(conn, args.keyword):
                print(f"'{args.keyword}'를 찾을 수 없음", file=sys.stderr)
                sys.exit(1)
            print(f"삭제됨: {args.keyword}")
        elif args.command == "rename":
            if not rename_keyword(conn, args.old_keyword, args.new_keyword):
                print(f"'{args.old_keyword}'를 찾을 수 없음", file=sys.stderr)
                sys.exit(1)
            print(f"이름 변경됨: {args.old_keyword} -> {args.new_keyword}")
        elif args.command == "set-terms":
            terms_ko = [t.strip() for t in args.ko.split(",") if t.strip()]
            terms_en = [t.strip() for t in args.en.split(",") if t.strip()]
            if not set_search_terms(conn, args.keyword, terms_ko, terms_en):
                print(f"'{args.keyword}'를 찾을 수 없음", file=sys.stderr)
                sys.exit(1)
            print(f"검색어 설정됨: {args.keyword} (ko={terms_ko}, en={terms_en})")
        elif args.command == "set-sites":
            sites = [s.strip() for s in args.sites.split(",") if s.strip()]
            if not set_sites(conn, args.keyword, sites):
                print(f"'{args.keyword}'를 찾을 수 없음", file=sys.stderr)
                sys.exit(1)
            print(f"사이트 설정됨: {args.keyword} (sites={sites or '전체'})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
