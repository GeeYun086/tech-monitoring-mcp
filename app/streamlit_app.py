"""AX 시장 모니터링 Streamlit 대시보드.

계산(랭킹·필터링)은 전부 tech_monitoring.dashboard_queries가 한다 — 여기는
레이아웃만 담당한다(v1 ax-dashboard 스킬에서 이어받은 원칙, SKILL.md는
삭제됐지만 "숫자 계산은 스크립트가, 화면은 레이아웃만" 이유는 그대로 유효).

화면 구성:
    1. 직접 검색(큐레이션 검색엔진 라이브 호출, DB에 저장 안 함)
    2. 🏷️ 라벨링 탭 — 관련도 판단을 Gemini에서 로컬 분류기로 옮기기 위한
       학습 데이터를 사람이 직접 쌓는 화면(2026-08-18 추가, tech_monitoring.
       labeling). 한 번에 기사 하나만 보여주고 도움됨/도움 안 됨을 누르면
       저장 후 다음 기사로 넘어간다 — 262건을 훑어야 해서 목록을 통째로
       그리면 클릭마다 전체 재렌더가 걸리고 어디까지 했는지도 놓친다.
       라벨을 저장하면 그 기사는 후보에서 빠지므로 rerun만으로 자연히
       다음 기사가 나온다.
    3. 고정 키워드(모니터링 대상 시장) 탭
       - 주간 이슈 기사(주요 콘텐츠) — top20 등으로 안 자르고 이번 주
         수집분 전체를 최신순으로 보여준다(2026-08-13 담당자 확인 —
         나중에 라벨링 작업에 쓸 예정이라 넉넉하게).
       - 이번 주 주요 키워드는 접힌 expander(보조 지표)로 축소했다 —
         대문자 시작 휴리스틱이라 완벽하지 않고(US·Security 같은 애매한
         것도 섞임), 담당자가 "기사를 제대로 보여주는 쪽"에 무게를 두기로
         확인(2026-08-13). 코드는 그대로 두어 나중에 Gemini 복구되면
         품질을 다시 올릴 수 있게 했다.

DB 연결은 .env(DATABASE_URL)에서 읽는다(config.py 경유).

    ./.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
"""

import psycopg
import streamlit as st

from tech_monitoring import dashboard_queries as dq
from tech_monitoring import labeling
from tech_monitoring.collectors.search_engine import search_once
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import get_run_period

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

    # 실패를 화면에서 바로 알 수 있게(2026-08-18) — 그 전까지는 파이프라인이
    # 조용히 실패해도 "결과가 좀 적네"로만 보였다. 아래 기사 목록이 비어
    # 보이는 게 수집 실패 때문인지 진짜 기사가 없어서인지 구분되게 한다.
    if run["status"] == "failed":
        st.error(
            f"이번 주 파이프라인에서 실패한 단계가 있습니다: **{run.get('error_message') or '(사유 미기록)'}**  \n"
            "아래 결과가 불완전할 수 있습니다. 실패 사유는 파이프라인 실행 로그를 확인하세요."
        )


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


def _render_keyword_expander(keywords: list[dict]) -> None:
    with st.expander("📊 이번 주 주요 키워드 (보조 지표 — 정확도 제한 있음)", expanded=False):
        st.caption(
            "영문은 '단어가 대문자로 시작하면 기업·기술명일 것'이라는 근사 규칙이라 "
            "US·Security처럼 애매한 것도 섞일 수 있습니다. 정확한 개체명 인식은 아닙니다."
        )
        if not keywords:
            st.info("이번 주 주요 키워드가 아직 없습니다.")
        else:
            top = keywords[:15]
            st.bar_chart({k["canonical_phrase"]: k["doc_count"] for k in top})


def _render_labeling_progress(conn, fixed_keyword: dict, remaining: int) -> None:
    counts = labeling.count_labels(conn, fixed_keyword["id"])
    done = counts["total"]
    total = done + remaining

    st.progress(done / total if total else 1.0)
    st.caption(
        f"**{fixed_keyword['keyword']}** — 라벨 완료 {done} / {total}건 "
        f"(도움됨 {counts['relevant']} · 도움 안 됨 {counts['irrelevant']}) · 남은 후보 {remaining}건"
    )


