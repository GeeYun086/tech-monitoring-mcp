"""ax-dashboard 스킬용 데이터 준비 — 숫자 계산은 전부 여기서 끝낸다.

배경: 첫 데모(2026-08-07)를 만들 때 impact_signals의 가중치×값을 손으로
계산해서 HTML에 박아 넣었다. 데이터가 바뀔 때마다 다시 계산해야 하고
실수하기 쉬워 재사용이 안 됐다(.claude/skills/ax-dashboard/SKILL.md 참고).
이 스크립트가 계산을 전담하고, Claude는 번역·레이아웃 판단만 한다.

get_weekly_digest()가 이미 정리한 응답에 두 가지만 더한다:
1. impact_breakdown_pct — 4개 신호(source_trust·aggregator_signal·
   cluster_size·recency)의 가중 기여도를 백분율로 정규화. config.py의
   실제 가중치를 그대로 읽으므로 가중치가 바뀌어도 이 스크립트는 안 바뀐다.
2. keywords — 필터를 통과한 실제 기사 제목·요약에서 뽑은 빈도 상위 단어.
   관련도를 좁히는 필터가 아니라 "발견된 주제어" 참고 표시용이다.

    ./.venv/Scripts/python.exe scripts/dashboard_data.py --period last_week --limit 20
"""

import argparse
import json
import re
import sys
from collections import Counter

from tech_monitoring.config import settings
from tech_monitoring.mcp_server import queries

# 흔한 영어 기능어만 거른다 — 도메인 키워드를 임의로 편집하지 않기 위해
# 최소한으로 유지한다(이 목록이 길어지면 사실상 키워드 필터가 되어버린다).
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for",
    "with", "at", "by", "from", "up", "about", "into", "over", "after",
    "is", "are", "was", "were", "be", "been", "being", "as", "it", "its",
    "this", "that", "these", "those", "has", "have", "had", "will", "would",
    "can", "could", "not", "no", "than", "then", "so", "if", "how", "what",
    "who", "which", "new", "says", "said", "more", "out", "now", "just",
}
_WORD_RE = re.compile(r"[A-Za-z가-힣][A-Za-z가-힣'\-]{1,}")
_URL_RE = re.compile(r"https?://\S+")
# hnrss처럼 본문이 없는 소스는 summary가 "Article URL: ... Comments URL: ...
# Points: N # Comments: M" 같은 구조적 메타데이터뿐이다(rss.py의 parse_hn_points가
# 파싱해 impact_signals.hn_points로 이미 뽑아간 바로 그 텍스트). 이건 기사 내용이
# 아니라 라벨이므로 키워드 후보에서 제외한다 — 안 그러면 "URL"·"Comments"·
# "Points" 같은 게 실제 주제어 자리를 차지한다(실측: 상위 10개 중 8개가 이거였음).
_HN_METADATA_RE = re.compile(
    r"Article URL:|Comments URL:|Points:\s*\d+|#\s*Comments:\s*\d+", re.IGNORECASE
)


def _clean_for_keywords(text: str) -> str:
    text = _URL_RE.sub(" ", text)
    text = _HN_METADATA_RE.sub(" ", text)
    return text


def _weights() -> dict[str, float]:
    return {
        "trust": settings.weight_source_trust,
        "agg": settings.weight_aggregator_signal,
        "cluster": settings.weight_cluster_size,
        "recency": settings.weight_recency,
    }


def compute_breakdown_pct(signals: dict) -> dict[str, float]:
    """impact_signals → 4개 신호의 가중 기여도 백분율. 합이 100에 가깝다
    (반올림 오차 제외 — impact_score 자체가 이 4개의 가중합이므로)."""
    w = _weights()
    raw = {
        "trust": (signals.get("source_trust") or 0.0) * w["trust"],
        "agg": (signals.get("aggregator_signal") or 0.0) * w["agg"],
        "cluster": (signals.get("cluster_size") or 0.0) * w["cluster"],
        "recency": (signals.get("recency") or 0.0) * w["recency"],
    }
    total = sum(raw.values())
    if total <= 0:
        return {k: 0.0 for k in raw}
    return {k: round(v / total * 100, 1) for k, v in raw.items()}


def extract_keywords(clusters: list[dict], top_n: int = 15) -> list[dict]:
    """대표 기사(lead)의 제목·요약에서 빈도 상위 단어 추출. 클러스터 하나당
    한 번만 세어(같은 이슈의 반복 기사에 중복 가중되지 않게) 실제 이슈 분포를 반영한다."""
    counter: Counter[str] = Counter()
    for cluster in clusters:
        lead = cluster.get("lead") or {}
        text = _clean_for_keywords(f"{lead.get('title') or ''} {lead.get('summary') or ''}")
        seen_in_this_cluster = set()
        for match in _WORD_RE.findall(text):
            lower = match.lower()
            if lower in _STOPWORDS or len(lower) < 2 or lower in seen_in_this_cluster:
                continue
            seen_in_this_cluster.add(lower)
            counter[match] += 1
    return [{"word": word, "count": count} for word, count in counter.most_common(top_n)]


def build(period: str = "last_week", limit: int = 20, min_impact: float = 0.0) -> dict:
    digest = queries.get_weekly_digest(period=period, limit=limit, min_impact=min_impact)

    for cluster in digest["clusters"]:
        signals = cluster["lead"].get("impact_signals") or {}
        cluster["lead"]["impact_breakdown_pct"] = compute_breakdown_pct(signals)

    digest["keywords"] = extract_keywords(digest["clusters"])
    return digest


if __name__ == "__main__":
    # Windows 콘솔 코드페이지(cp949 등)로 리다이렉트하면 em dash 같은 문자에서
    # UnicodeEncodeError가 난다. 어떤 환경에서 실행되든 안전하게 UTF-8로 낸다.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", default="last_week")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-impact", type=float, default=0.0, dest="min_impact")
    args = parser.parse_args()

    print(json.dumps(build(args.period, args.limit, args.min_impact), ensure_ascii=False, indent=2))
