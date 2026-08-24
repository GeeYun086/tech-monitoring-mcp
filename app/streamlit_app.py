"""AX 시장 모니터링 Streamlit 대시보드.

계산(랭킹·필터링)은 전부 tech_monitoring.dashboard_queries가 한다 — 여기는
레이아웃만 담당한다(v1 ax-dashboard 스킬에서 이어받은 원칙, SKILL.md는
삭제됐지만 "숫자 계산은 스크립트가, 화면은 레이아웃만" 이유는 그대로 유효).

화면 구성(2026-08-24 대개편 — 마켓 3개 분할 제거):
    1. "기사" 탭(기본 화면) — 이번 주 공용 기사 풀 전체를 보여준다(006,
       top20 등으로 안 자르는 원칙은 그대로, 2026-08-13 담당자 확인).
       **마켓(고정 키워드) 3개로 나눠 보던 걸 없앴다** — 수집 자체가 애초에
       시장과 무관한 공용 풀이라(collectors/search_engine.py의 "공용 기사
       풀" 방식) 3개로 쪼개 보는 게 의미 없다는 담당자 판단. DB의
       fixed_keywords는 스키마를 안 건드리려고 1개만 active로 남겨뒀다
       (scripts/manage_fixed_keywords.py로 조정 가능하지만, 지금 설계는
       "여러 마켓"을 다시 켜는 걸 상정하지 않는다).
       - 정렬: **국내 매체 우선**(2026-08-24 담당자 피드백 — 해외 기사에
         묻혀 국내 기사가 잘 안 보인다는 의견. dashboard_queries.
         _sort_domestic_first, 국내/해외 판정은 collectors/search_engine.py
         의 KOREAN_DOMAINS 재사용) 다음 분류기 점수(모델 없으면 최신순) —
         무엇으로 정렬됐는지 목록 위에 표시한다.
       - **키워드 검색**(2026-08-24) — 문장 임베딩 코사인 유사도로 찾는다.
         완전히 같은 단어가 아니어도(동의어·다른 표현) 걸린다. 예전
         "🔍 직접 검색"(Tavily 라이브 호출)과 "키워드로 기사 필터링"
         드롭다운(market_keywords 기반, 더는 안 채워짐)을 이걸로 대체했다
         — 담당자 판단: 이미 모아둔 이번 주 기사 안에서 찾는 것으로 충분
         하고, 매 검색마다 API 크레딧을 쓰는 라이브 호출은 필요 없다.
       - 각 기사 행에 👍/👎 인라인 버튼 — 262건을 한 번에 하나씩 강제로
         훑던 예전 🏷️ 라벨링 탭을 없애고(비효율적이라는 담당자 피드백)
         기사를 먼저 보여주고 읽다가 필요한 것만 누르게 한다. 저장 로직은
         예전과 동일하게 tech_monitoring.labeling을 그대로 쓴다 — 바뀐 건
         "언제·어디서 누르는가"뿐이다. 같은 버튼을 다시 누르면 라벨이
         취소되고(labeling.delete_label), 반대 버튼을 누르면 뒤집힌다
         (labeling.save_label의 UPSERT). 버튼을 누를 때마다
         auto_retrain.maybe_retrain이 불려 라벨 5건마다(기본값) 분류기를
         자동으로 다시 학습하고 이번 주 순위를 갱신한다(auto_retrain.py
         모듈 docstring 참고 — 클릭마다 재학습하지 않는 이유도 거기 있다).
       - 분류기는 더 이상 "이 기사가 이 시장에 도움되는가"가 아니라
         "일반적으로 도움되는가" 하나로 판단한다(relevance_model.build_text
         — 시장 이름을 더는 입력에 안 붙인다). 마켓 3개에 걸쳐 있던 예전
         라벨은 버리지 않고 그대로 하나의 학습 데이터로 합쳐 쓴다.
    2. 📈 성능 탭(보조 지표라 뒤로) — 라벨을 정답지로 삼아 분류기를
       채점한다(relevance_model). 라벨 수가 적을 때는 클래스 분포만
       보여주고, 최소 기준을 넘으면 버튼을 눌러 측정한다. 정확도는 항상
       "찍기 기준선"과 나란히 보여준다(쏠린 라벨에서 정확도만 보면 착시가
       생긴다).
    "이번 주 주요 키워드" 차트는 없앴다(2026-08-24 담당자 결정) — 대문자
    시작 휴리스틱이라 품질이 낮은 채로 방치돼 있었는데, 그 목록을 만들려고
    파이프라인이 매주 Gemini 동의어 병합을 부르는 비용이 품질 대비 아깝다는
    판단. pipeline_v2.py에서도 merge_keywords 단계를 뺐다.

DB 연결은 .env(DATABASE_URL)에서 읽는다(config.py 경유).

    ./.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
"""

