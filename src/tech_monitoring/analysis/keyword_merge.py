"""후보 키워드(analysis/keyword_extraction.py)를 Gemini로 동의어 병합해
market_keywords 최종 테이블을 확정한다 — "이번 주 주요 키워드" 파이프라인의
마지막 단계.

원칙(여러 차례 논의 끝에 확정, 2026-08-13): **카운팅은 항상 코드, 의미
판단(동의어 여부)만 Gemini.** Gemini에게 원본 기사나 숫자 계산을 시키지
않는다 — 후보 phrase 목록(이미 코드가 정확히 센 것)만 보여주고 "같은
의미인 것끼리 그룹으로 묶어달라"고만 요청한다. Gemini가 그룹을 지어주면,
코드가 그 그룹에 속한 변형 표기들의 **문서 집합 합집합**으로 최종 doc_count를
재계산한다(단순 합산이 아님 — 한 기사에 "OpenAI"와 "오픈AI"가 같이 나올
수 있어 합산하면 중복 계산된다).

환각 방지: Gemini 응답에서 원본 후보 목록에 없는 phrase는 전부 버린다
(parse_and_validate_groups). 그룹에 못 들어간 후보는 자기 자신만 있는
단독 그룹으로 남겨 어떤 후보도 조용히 사라지지 않게 한다.

    ./.venv/Scripts/python.exe -m tech_monitoring.analysis.keyword_merge
"""

import json

from tech_monitoring.analysis.keyword_extraction import (
    build_term_sets,
    extract_candidates,
    fetch_search_results,
)
from tech_monitoring.config import settings
from tech_monitoring.db.connection import get_connection
from tech_monitoring.db.weekly_run import get_active_fixed_keywords

_PROMPT_TEMPLATE = """다음은 "{fixed_keyword}" 시장 관련 이번 주 기사에서 뽑은 후보 키워드 목록이다.

같은 대상(기업·제품·인물·사건 등)을 표기만 다르게 쓴 것들을 하나의 그룹으로 묶어라.
예: "OpenAI", "오픈AI", "오픈 ai"는 같은 대상이므로 한 그룹.

규칙:
- 반드시 아래 목록에 있는 phrase만 사용한다. 목록에 없는 새 표기를 만들어내지 않는다.
- 각 phrase는 정확히 하나의 그룹에만 속해야 한다.
- 같은 의미의 표기가 없는 phrase도 자기 자신만 있는 그룹으로 포함시킨다(빠뜨리지 말 것).
- 그룹 개수나 등장 횟수는 세지 않는다 — 오직 그룹핑만 한다.

후보 목록:
{phrase_list}

아래 JSON 형식으로만 답하라(설명 문장 없이):
{{"groups": [{{"canonical_phrase": "대표 표기", "variant_phrases": ["표기1", "표기2"]}}, ...]}}
"""


def build_prompt(fixed_keyword: str, phrases: list[str]) -> str:
    phrase_list = "\n".join(f"- {p}" for p in phrases)
    return _PROMPT_TEMPLATE.format(fixed_keyword=fixed_keyword, phrase_list=phrase_list)


def call_gemini(prompt: str) -> str:
    """실제 Gemini 호출 — 테스트에서는 이 함수 전체를 monkeypatch로 대체한다
    (google-genai SDK·네트워크·API 키에 의존하지 않게)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return response.text


def parse_and_validate_groups(raw_json: str, valid_phrases: set[str]) -> list[dict]:
    """Gemini 응답을 파싱하고 환각(원본에 없는 phrase)·중복 배정을 걸러낸다.

    - JSON 파싱 실패 → 빈 리스트(호출자가 전부 단독 그룹으로 폴백하게 함).
    - variant_phrases 중 valid_phrases에 없는 것은 버린다.
    - 이미 다른 그룹에 배정된 phrase가 또 나오면 먼저 나온 그룹 것으로 취급.
    - 걸러낸 뒤 variant가 하나도 안 남은 그룹은 통째로 버린다.
    - canonical_phrase 자체가 유효하지 않으면(원본에 없거나 이미 다른 그룹행)
      남은 variant 중 하나를 대표로 대신 쓴다 — 대표 표기도 반드시 실제
      후보여야 한다는 원칙(환각 방지)이 canonical에도 똑같이 적용된다.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []

    groups = data.get("groups", []) if isinstance(data, dict) else []
    seen_variants: set[str] = set()
    result = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        variants = group.get("variant_phrases") or []
        valid_variants = [v for v in variants if v in valid_phrases and v not in seen_variants]
        if not valid_variants:
            continue
        canonical = group.get("canonical_phrase")
        if canonical not in valid_variants:
            canonical = valid_variants[0]
        seen_variants.update(valid_variants)
        result.append({"canonical_phrase": canonical, "variant_phrases": valid_variants})
    return result


