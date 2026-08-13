"""이번 주 검색결과(search_results)에서 주요 키워드 후보를 뽑는다 — v2
"이번 주 주요 키워드" 파이프라인의 코드 담당 단계(카운팅은 항상 코드,
동의어 병합만 Gemini가 한다는 원칙의 앞부분. 뒷부분인 Gemini 동의어
병합은 이 모듈이 아니라 다음 단계에서 처리한다).

핵심 재사용: utils/keyword_text.py의 구(phrase)+TF-IDF 로직 — v1
dashboard_data.py가 실사용 검증까지 마친 것을 그대로 쓴다("AI"·"모델" 같은
최상위 개념어만 상위를 차지하는 문제를 이미 풀어본 코드).

**국내/해외 비대칭 처리를 여기서도 유지한다.** v1은 이걸 소스 이름
목록(DOMESTIC_SOURCES)으로 판단했지만, v2의 큐레이션 검색엔진은 사이트에
국내/해외 라벨을 붙여두지 않았으므로 텍스트 자체(한글 비중)로 판단한다.
이유는 v1과 동일하다 — 한국어는 형태소 분석기 없이 조사가 붙은 채로
토큰화되는데("기술을"·"모델을"), TF-IDF의 "너무 흔하지도 드물지도 않은
중간 빈도 우대" 특성과 만나면 이런 조사 결합형이 상위권을 차지해버린다
(v1 dashboard_data.py 실사용 검증, 2026-08-11). 그래서 한글이 우세한
기사는 원시 빈도(count_keywords)로, 그 외(주로 영문)는 구+TF-IDF
(phrase_candidates + tfidf_rank)로 별도 채점한다.

    ./.venv/Scripts/python.exe -m tech_monitoring.analysis.keyword_extraction
"""

import re

from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import get_active_fixed_keywords
from tech_monitoring.utils.keyword_text import count_keywords, phrase_candidates, tfidf_rank

_HANGUL_RE = re.compile(r"[가-힣]")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")

# 버킷별 후보 개수. 둘을 하나로 합쳐 등수를 매기지 않는다 — count(원시 빈도)와
# tfidf_score(로그감쇠 TF×IDF)는 척도가 달라 직접 비교할 근거가 없다. 다음
# 단계(Gemini 동의어 병합)가 두 목록을 합쳐서 받는다.
CANDIDATES_PER_BUCKET = 30


def _article_text(row: dict) -> str:
    return f"{row.get('title') or ''} {row.get('snippet') or ''}"


def _is_korean_heavy(text: str) -> bool:
    """한글 문자 수가 영문 알파벳 수보다 많으면 한국어 텍스트로 본다. v1은
    소스 이름(DOMESTIC_SOURCES)으로 국내/해외를 나눴지만, v2 검색결과엔
    그런 라벨이 없어(큐레이션 사이트 목록 자체에 국내/해외 구분이 없음)
    텍스트로 직접 판단한다. 동률(둘 다 0 포함)이면 global 취급 — 실질적으로
    영문 처리(구+TF-IDF)가 한글이 섞이지 않은 텍스트에 안전한 기본값이다."""
    hangul_count = len(_HANGUL_RE.findall(text))
    ascii_count = len(_ASCII_LETTER_RE.findall(text))
    return hangul_count > ascii_count


def fetch_search_results(conn, run_id: int, fixed_keyword_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT title, snippet, source_domain FROM search_results "
            "WHERE run_id = %s AND fixed_keyword_id = %s",
            (run_id, fixed_keyword_id),
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def extract_candidates(rows: list[dict], top_n: int = CANDIDATES_PER_BUCKET) -> list[dict]:
    """search_results 행 목록(제목+스니펫) → 후보 키워드 목록.

    반환 형식은 버킷(한국어/그 외)에 상관없이 동일하게 맞춘다: phrase,
    doc_count(정확한 등장 문서 수 — 이 단계는 항상 코드가 셈), tfidf_score
    (한국어 버킷은 TF-IDF를 안 쓰므로 None), method(디버깅·다음 단계 참고용).
    """
    korean_texts: list[str] = []
    global_texts: list[str] = []
    for row in rows:
        text = _article_text(row)
        (korean_texts if _is_korean_heavy(text) else global_texts).append(text)

    candidates: list[dict] = []

    for entry in count_keywords(korean_texts, top_n):
        candidates.append({
            "phrase": entry["word"], "doc_count": entry["count"],
            "tfidf_score": None, "method": "frequency",
        })

    term_sets = [phrase_candidates(text) for text in global_texts]
    for entry in tfidf_rank(term_sets, top_n):
        candidates.append({
            "phrase": entry["word"], "doc_count": entry["doc_freq"],
            "tfidf_score": entry["score"], "method": "tfidf",
        })

    return candidates


def extract_candidates_for_keyword(conn, run_id: int, fixed_keyword_id: int) -> list[dict]:
    rows = fetch_search_results(conn, run_id, fixed_keyword_id)
    return extract_candidates(rows)


def extract_all(conn, run_id: int) -> dict[int, list[dict]]:
    """이번 run의 모든 활성 고정 키워드에 대해 후보를 뽑는다.
    {fixed_keyword_id: [후보, ...]} 형태 — 다음 단계(Gemini 동의어 병합)가
    고정 키워드별로 독립 처리하므로 이 경계를 유지한다."""
    return {
        kw["id"]: extract_candidates_for_keyword(conn, run_id, kw["id"])
        for kw in get_active_fixed_keywords(conn)
    }


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    _conn = get_connection()
    try:
        with _conn.cursor() as _cur:
            _cur.execute("SELECT id FROM weekly_runs ORDER BY id DESC LIMIT 1")
            _row = _cur.fetchone()
        if _row is None:
            print("weekly_runs가 비어 있음 — 먼저 collectors.search_engine을 실행할 것")
        else:
            _run_id = _row[0]
            for _kw_id, _candidates in extract_all(_conn, _run_id).items():
                print(f"--- fixed_keyword_id={_kw_id} ({len(_candidates)}건) ---")
                for _c in _candidates[:15]:
                    print(f"  {_c['phrase']!r:30} doc_count={_c['doc_count']:>3} "
                          f"tfidf={_c['tfidf_score']} ({_c['method']})")
    finally:
        _conn.close()