import psycopg
import streamlit as st

from tech_monitoring import auto_retrain
from tech_monitoring import dashboard_queries as dq
from tech_monitoring import labeling
from tech_monitoring import relevance_model
from tech_monitoring.analysis.relevance_filter import judge_all
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import get_run_period, week_bounds_for
from tech_monitoring.utils.url_normalize import normalize_url

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


def _render_run_banner(conn, run: dict | None) -> None:
    if run is None:
        st.warning(
            "아직 수집된 데이터가 없습니다. "
            "`./.venv/Scripts/python.exe -m tech_monitoring.pipeline_v2`를 먼저 실행하세요."
        )
        return
    label = _STATUS_LABELS.get(run["status"], run["status"])
    st.caption(f"기준 기간: {run['period_start']} ~ {run['period_end']} · 상태: {label}")

    # 소급 수집분이 섞여 있으면 그렇다고 말해준다 — 안 그러면 기준 기간보다
    # 오래된 기사가 목록에 있는 게 기간 계산 오류처럼 보인다.
    span = dq.get_pool_span(conn, run["id"])
    if span["oldest"] and span["oldest"] < run["period_start"]:
        st.caption(
            f"↳ 최초 라벨링용 **소급 수집분 포함** — 목록의 기사 발행일은 "
            f"{span['oldest']} ~ {span['newest']} ({span['total']}건)"
        )

    # 실패를 화면에서 바로 알 수 있게(2026-08-18) — 그 전까지는 파이프라인이
    # 조용히 실패해도 "결과가 좀 적네"로만 보였다. 아래 기사 목록이 비어
    # 보이는 게 수집 실패 때문인지 진짜 기사가 없어서인지 구분되게 한다.
    if run["status"] == "failed":
        st.error(
            f"이번 주 파이프라인에서 실패한 단계가 있습니다: **{run.get('error_message') or '(사유 미기록)'}**  \n"
            "아래 결과가 불완전할 수 있습니다. 실패 사유는 파이프라인 실행 로그를 확인하세요."
        )


_ALL_WEEKS = "전체 기간"
_UNDATED_LABEL = "날짜 미상"


def _week_label(week_start) -> str:
    """주를 "8/10~8/16"처럼 **기간 범위**로 적는다(2026-08-19 담당자 요청).

    월요일 날짜 하나만 적으면("2026-08-10 주") 그게 그 주의 시작인지, 그 날
    수집했다는 뜻인지 읽는 사람이 알 수 없다. 이 프로젝트는 "월요일에 직전
    주를 걷는다"라서 수집일과 기사 발행 주가 항상 어긋나 있어 더 헷갈린다 —
    8/17에 걷은 건 8/10~8/16 기사다. 범위로 적으면 그 혼동이 사라진다.
    """
    _monday, sunday = week_bounds_for(week_start)
    return f"{week_start.month}/{week_start.day}~{sunday.month}/{sunday.day}"


