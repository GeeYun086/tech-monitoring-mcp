"""기사 모니터링 Streamlit 대시보드(2026-08-25부로 "AX 시장 모니터링"에서
개명 — 특정 팀 전용 이름이 아니라 어느 팀이 포크해 써도 맞는 이름으로).

계산(랭킹·필터링)은 전부 tech_monitoring.dashboard_queries가 한다 — 여기는
레이아웃만 담당한다(v1 ax-dashboard 스킬에서 이어받은 원칙, SKILL.md는
삭제됐지만 "숫자 계산은 스크립트가, 화면은 레이아웃만" 이유는 그대로 유효).

**한 배포 = 팀 하나(2026-08-25 재설계)**: 화면에서 팀을 추가하던 "➕ 새 팀"
탭은 없앴다. 대신 배포하기 **전에** `scripts/manage_fixed_keywords.py`로
팀 이름·검색어·사이트를 미리 정해두면(README "다른 팀이 독립적으로
배포하기" 참고), 그 설정 그대로 매주 자동 수집(pipeline_v2 →
collectors.search_engine.collect_all)이 돌고 이 화면은 그 결과만 보여준다.
다른 팀은 이 레포·DB·배포 링크를 통째로 새로 만들어 쓴다 — 한 링크에
접속한 사람들은 전부 같은 팀의 기사만 본다.

화면 구성(2026-08-24 대개편 — 마켓 3개 분할 제거):
    1. "<팀 이름>" 탭(기본 화면, fixed_keywords.keyword가 그대로 탭 이름이
       된다) — 이번 주 공용 기사 풀 전체를 보여준다(006, top20 등으로 안
       자르는 원칙은 그대로, 2026-08-13 담당자 확인).
       **마켓(고정 키워드) 3개로 나눠 보던 걸 없앴다** — 수집 자체가 애초에
       시장과 무관한 공용 풀이라(collectors/search_engine.py의 "공용 기사
       풀" 방식) 3개로 쪼개 보는 게 의미 없다는 담당자 판단. 활성
       fixed_keywords는 이제 딱 1개만 두는 게 이 배포의 팀을 뜻한다(위
       "한 배포 = 팀 하나" 참고).
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

import secrets

import psycopg
import streamlit as st

from tech_monitoring import auto_retrain
from tech_monitoring import dashboard_queries as dq
from tech_monitoring import labeling
from tech_monitoring import relevance_model
from tech_monitoring.analysis.relevance_filter import judge_all
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import get_run_period
from tech_monitoring.utils.url_normalize import normalize_url

st.set_page_config(page_title="기사 모니터링", layout="wide")

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


def _live_conn():
    # 2026-08-27 재현 — Supabase 풀러(특히 무료 티어 트랜잭션 모드)가 유휴
    # 커넥션을 끊으면, cache_resource가 붙잡고 있던 죽은 커넥션 객체가 그대로
    # 재사용돼 "the connection is closed"/"server closed the connection
    # unexpectedly"가 새로고침해도 계속 반복됐다. 매 rerun 진입 시 살아있는지
    # 확인하고, 죽었으면 캐시를 비운 뒤 새로 연결한다.
    conn = _conn()
    if conn.closed:
        _conn.clear()
        conn = _conn()
    return conn


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

    **익명 좋아요(2026-08-24 담당자 결정)**: 누가 눌렀는지 남기지 않고
    개수만 쌓이길 원해서, 클릭할 때마다 무작위 토큰을 labeled_by로 발급한다
    (실명·계정 불필요). 화면에 "지금 상태"(버튼 강조 표시)를 보여주려면
    그래도 "내가 이 기사에 어떤 토큰으로 눌렀는지"는 기억해야 하는데, DB
    조회로는 알 수 없다(토큰이 매번 다르니까) — 그래서 st.session_state에
    (시장, url_norm) -> {label, token}으로 세션 동안만 기억한다. 브라우저를
    새로고침하거나 다른 사람이 누르면 새 토큰이 발급돼 별개의 좋아요로
    쌓인다 — 로그인 없는 위젯이 흔히 감수하는 오차다(labeling.
    fetch_like_counts 헤더 참고). 같은 세션에서 같은 버튼을 다시 누르면
    취소(delete_label), 반대 버튼을 누르면 뒤집힌다(save_label의 UPSERT를
    같은 토큰으로 다시 불러 그대로 덮어쓴다).

    좋아요/싫어요 개수는 dashboard_queries.get_pool_articles가 이미
    article마다 like_count/dislike_count로 붙여서 준다(사람 구분 없이
    전체 집계) — 버튼 라벨에 그 숫자를 그대로 보여준다.

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

    # (fixed_keyword_id, url_norm) -> {"label": ..., "token": ...} — 이번
    # 브라우저 세션 동안만 "내가 누른 것"을 기억한다(위 함수 docstring 참고).
    my_votes = st.session_state.setdefault("my_votes", {})

    for a in articles[:visible]:
        url_norm = normalize_url(a["url"])
        vote_key = (fixed_keyword["id"], url_norm)
        my_vote = my_votes.get(vote_key)
        current = my_vote["label"] if my_vote else None

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

        # 버튼 라벨에 개수를 그대로 보여준다 — 0건이면 굳이 "0"을 안 붙인다.
        like_count = a.get("like_count") or 0
        dislike_count = a.get("dislike_count") or 0
        up_label = f"👍 {like_count}" if like_count else "👍"
        down_label = f"👎 {dislike_count}" if dislike_count else "👎"

        # key에 url_norm을 넣어 기사가 바뀌면 버튼도 새 위젯이 되게 한다 —
        # 같은 key를 재사용하면 Streamlit이 이전 클릭 상태를 물려받는다.
        key = f"{fixed_keyword['id']}_{url_norm}"
        clicked = None
        if up_col.button(
            up_label, key=f"up_{key}", use_container_width=True,
            type="primary" if current == labeling.LABEL_RELEVANT else "secondary",
            help="도움이 되는 기사예요(다시 누르면 취소)",
        ):
            clicked = labeling.LABEL_RELEVANT
        if down_col.button(
            down_label, key=f"down_{key}", use_container_width=True,
            type="primary" if current == labeling.LABEL_IRRELEVANT else "secondary",
            help="도움이 되지 않는 기사예요(다시 누르면 취소)",
        ):
            clicked = labeling.LABEL_IRRELEVANT

        if clicked is not None:
            with st.spinner("저장하는 중…"):
                if clicked == current:
                    # 취소 — 이번 세션에서 이 기사에 쓴 그 토큰만 지운다.
                    labeling.delete_label(conn, url_norm, fixed_keyword["id"], labeled_by=my_vote["token"])
                    del my_votes[vote_key]
                else:
                    # 새 좋아요면 이번에 새 무작위 토큰을 발급하고, 반대
                    # 버튼으로 뒤집는 거면 방금 그 토큰을 그대로 재사용해
                    # save_label의 UPSERT가 같은 행을 덮어쓰게 한다.
                    token = my_vote["token"] if my_vote else secrets.token_hex(8)
                    snapshot = {**a, "source_table": "collected_articles", "url_norm": url_norm}
                    labeling.save_label(
                        conn, fixed_keyword["id"], snapshot, clicked, period_start, labeled_by=token,
                    )
                    my_votes[vote_key] = {"label": clicked, "token": token}
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

    # ALL_LABELERS: 인라인 버튼이 세션마다 무작위 labeled_by를 새로 발급하므로
    # (2026-08-24, 익명 좋아요 개수 집계 — _render_feedback_article_list 참고)
    # 기본값(설정값 하나)으로 좁히면 새로 쌓이는 라벨이 다 안 잡힌다.
    labels = labeling.fetch_all_labels(conn, labeled_by=labeling.ALL_LABELERS)
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


def _search_by_keyword(
    articles: list[dict], query: str, *, top_k: int = 50, min_similarity: float = 0.05,
) -> list[dict]:
    """문자 n-gram TF-IDF 코사인 유사도로 기사를 찾는다(2026-08-25, 두 번째
    되돌림 — 단순 포함 검색 다음 단계).

    **문장 임베딩(sentence-transformers)은 여전히 안 쓴다** — 배포 환경엔
    일부러 안 깔려 있다(torch 527MB+모델 458MB가 무료 티어엔 너무 커서,
    requirements.txt 헤더 참고). 그 대신 relevance_model.py가 분류기 학습에
    이미 쓰는 것과 같은 방식(문자 2~4-gram TF-IDF, 형태소 분석기 없이 한국어
    처리)을 재사용한다 — scikit-learn은 이미 필수 의존성이라 배포 환경에서도
    항상 동작한다.

    **한계**: "생성형 AI"로 검색해도 "LLM"·"챗봇" 같은 **다른 단어를 쓴**
    기사는 못 찾는다(진짜 동의어 매칭은 임베딩이 있어야 가능). 대신 단순
    포함 검색과 달리 문구가 정확히 안 겹쳐도 **일부 단어(문자열)만 겹쳐도**
    유사도 순위에 낀다 — 완전 일치보다는 넓게, 임베딩보다는 좁게 찾는
    중간 지점이다."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    texts = [f"{a['title']} {a.get('snippet') or ''}".strip() for a in articles]
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
    doc_vecs = vectorizer.fit_transform(texts)
    query_vec = vectorizer.transform([query])

    similarities = (doc_vecs @ query_vec.T).toarray().ravel()
    order = similarities.argsort()[::-1]
    return [articles[i] for i in order if similarities[i] >= min_similarity][:top_k]