def add_ungrouped_singletons(groups: list[dict], all_phrases: list[str]) -> list[dict]:
    """Gemini가 그룹에 넣지 않은(또는 검증 과정에서 걸러진) 후보를 자기
    자신만 있는 단독 그룹으로 추가한다 — 어떤 후보도 조용히 사라지면 안 된다."""
    grouped = {v for g in groups for v in g["variant_phrases"]}
    for phrase in all_phrases:
        if phrase not in grouped:
            groups.append({"canonical_phrase": phrase, "variant_phrases": [phrase]})
            grouped.add(phrase)
    return groups


def compute_merged_stats(
    group: dict, term_sets: list[set[str]], candidates_by_phrase: dict[str, dict],
) -> dict:
    """그룹의 최종 doc_count(합집합)·tfidf_score(변형들 중 최댓값)를 계산한다.
    doc_count는 합산이 아니라 합집합 — 한 문서가 여러 변형을 동시에 포함해도
    한 번만 센다."""
    variants = set(group["variant_phrases"])
    doc_count = sum(1 for terms in term_sets if terms & variants)
    scores = [
        candidates_by_phrase[v]["tfidf_score"]
        for v in variants
        if candidates_by_phrase.get(v, {}).get("tfidf_score") is not None
    ]
    return {
        "canonical_phrase": group["canonical_phrase"],
        "variant_phrases": sorted(variants),
        "doc_count": doc_count,
        "tfidf_score": max(scores) if scores else None,
    }


def merge_candidates_for_keyword(conn, run_id: int, fixed_keyword: dict) -> list[dict]:
    """고정 키워드 하나에 대해 후보 추출 → Gemini 그룹핑 → 카운트 재계산까지 전부 수행."""
    rows = fetch_search_results(conn, run_id, fixed_keyword["id"])
    candidates = extract_candidates(rows)
    if not candidates:
        return []

    candidates_by_phrase = {c["phrase"]: c for c in candidates}
    all_phrases = list(candidates_by_phrase.keys())
    term_sets = build_term_sets(rows)

    prompt = build_prompt(fixed_keyword["keyword"], all_phrases)
    raw_response = call_gemini(prompt)
    groups = parse_and_validate_groups(raw_response, valid_phrases=set(all_phrases))
    groups = add_ungrouped_singletons(groups, all_phrases)

    return [compute_merged_stats(g, term_sets, candidates_by_phrase) for g in groups]


def _save_market_keywords(conn, run_id: int, fixed_keyword_id: int, merged: list[dict]) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for row in merged:
            cur.execute(
                """
                INSERT INTO market_keywords
                    (run_id, fixed_keyword_id, canonical_phrase, variant_phrases, doc_count, tfidf_score)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, fixed_keyword_id, canonical_phrase) DO NOTHING
                RETURNING id
                """,
                (
                    run_id, fixed_keyword_id, row["canonical_phrase"],
                    row["variant_phrases"], row["doc_count"], row["tfidf_score"],
                ),
            )
            if cur.fetchone() is not None:
                inserted += 1
    return inserted


def run_for_all_keywords(conn, run_id: int) -> list[dict]:
    results = []
    for kw in get_active_fixed_keywords(conn):
        merged = merge_candidates_for_keyword(conn, run_id, kw)
        inserted = _save_market_keywords(conn, run_id, kw["id"], merged)
        results.append({"fixed_keyword": kw["keyword"], "groups": len(merged), "inserted": inserted})
    return results


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
            for _result in run_for_all_keywords(_conn, _row[0]):
                print(_result)
    finally:
        _conn.close()