def _week_options(conn, run_id: int) -> dict:
    """주차 선택 라벨 -> week_start 값. 소급 수집(작업 4)으로 한 run에 여러 주가
    섞여 있어서, 한 주씩 골라 보고 라벨할 수 있어야 한다 — 후보가 최신순이라
    필터 없이 진행하면 라벨이 최신 주에만 몰리고 주차 단위 교차검증이 성립하지
    않는다(실측: 라벨 30건이 전부 한 주에 몰렸다)."""
    options = {_ALL_WEEKS: None}
    for week in dq.get_pool_weeks(conn, run_id):
        if week["week_start"] is None:
            options[f"{_UNDATED_LABEL} ({week['total']}건)"] = labeling.UNDATED
        else:
            options[f"{_week_label(week['week_start'])} ({week['total']}건)"] = week["week_start"]
    return options


def _select_week(conn, run_id: int, key: str):
    """주차 선택 위젯. 선택값(week_start)을 그대로 조회 함수에 넘기면 된다."""
    options = _week_options(conn, run_id)
    if len(options) <= 1:          # 한 주뿐이면 고를 게 없다
        return None
    label = st.selectbox("어느 주 기사를 볼까요?", list(options), key=key)
    return options[label]


_LABEL_BADGES = {
    labeling.LABEL_RELEVANT: "👍 도움됨",
    labeling.LABEL_IRRELEVANT: "👎 도움 안 됨",
}


def _apply_retrain_feedback(retrain_result: dict | None) -> None:
    """auto_retrain.maybe_retrain의 결과를 짧은 토스트로 알려준다.

    None(문턱 안 넘음)이면 조용히 넘어간다 — 라벨 저장 자체는 이미 끝났고,
    "아직 재학습할 때가 아니다"는 매번 알릴 정보가 아니다."""
    if retrain_result is None:
        return
    if retrain_result["trained"]:
        st.toast(
            f"라벨이 쌓여 분류기를 다시 학습했습니다({retrain_result['method']}, "
            f"F1 {retrain_result['metrics']['f1']:.3f}) — 이번 주 순위에 반영됐습니다.",
            icon="🔄",
        )
    else:
        st.toast("라벨은 저장했지만 아직 찍기 기준선을 넘지 못해 순위는 그대로입니다.", icon="ℹ️")


_FEEDBACK_PAGE_SIZE = 30