def _render_keyword_tab(conn, run_id: int, fixed_keyword: dict, period_start) -> None:
    st.markdown("**주간 이슈 기사**")

    # 006부터 기사는 시장과 무관한 공용 풀에서 온다. 주차 선택 드롭다운은
    # 없앴다(2026-08-24 담당자 결정) — 이제 항상 딱 한 주만 존재해서(db/
    # weekly_run.py, 매주 전체 wipe 후 직전 완료된 한 주만 재수집) 고를 게
    # 없다.
    articles = dq.get_pool_articles(conn, run_id, fixed_keyword["id"])

    query = st.text_input(
        "키워드로 검색",
        key=f"kw_search_{fixed_keyword['id']}",
        placeholder="예: 생성형 AI",
    )
    if query.strip():
        articles = _search_by_keyword(articles, query.strip())
        st.caption(f"'{query.strip()}'와(과) 유사한 기사 {len(articles)}건")
        _render_feedback_article_list(conn, fixed_keyword, articles, period_start)
        return

    st.caption(f"{len(articles)}건 · 정렬: {dq.describe_ordering(articles)}")
    _render_feedback_article_list(conn, fixed_keyword, articles, period_start)


def _render_first_run_setup(conn) -> None:
    """첫 실행 화면(2026-08-25) — 팀 이름·검색어·사이트를 딱 한 번 여기서
    정한다. `fixed_keywords`가 비어있을 때만 나타나고, 한 번 설정하고 나면
    다시 안 뜬다 — "한 배포 = 팀 하나"라 팀을 더 추가하는 화면은 없다.
    다른 팀은 이 저장소를 통째로 포크해 따로 배포한다(README "다른 팀이
    독립적으로 배포하기" 참고). 배포하는 사람은 `.env`/Streamlit Cloud
    Secrets에 `DATABASE_URL`·`TAVILY_API_KEY`만 넣고 이 화면을 열면 된다 —
    CLI를 몰라도 여기서 전부 끝난다.

    제출하면 팀을 등록하고 **그 자리에서 곧바로 첫 수집까지 돌린다**
    (pipeline_v2.run_pipeline을 그대로 재사용 — 매주 자동 수집(GitHub
    Actions)과 완전히 같은 경로를 타서 "수동 첫 실행"과 "자동 주간 수집"이
    다른 코드로 갈라지지 않는다). 최초 실행도 직전 완료된 1주만 걷는다
    (2026-08-27부로 3주 부트스트랩 없앰 — db/weekly_run.BOOTSTRAP_WEEKS
    참고). 그래도 사이트·검색어 수에 따라 몇 분 걸릴 수 있다."""
    from tech_monitoring.collectors.search_engine import KOREAN_DOMAINS, SITE_DOMAINS, SITE_NAMES

    st.subheader("처음 오셨네요 — 팀을 설정해주세요")
    st.caption(
        "여기서 정한 이름·검색어·사이트로 앞으로 매주 자동 수집됩니다. "
        "이 화면은 지금 한 번만 나오고, 완료되면 이 팀의 기사 목록이 기본 화면이 됩니다 — "
        "이 링크에 접속하는 팀원은 전부 같은 기사·좋아요를 보게 됩니다."
    )

    name = st.text_input("팀 이름", key="setup_team_name", placeholder="예: 콘텐츠팀")
    ko_terms = st.text_input(
        "한국어 검색어(쉼표로 구분)", key="setup_team_ko", placeholder="예: 에듀테크, AI 튜터",
    )
    en_terms = st.text_input(
        "영어 검색어(쉼표로 구분)", key="setup_team_en", placeholder="예: edtech AI, AI tutoring",
    )
    site_options = {d: SITE_NAMES.get(d, d) for d in SITE_DOMAINS}
    selected_sites = st.multiselect(
        "검색할 사이트(최소 1곳)",
        options=list(site_options.keys()),
        format_func=lambda d: site_options[d],
        key="setup_team_sites",
    )

    if st.button("시작하기", type="primary"):
        ko_list = [t.strip() for t in ko_terms.split(",") if t.strip()]
        en_list = [t.strip() for t in en_terms.split(",") if t.strip()]
        # 국내/해외 사이트를 하나라도 골랐는데 그 언어 검색어가 비어있으면
        # _terms_for_domain이 넓은 질의로 폴백해 팀 색깔이 안 드러난다 —
        # 설정 단계에서 미리 막는다(실사용 중 발견, 2026-08-25).
        has_korean_site = any(d in KOREAN_DOMAINS for d in selected_sites)
        has_english_site = any(d not in KOREAN_DOMAINS for d in selected_sites)
        if not name.strip():
            st.error("팀 이름을 입력하세요.")
        elif not selected_sites:
            st.error("사이트를 최소 1곳 선택하세요.")
        elif not ko_list and not en_list:
            st.error("검색어를 최소 1개 입력하세요.")
        elif has_korean_site and not ko_list:
            st.error("국내 사이트를 고르셨으니 한국어 검색어도 입력하세요 — 비워두면 넓은 질의로 대체돼 팀 색깔이 안 드러납니다.")
        elif has_english_site and not en_list:
            st.error("해외 사이트를 고르셨으니 영어 검색어도 입력하세요 — 비워두면 넓은 질의로 대체돼 팀 색깔이 안 드러납니다.")
        else:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fixed_keywords
                        (keyword, display_order, active, search_terms_ko, search_terms_en, site_domains)
                    VALUES (%s, 0, TRUE, %s, %s, %s)
                    """,
                    (name.strip(), ko_list, en_list, selected_sites),
                )
            with st.spinner("첫 수집 중입니다… 사이트·검색어 수에 따라 몇 분 걸릴 수 있습니다"):
                from tech_monitoring.pipeline_v2 import run_pipeline
                report = run_pipeline()
            if report["failed"]:
                st.error(
                    f"'{name.strip()}' 팀은 등록됐지만 수집 중 일부 실패했습니다: {report['failed']} — "
                    "DATABASE_URL·TAVILY_API_KEY를 확인한 뒤 새로고침하면 다시 시도할 수 있습니다."
                )
            else:
                st.success(f"'{name.strip()}' 팀이 설정됐고 첫 수집도 끝났습니다.")
            st.rerun()


def _render_settings_tab(conn, fixed_keyword: dict) -> None:
    """검색어·수집 사이트 조회·변경 화면(2026-08-27 추가). 예전엔
    scripts/manage_fixed_keywords.py CLI로만 가능했는데, 담당자가 CLI 없이
    화면에서 직접 확인·조정하고 싶어해서 추가했다.

    여기서 저장한 값은 fixed_keywords에 바로 반영되고, **다음 자동 수집
    (매주 월요일 GitHub Actions, 또는 다음 수동 파이프라인 실행)부터
    적용된다** — pipeline_v2가 실행되는 그 시점의 DB 값을 그대로 읽어
    쓰기 때문에(collectors/search_engine.py), 코드 재배포가 필요 없다.
    이번 주 이미 수집된 기사·라벨은 건드리지 않는다(fixed_keywords는 매주
    wipe 대상이 아닌 설정값 테이블 — README "스키마" 표 참고).
    팀 이름 자체(keyword 컬럼)는 라벨·분류기 점수와 연결된 식별자라 여기서는
    안 바꾼다 — 이름 변경은 지금처럼 scripts/manage_fixed_keywords.py rename
    으로만 한다."""
    from tech_monitoring.collectors.search_engine import SITE_DOMAINS, SITE_NAMES

    st.subheader("⚙️ 설정")
    st.caption(
        f"'{fixed_keyword['keyword']}' 팀의 검색어·수집 사이트입니다. "
        "여기서 바꾼 값은 **다음 자동 수집(매주 월요일)부터** 반영되고, "
        "이번 주 이미 수집된 기사는 그대로 유지됩니다."
    )

    ko_current = ", ".join(fixed_keyword.get("search_terms_ko") or [])
    en_current = ", ".join(fixed_keyword.get("search_terms_en") or [])
    sites_current = fixed_keyword.get("site_domains") or []

    ko_terms = st.text_input(
        "한국어 검색어(쉼표로 구분)", value=ko_current, key="settings_ko",
        help="비워두면 넓은 질의(\"AI\"/\"인공지능\")로 폴백합니다.",
    )
    en_terms = st.text_input(
        "영어 검색어(쉼표로 구분)", value=en_current, key="settings_en",
        help="비워두면 넓은 질의(\"AI\")로 폴백합니다.",
    )
    site_options = {d: SITE_NAMES.get(d, d) for d in SITE_DOMAINS}
    selected_sites = st.multiselect(
        "수집할 사이트(비우면 전체 화이트리스트)",
        options=list(site_options.keys()),
        format_func=lambda d: site_options[d],
        default=[d for d in sites_current if d in site_options],
        key="settings_sites",
    )

    if st.button("저장", type="primary"):
        ko_list = [t.strip() for t in ko_terms.split(",") if t.strip()]
        en_list = [t.strip() for t in en_terms.split(",") if t.strip()]
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE fixed_keywords SET search_terms_ko = %s, search_terms_en = %s, "
                "site_domains = %s WHERE keyword = %s",
                (ko_list, en_list, selected_sites or None, fixed_keyword["keyword"]),
            )
        st.success("저장했습니다. 다음 자동 수집(매주 월요일)부터 반영됩니다.")
        st.rerun()


def main() -> None:
    st.title("기사 모니터링")

    conn = _live_conn()
    run = dq.get_latest_run(conn)
    _render_run_banner(conn, run)

    fixed_keywords = dq.get_fixed_keywords(conn)
    if not fixed_keywords:
        _render_first_run_setup(conn)
        return
    if run is None:
        st.info("아직 수집된 데이터가 없습니다. 첫 수집은 파이프라인을 한 번 실행해야 합니다.")
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
    # 보조 지표라 뒤로. "한 배포 = 팀 하나"(2026-08-25)로 fixed_keywords는
    # 보통 1개뿐이라 사실상 "<팀 이름>"/"📈 성능" 두 탭만 남는다 — 화면에서
    # 팀을 더 추가하는 기능은 없다(README "다른 팀이 독립적으로 배포하기").
    options = [kw["keyword"] for kw in fixed_keywords] + ["📈 성능", "⚙️ 설정"]
    selected = st.segmented_control("보기", options, default=options[0], label_visibility="collapsed")
    if not selected:          # single-select라 같은 항목을 다시 누르면 선택 해제된다
        selected = options[0]

    if selected == "📈 성능":
        _render_performance_tab(conn)
    elif selected == "⚙️ 설정":
        # "한 배포 = 팀 하나"라 fixed_keywords[0]이 곧 이 배포의 팀이다
        # (_render_settings_tab 함수 docstring 참고).
        _render_settings_tab(conn, fixed_keywords[0])
    else:
        kw = next(k for k in fixed_keywords if k["keyword"] == selected)
        _render_keyword_tab(conn, run["id"], kw, period_start)


main()
