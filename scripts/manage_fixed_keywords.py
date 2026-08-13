"""fixed_keywords(모니터링 대상 시장 고정 키워드) 관리용 CLI.

Streamlit UI가 생기기 전까지 이 테이블을 직접 만지는 유일한 통로다.
매주 TRUNCATE ... CASCADE로 다른 수집 테이블은 다 비워도 fixed_keywords는
설정값이라 보존된다(db/migrations/001_market_keywords_schema.sql 참고) —
그래서 "매번 다시 넣어야 하는" 스크립트가 아니라 "최초 1회 + 가끔 조정"용이다.

    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py list
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py add "AX 시장" --order 1
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py deactivate "AX 시장"
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py activate "AX 시장"
    ./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py remove "AX 시장"
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
            "SELECT id, keyword, display_order, active, created_at "
            "FROM fixed_keywords ORDER BY display_order, id"
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


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
    finally:
        conn.close()


if __name__ == "__main__":
    main()