def _render_feedback_article_list(
    conn, fixed_keyword: dict, articles: list[dict], period_start,
) -> None:
    """기사 목록 + 행마다 인라인 👍/👎(2026-08-24, 🏷️ 라벨링 탭 대체).

    저장 자체는 예전과 똑같이 tech_monitoring.labeling을 그대로 쓴다 —
    get_pool_articles가 돌려주는 행엔 없는 url_norm·source_table만 여기서
    채워 넣는다(labeling.save_label이 요구하는 모양, labeling.py 헤더 참고).

    같은 버튼을 다시 누르면 취소(delete_label), 반대 버튼을 누르면
    뒤집힌다(save_label의 UPSERT가 그대로 덮어쓴다) — 사람이 지금 상태를
    보고 판단해야 하므로 fetch_label_map으로 미리 다 가져와 버튼 표시(type=
    "primary")에 반영한다.

    **"더 보기" 페이지네이션(2026-08-24 실사용 중 발견)**: 기사마다 버튼을
    2개씩 그리면 시장 하나(최대 415건)만 해도 위젯이 830개를 넘는다.
    st.tabs를 segmented_control로 바꿔 시장 3개 동시 렌더는 막았지만
    (위 main() 참고), 시장 **하나**의 830개 위젯만으로도 첫 클릭 직후
    커넥션이 맛이 가서 그다음 클릭부터 반응이 없는 걸 실사용 중 확인했다
    (스피너조차 안 뜸 — 요청이 서버에 닿지도 못한다는 뜻). "전체 안 자르고
    보여준다" 원칙(README)은 **데이터**에 대한 것이지 "동시에 살아있는
    위젯 개수"에 대한 게 아니라고 보고, 화면엔 한 번에 _FEEDBACK_PAGE_SIZE
    건만 위젯으로 그리고 "더 보기"로 이어 붙인다 — 다 보려면 몇 번 눌러야
    하지만 데이터 자체는 여전히 전부 도달 가능하다."""
    if not articles:
        st.info("해당하는 기사가 없습니다.")
        return

    state_key = f"feedback_visible_{fixed_keyword['id']}"
    visible = st.session_state.get(state_key, _FEEDBACK_PAGE_SIZE)

    label_map = labeling.fetch_label_map(conn, fixed_keyword["id"])

    for a in articles[:visible]:
        url_norm = normalize_url(a["url"])
        current = label_map.get(url_norm)

        published = a["published_at"].strftime("%Y-%m-%d") if a.get("published_at") else ""
        meta = " · ".join(p for p in (a.get("source_domain"), published) if p)

        text_col, up_col, down_col = st.columns([10, 1, 1])
        text_col.markdown(
            f"- [{a['title']}]({a['url']})"
            + (f"  \n  <span style='color:gray'>{meta}</span>" if meta else ""),
            unsafe_allow_html=True,
        )
        if a.get("snippet"):
            text_col.caption(a["snippet"])

        # key에 url_norm을 넣어 기사가 바뀌면 버튼도 새 위젯이 되게 한다 —
        # 같은 key를 재사용하면 Streamlit이 이전 클릭 상태를 물려받는다.
        key = f"{fixed_keyword['id']}_{url_norm}"
        clicked = None
        if up_col.button(
            "👍", key=f"up_{key}", use_container_width=True,
            type="primary" if current == labeling.LABEL_RELEVANT else "secondary",
            help="도움이 되는 기사예요(다시 누르면 취소)",
        ):
            clicked = labeling.LABEL_RELEVANT
        if down_col.button(
            "👎", key=f"down_{key}", use_container_width=True,
            type="primary" if current == labeling.LABEL_IRRELEVANT else "secondary",
            help="도움이 되지 않는 기사예요(다시 누르면 취소)",
        ):
            clicked = labeling.LABEL_IRRELEVANT

        if clicked is not None:
            with st.spinner("저장하는 중…"):
                if clicked == current:
                    labeling.delete_label(conn, url_norm, fixed_keyword["id"])
                else:
                    snapshot = {**a, "source_table": "collected_articles", "url_norm": url_norm}
                    labeling.save_label(conn, fixed_keyword["id"], snapshot, clicked, period_start)
                retrain_result = auto_retrain.maybe_retrain(conn)
            _apply_retrain_feedback(retrain_result)
            st.rerun()

    if visible < len(articles):
        remaining = len(articles) - visible
        if st.button(f"더 보기({min(_FEEDBACK_PAGE_SIZE, remaining)}건 더, 남은 {remaining}건)",
                     key=f"more_{state_key}"):
            st.session_state[state_key] = visible + _FEEDBACK_PAGE_SIZE
            st.rerun()


@st.cache_resource(show_spinner=False)
def _model(_labels: list[dict], cache_key: tuple):
    """라벨에서 만든 분류기를 프로세스에 캐시한다(작업 6).

    파일(models/*.joblib)을 읽지 않는 게 핵심 — 배포 환경의 파일시스템은
    재시작하면 초기화돼서, 학습해 저장해둔 모델이 조용히 사라진다. 라벨은
    DB에 있으므로 그걸 원본으로 삼아 필요할 때 다시 만든다.

    cache_key에 라벨 수와 마지막 라벨 시각이 들어 있어(relevance_model.
    labels_signature) 라벨이 늘거나 수정되면 자동으로 다시 학습한다. 그
    전까지는 재렌더마다 다시 만들지 않는다 — 임베딩 방식은 모델 로드에만
    수십 초가 걸려 매번 하면 화면을 못 쓴다. cache_data가 아니라
    cache_resource인 이유도 같다(학습된 모델은 직렬화 대상이 아니다).
    """
    return relevance_model.build_model(_labels)