def _render_labeling_card(conn, article: dict, fixed_keyword: dict, period_start) -> None:
    published = article["published_at"].strftime("%Y-%m-%d") if article.get("published_at") else "날짜 미상"
    st.markdown(f"### [{article['title']}]({article['url']})")
    st.caption(f"{article.get('source_domain') or '출처 미상'} · {published}")
    # 저장은 원문 전체(labeling.py 참고), 화면은 대시보드와 같은 길이로 자른다.
    if article.get("snippet"):
        st.write(dq.truncate_summary(article["snippet"], max_chars=400))

    st.write("")
    yes, no, skip = st.columns(3)

    def _save(label: str) -> None:
        labeling.save_label(conn, fixed_keyword["id"], article, label, period_start)

    # key에 url_norm을 넣어 기사가 바뀌면 버튼도 새 위젯이 되게 한다 — 같은
    # key를 재사용하면 Streamlit이 이전 클릭 상태를 물려받아 연속 저장이 난다.
    key = f"{fixed_keyword['id']}_{article['url_norm']}"
    if yes.button("도움이 되는 기사예요", key=f"yes_{key}", use_container_width=True, type="primary"):
        _save(labeling.LABEL_RELEVANT)
        st.rerun()
    if no.button("도움이 되지 않는 기사예요", key=f"no_{key}", use_container_width=True):
        _save(labeling.LABEL_IRRELEVANT)
        st.rerun()
    if skip.button("판단 보류", key=f"skip_{key}", use_container_width=True):
        # 저장하지 않고 이번 세션에서만 숨긴다 — 판단이 안 서는 걸 억지로
        # 라벨하면 학습 데이터가 오염된다. 새로고침하면 다시 나온다.
        st.session_state.setdefault("labeling_skipped", set()).add(article["url_norm"])
        st.rerun()


def _render_labeling_tab(conn, run_id: int, fixed_keywords: list[dict], period_start) -> None:
    st.subheader("🏷️ 라벨링")
    st.caption(
        "이 기사가 해당 시장을 모니터링하는 데 **도움이 되는지** 선택해 주세요. "
        "여기에 쌓인 판단이 그대로 관련도 분류기의 학습 데이터가 됩니다 "
        "(같은 기사라도 시장이 다르면 판단이 달라질 수 있어 시장별로 따로 여쭙니다)."
    )

    keyword_names = [kw["keyword"] for kw in fixed_keywords]
    selected = st.selectbox("어느 시장 기준으로 라벨링할까요?", keyword_names, key="labeling_keyword")
    fixed_keyword = next(kw for kw in fixed_keywords if kw["keyword"] == selected)

    candidates = labeling.fetch_unlabeled_candidates(conn, run_id, fixed_keyword["id"])
    skipped = st.session_state.get("labeling_skipped", set())
    pending = [c for c in candidates if c["url_norm"] not in skipped]

    _render_labeling_progress(conn, fixed_keyword, len(candidates))

    if not pending:
        if skipped and candidates:
            st.info(f"보류한 {len(candidates)}건만 남았습니다. 새로고침하면 다시 볼 수 있습니다.")
        else:
            st.success("이 시장은 라벨링이 끝났습니다. 위에서 다른 시장을 선택하세요.")
        return

    st.divider()
    _render_labeling_card(conn, pending[0], fixed_keyword, period_start)


def _render_keyword_tab(conn, run_id: int, fixed_keyword: dict) -> None:
    keywords = dq.get_market_keywords(conn, run_id, fixed_keyword["id"])

    _render_keyword_expander(keywords)

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

    # 라벨링을 맨 앞 탭에 둔다 — 지금 단계에서 매주 실제로 하는 작업이고,
    # 키워드 탭들과 나란히 두면 어느 시장을 라벨링 중인지가 탭 선택과
    # 뒤섞여 헷갈린다(라벨링 안에서 시장을 따로 고르게 했다).
    period_start, _period_end = get_run_period(conn, run["id"])
    tabs = st.tabs(["🏷️ 라벨링"] + [kw["keyword"] for kw in fixed_keywords])

    with tabs[0]:
        _render_labeling_tab(conn, run["id"], fixed_keywords, period_start)

    for tab, kw in zip(tabs[1:], fixed_keywords):
        with tab:
            _render_keyword_tab(conn, run["id"], kw)


main()
