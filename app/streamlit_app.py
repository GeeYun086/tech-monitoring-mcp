"""AX 시장 모니터링 Streamlit 대시보드.

계산(랭킹·필터링)은 전부 tech_monitoring.dashboard_queries가 한다 — 여기는
레이아웃만 담당한다(v1 ax-dashboard 스킬에서 이어받은 원칙, SKILL.md는
삭제됐지만 "숫자 계산은 스크립트가, 화면은 레이아웃만" 이유는 그대로 유효).

화면 구성:
    1. 직접 검색(큐레이션 검색엔진 라이브 호출, DB에 저장 안 함)
    2. 고정 키워드(모니터링 대상 시장) 탭
       - 이번 주 주요 키워드(막대그래프 — 동의어 병합까지 끝난 doc_count 기준)
       - 키워드 선택 → 관련 기사만 필터(선택 안 하면 검색엔진 원 순위로 전체 표시)

DB 연결은 .env(DATABASE_URL)에서 읽는다(config.py 경유).

    ./.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
"""

import psycopg
import streamlit as st

from tech_monitoring import dashboard_queries as dq
from tech_monitoring.collectors.search_engine import search_once
from tech_monitoring.db.connection import get_connection

st.set_page_config(page_title="AX 시장 모니터링", layout="wide")

_STATUS_LABELS = {"completed": "완료", "running": "진행 중", "failed": "실패"}


@st.cache_resource
def _conn():
    # connect_timeout(db/connection.py)이 실패를 빨리 드러내주므로, 여기서
    # 잡아서 화면에 원인을 보여준다 — 안 잡으면 Streamlit 기본 에러 페이지로
    # 스택트레이스가 그대로 노출된다(DATABASE_URL·Supabase 일시정지 등
    # 운영 중 흔한 원인을 담당자가 바로 알아볼 수 있는 메시지가 더 낫다).
    try:
        return get_connection()
    except psycopg.OperationalError as exc:
        st.error(
            "DB에 연결할 수 없습니다. `.env`의 DATABASE_URL을 확인하거나, "
            "Supabase가 일시정지(무료 티어 7일 미사용 시 자동 정지)되지 않았는지 "
            f"확인하세요.\n\n오류: {exc}"
        )
        st.stop()


def _render_run_banner(run: dict | None) -> None:
    if run is None:
        st.warning(
            "아직 수집된 데이터가 없습니다. "
            "`./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v2`를 먼저 실행하세요."
        )
        return
    label = _STATUS_LABELS.get(run["status"], run["status"])
    st.caption(f"기준 기간: {run['period_start']} ~ {run['period_end']} · 상태: {label}")


def _render_search_box() -> None:
    st.subheader("🔍 직접 검색")
    query = st.text_input(
        "큐레이션 검색엔진에서 바로 검색(이번 주 데이터와 무관 — 매 요청 라이브 호출)",
        placeholder="예: OpenAI 기업 도입 사례",
    )
    if not query:
        return
    results = search_once(query)
    if not results:
        st.info("검색 결과가 없거나 검색 설정(TAVILY_API_KEY)이 비어 있습니다.")
        return
    for item in results:
        st.markdown(
            f"**[{item.get('title', '(제목 없음)')}]({item.get('link', '')})**  \n"
            f"{item.get('displayLink', '')} — {item.get('snippet', '')}"
        )


def _render_article_list(articles: list[dict]) -> None:
    if not articles:
        st.info("해당하는 기사가 없습니다.")
        return
    for a in articles:
        published = a["published_at"].strftime("%Y-%m-%d") if a.get("published_at") else ""
        meta = " · ".join(p for p in (a.get("source_domain"), published) if p)
        st.markdown(
            f"- [{a['title']}]({a['url']})"
            + (f"  \n  <span style='color:gray'>{meta}</span>" if meta else ""),
            unsafe_allow_html=True,
        )
        if a.get("snippet"):
            st.caption(a["snippet"])


def _render_keyword_tab(conn, run_id: int, fixed_keyword: dict) -> None:
    keywords = dq.get_market_keywords(conn, run_id, fixed_keyword["id"])

    st.markdown("**이번 주 주요 키워드**")
    if not keywords:
        st.info("이번 주 주요 키워드가 아직 없습니다.")
    else:
        top = keywords[:15]
        st.bar_chart({k["canonical_phrase"]: k["doc_count"] for k in top})

    st.markdown("**주간 이슈 기사**")
    options = ["(전체)"] + [k["canonical_phrase"] for k in keywords]
    selected = st.selectbox(
        "키워드로 기사 필터링", options, key=f"kw_select_{fixed_keyword['id']}",
    )

    if selected == "(전체)":
        articles = dq.get_search_results(conn, run_id, fixed_keyword["id"])
    else:
        variant_phrases = next(
            k["variant_phrases"] for k in keywords if k["canonical_phrase"] == selected
        )
        articles = dq.get_search_results_for_variants(
            conn, run_id, fixed_keyword["id"], variant_phrases,
        )

    st.caption(f"{len(articles)}건")
    _render_article_list(articles)


def main() -> None:
    st.title("AX 시장 모니터링")

    conn = _conn()
    run = dq.get_latest_run(conn)
    _render_run_banner(run)

    _render_search_box()
    st.divider()

    fixed_keywords = dq.get_fixed_keywords(conn)
    if not fixed_keywords:
        st.warning(
            "고정 키워드가 없습니다. "
            "`./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py add \"<키워드>\"`로 등록하세요."
        )
        return
    if run is None:
        return

    tabs = st.tabs([kw["keyword"] for kw in fixed_keywords])
    for tab, kw in zip(tabs, fixed_keywords):
        with tab:
            _render_keyword_tab(conn, run["id"], kw)


main()