@st.cache_data(show_spinner=False)
def _measure(_labels: list[dict], cache_key: tuple) -> list[dict]:
    """채점 결과를 캐시한다. cache_key에 라벨 수·분포를 넣어, 라벨이 늘면
    자동으로 다시 재고 그 전까지는 재렌더마다 다시 학습하지 않게 한다
    (임베딩 방식은 모델 로드에만 수십 초가 걸려 매번 돌리면 못 쓴다).
    _labels는 앞에 밑줄을 붙여 해시 대상에서 제외한다(dict 리스트라 해시 불가)."""
    return relevance_model.evaluate_all(_labels)


def _render_distribution(distribution: dict) -> None:
    st.markdown("**라벨 분포**")
    left, right = st.columns([1, 2])
    left.metric("전체 라벨", f"{distribution['total']}건")
    left.metric("도움됨 비율", f"{distribution['positive_rate']:.0%}")
    right.bar_chart({
        "도움됨": distribution["relevant"],
        "도움 안 됨": distribution["irrelevant"],
    }, horizontal=True)

    if distribution["total"] and distribution["majority_accuracy"] >= 0.8:
        st.warning(
            f"라벨이 한쪽으로 크게 쏠려 있습니다(찍기만 해도 정확도 "
            f"{distribution['majority_accuracy']:.0%}). 이 상태로는 정확도가 착시를 주니 "
            "적은 쪽 라벨을 더 모으고, 아래 Precision·Recall·AUC를 함께 보세요."
        )


def _render_metrics(result: dict, baseline: float) -> None:
    metrics = result["metrics"]
    cv = result["cv"]
    st.caption(
        f"교차검증: {cv['group_kind']} 단위 {cv['n_splits']}-fold({cv['n_groups']}개 그룹) — "
        "학습에 쓰지 않은 조각으로만 채점했습니다."
    )

    row = st.columns(5)
    row[0].metric("Precision", f"{metrics['precision']:.3f}", help="도움됨이라 한 것 중 실제로 도움된 비율")
    row[1].metric("Recall", f"{metrics['recall']:.3f}", help="실제 도움되는 기사 중 찾아낸 비율")
    row[2].metric("F1", f"{metrics['f1']:.3f}", help="Precision과 Recall의 균형")
    row[3].metric("AUC", f"{metrics['auc']:.3f}", help="순위를 매기는 능력. 0.5는 찍기와 같음")
    # 정확도는 반드시 찍기 기준선과 함께 — 단독으로는 쏠린 라벨에서 착시를 준다.
    row[4].metric(
        "정확도", f"{metrics['accuracy']:.3f}",
        delta=f"{metrics['accuracy'] - baseline:+.3f} vs 찍기",
        help=f"무조건 다수 쪽으로 답하는 분류기는 {baseline:.3f}입니다. 이걸 넘어야 의미가 있습니다.",
    )

    ranking = {k: v for k, v in metrics.items() if k.startswith(("precision_at", "ndcg_at"))}
    if ranking:
        st.markdown("**순위 지표** — 시장별로 따로 순위를 매겨 평균낸 값입니다.")
        cols = st.columns(len(ranking))
        for col, (key, value) in zip(cols, ranking.items()):
            col.metric(key.replace("_at_", "@").replace("precision", "Precision").replace("ndcg", "NDCG"),
                       f"{value:.3f}")

    _render_per_market(result.get("per_market", []))


def _render_per_market(per_market: list[dict]) -> None:
    """시장별 분해 — 위의 통합 점수는 3개 시장 평균이라 "교육은 잘 맞히는데
    비즈니스 실적은 못 맞힌다"가 묻힌다. 어느 시장 라벨을 더 모아야 하는지는
    이 표에서만 보인다."""
    if not per_market:
        return

    with st.expander("시장별 성능 (위 점수는 전체 통합값입니다)", expanded=True):
        st.caption(
            "정확도는 그 시장의 '찍기' 기준선과 함께 보세요 — 찍기보다 낮으면 "
            "그 시장은 아직 라벨이 부족하다는 뜻입니다. AUC의 '—'는 그 시장 라벨이 "
            "한쪽 종류뿐이라 계산할 수 없다는 표시입니다(성능이 0이라는 뜻이 아닙니다)."
        )
        rows = []
        for m in per_market:
            gap = m["accuracy"] - m["majority_accuracy"]
            rows.append({
                "시장": m["fixed_keyword"],
                "라벨": f"{m['n_labels']}건",
                "도움됨 비율": f"{m['positive_rate']:.0%}",
                "정확도": f"{m['accuracy']:.3f}",
                "찍기": f"{m['majority_accuracy']:.3f}",
                "찍기 대비": f"{gap:+.3f}",
                "Precision": f"{m['precision']:.3f}",
                "Recall": f"{m['recall']:.3f}",
                "F1": f"{m['f1']:.3f}",
                "AUC": f"{m['auc']:.3f}" if m["auc"] is not None else "—",
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)

        weak = [m["fixed_keyword"] for m in per_market
                if m["accuracy"] <= m["majority_accuracy"]]
        if weak:
            st.warning(
                f"**{', '.join(weak)}** 시장은 찍기 기준선을 넘지 못했습니다. "
                "해당 시장의 라벨을 더 모으면 개선될 가능성이 높습니다."
            )


def _render_performance_tab(conn) -> None:
    st.subheader("📈 성능")
    st.caption(
        "라벨링한 판단을 정답지로 삼아, 분류기가 **처음 보는 기사**를 얼마나 맞히는지 채점합니다. "
        "이번 주 기사 순위 자체는 시장 탭에서 👍/👎를 누를 때마다 "
        f"{auto_retrain.RETRAIN_EVERY_N_LABELS}건마다 자동으로 갱신됩니다 — 여기서는 지금 당장 "
        "정확한 지표(교차검증·시장별 분해)를 보고 싶을 때 눌러 확인하세요."
    )

    labels = labeling.fetch_all_labels(conn)
    distribution = relevance_model.class_distribution(labels)
    _render_distribution(distribution)
    st.divider()

    if distribution["total"] < relevance_model.MIN_LABELS_TO_TRAIN:
        st.info(
            f"성능 측정에는 라벨이 최소 {relevance_model.MIN_LABELS_TO_TRAIN}건 필요합니다 "
            f"(현재 {distribution['total']}건). 아래 시장 탭에서 기사에 👍/👎를 눌러 주세요."
        )
        return

    st.caption(
        "문자 n-gram 방식과 다국어 문장 임베딩 방식을 같은 조건으로 채점해 더 나은 쪽을 저장합니다. "
        "임베딩 방식은 모델을 불러오느라 첫 측정에 1분 정도 걸릴 수 있습니다."
    )
    cache_key = (distribution["total"], distribution["relevant"])
    if st.button("성능 측정하기", type="primary"):
        st.session_state["measured_key"] = cache_key
    # 버튼은 눌린 그 순간에만 True라, 이후 다른 조작으로 재렌더되면 결과가
    # 사라진다. 마지막으로 측정한 조건을 세션에 남겨 계속 보이게 하고,
    # 라벨이 늘어 조건이 달라지면 다시 눌러 재측정하게 한다.
    if st.session_state.get("measured_key") != cache_key:
        if st.session_state.get("measured_key") is not None:
            st.info("라벨이 늘었습니다. 다시 측정하면 갱신된 성능을 볼 수 있습니다.")
        return

    with st.spinner("두 방식을 채점하는 중입니다…"):
        results = _measure(labels, cache_key)

    for result in results:
        st.markdown(f"#### {result['method']}")
        if not result["ok"]:
            st.info(f"측정 불가 — {result['reason']}")
            continue
        _render_metrics(result, distribution["majority_accuracy"])
        st.write("")

    best = results[0]
    if not best.get("ok"):
        return

    if best["metrics"]["accuracy"] <= distribution["majority_accuracy"]:
        st.warning(
            "정확도가 찍기 기준선을 넘지 못했습니다. 아직 라벨이 부족하다는 신호이니 "
            "더 모은 뒤 다시 측정해 주세요. (모델은 저장하지 않았습니다.)"
        )
        return

    with st.spinner("가장 성능이 좋은 방식으로 최종 모델을 학습하는 중입니다…"):
        bundle = _model(labels, cache_key)
    st.success(
        f"**{best['method']}** 방식이 가장 좋았습니다(F1 {best['metrics']['f1']:.3f}). "
        "이 모델은 라벨에서 바로 만들어 메모리에 둡니다 — 파일에 의존하지 않으므로 "
        "배포 환경에서 재시작돼도 라벨만 있으면 그대로 복원됩니다."
    )

    # 모델만 만들고 끝내면 시장 탭은 여전히 옛 순서(또는 최신순)를 보여준다.
    # 라벨링 → 측정 → 목록 갱신이 한 번에 이어지도록 여기서 바로 재판단한다
    # (파이프라인을 다시 돌릴 필요가 없다 — 수집은 그대로 쓰고 점수만 갱신).
    run = dq.get_latest_run(conn)
    if run is None:
        return
    with st.spinner("새 모델로 이번 주 기사 순위를 다시 매기는 중입니다…"):
        judged = judge_all(conn, run["id"], bundle=bundle)
    total = sum(r["judged"] for r in judged)
    st.success(
        f"이번 주 기사 {total}건에 시장별 점수를 다시 매겼습니다. "
        "각 시장 탭의 기사 순서가 갱신됐습니다."
    )


@st.cache_data(show_spinner="기사 임베딩 계산 중… (같은 목록은 처음 검색할 때만, 몇 초 걸립니다)")
def _pool_embeddings(cache_key: tuple, _articles: list[dict]):
    """기사 목록을 문장 임베딩으로 미리 변환해 캐시한다.

    **캐시 키는 run_id가 아니라 기사 URL 튜플이다(2026-08-24 버그 수정)** —
    run_id만으로 캐시하면, 같은 run 안에서도 주차 선택(_select_week)에 따라
    articles 길이가 달라지는 걸 못 잡아서(예: 전체 415건 보다가 특정 주만
    골라 92건이 됨), 이전에 캐시된 415개짜리 임베딩을 그대로 돌려줘 아래
    유사도 계산에서 길이가 안 맞아 IndexError가 났다(실사용 중 발견). URL
    튜플로 잡으면 목록이 바뀔 때마다 정확히 다시 계산된다(articles 자체는
    앞에 밑줄 — 리스트라 해시 불가, dashboard_queries.py의 다른 캐시
    함수들과 같은 관례). 검색할 때마다 다시 인코딩하면 매번 몇 초씩
    걸리는데, 이렇게 하면 같은 목록에 대한 첫 검색에만 비용을 치른다."""
    from tech_monitoring.relevance_model import encode_texts

    texts = [f"{a['title']} {a.get('snippet') or ''}".strip() for a in _articles]
    return encode_texts(texts)


def _search_by_keyword(
    articles: list[dict], query: str, *, top_k: int = 50, min_similarity: float = 0.2,
) -> list[dict] | None:
    """자유 키워드와 뜻이 비슷한 기사를 찾는다(2026-08-24 — 고정 키워드
    드롭다운 대신 도입. market_keywords가 더 이상 안 채워지기도 하고, 담당자
    피드백대로 "정확히 일치 안 해도 관련 있으면" 찾아주는 쪽이 더 유용하다).

    문자열 포함 검사(ILIKE)가 아니라 문장 임베딩 코사인 유사도를 쓴다 —
    "생성형 AI"를 검색해도 "LLM", "챗봇" 같은 다른 표현의 기사까지 걸리게
    하려는 목적이라 정확 일치로는 안 된다. sentence-transformers가 없으면
    (선택 설치, pyproject의 [embedding] extra) None을 돌려줘 호출부가
    "검색 기능을 못 쓴다"고 알리게 한다 — 조용히 빈 결과를 주면 "관련
    기사가 없다"로 오해한다.
    """
    import numpy as np

    try:
        cache_key = tuple(a["url"] for a in articles)
        doc_vecs = _pool_embeddings(cache_key, articles)
        from tech_monitoring.relevance_model import encode_texts

        query_vec = encode_texts([query])[0]
    except ImportError:
        return None

    doc_norms = np.linalg.norm(doc_vecs, axis=1)
    query_norm = np.linalg.norm(query_vec)
    similarities = (doc_vecs @ query_vec) / (doc_norms * query_norm + 1e-9)

    order = np.argsort(-similarities)
    return [articles[i] for i in order if similarities[i] >= min_similarity][:top_k]


def _render_keyword_tab(conn, run_id: int, fixed_keyword: dict, period_start) -> None:
    st.markdown("**주간 이슈 기사**")
    week_start = _select_week(conn, run_id, key=f"articles_week_{fixed_keyword['id']}")

    # 006부터 기사는 시장과 무관한 공용 풀에서 온다.
    articles = dq.get_pool_articles(conn, run_id, fixed_keyword["id"], week_start=week_start)

    query = st.text_input(
        "키워드로 검색(완전히 같은 단어가 아니어도 뜻이 비슷하면 찾아줍니다)",
        key=f"kw_search_{fixed_keyword['id']}",
        placeholder="예: 생성형 AI 도입 사례",
    )
    if query.strip():
        matched = _search_by_keyword(articles, query.strip())
        if matched is None:
            st.info(
                "검색 기능을 쓰려면 문장 임베딩 라이브러리가 필요합니다 — "
                'pip install -e ".[embedding]"'
            )
        else:
            articles = matched
            st.caption(f"'{query.strip()}'와(과) 관련된 기사 {len(articles)}건 · 유사도순")
            _render_feedback_article_list(conn, fixed_keyword, articles, period_start)
            return

    st.caption(f"{len(articles)}건 · 정렬: {dq.describe_ordering(articles)}")
    _render_feedback_article_list(conn, fixed_keyword, articles, period_start)


def main() -> None:
    st.title("AX 시장 모니터링")

    conn = _conn()
    run = dq.get_latest_run(conn)
    _render_run_banner(conn, run)

    fixed_keywords = dq.get_fixed_keywords(conn)
    if not fixed_keywords:
        st.warning(
            "고정 키워드가 없습니다. "
            "`./.venv/Scripts/python.exe scripts/manage_fixed_keywords.py add \"<키워드>\"`로 등록하세요."
        )
        return
    if run is None:
        return

    period_start, _period_end = get_run_period(conn, run["id"])

    # st.tabs가 아니라 segmented_control인 이유(2026-08-24) — st.tabs는 보이지
    # 않는 탭까지 매번 전부 다시 그린다. 기사 목록에 인라인 👍/👎가 붙은 뒤로
    # 시장 하나당 최대 415건×2버튼=830개 위젯이라, 3개 시장을 한 rerun에 동시에
    # 그리면(최대 2490개) 커넥션이 못 버티고 끊겨 마지막 순서 시장이 통째로 안
    # 뜨는 걸 실사용 중 확인했다(교육 탭 — 렌더 순서상 항상 마지막이라 그 여파를
    # 맞았다). segmented_control은 평범한 위젯이라 "선택된 것만" 아래에서 직접
    # if로 분기해 계산하면 실제로는 한 번에 시장 하나 분량만 그려진다 — 화면에
    # 보이는 게 하나뿐이라는 사실과도 맞고, "전체 안 자르고 보여준다" 원칙도
    # 그대로 유지된다(그 하나의 시장 안에서는 여전히 전부 보여준다).
    # 기사 목록이 기본 화면이라 앞에 둔다(2026-08-24 담당자 요청) — 성능은
    # 보조 지표라 뒤로.
    options = [kw["keyword"] for kw in fixed_keywords] + ["📈 성능"]
    selected = st.segmented_control("보기", options, default=options[0], label_visibility="collapsed")
    if not selected:          # single-select라 같은 항목을 다시 누르면 선택 해제된다
        selected = options[0]

    if selected == "📈 성능":
        _render_performance_tab(conn)
    else:
        kw = next(k for k in fixed_keywords if k["keyword"] == selected)
        _render_keyword_tab(conn, run["id"], kw, period_start)


main()
