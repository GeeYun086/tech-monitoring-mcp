"""구(phrase) 후보 추출 + TF-IDF 랭킹 — 원래 scripts/dashboard_data.py(v1)에서
실사용 검증까지 마친 로직을 공유 유틸로 뽑아낸 것(2026-08-13, v2 피벗 중 추출).

뽑아낸 이유: v1(RSS 수집 + 룰/임베딩 필터링)이 폐기되면서 dashboard_data.py는
언젠가 정리·삭제될 예정인데, 이 파일 안의 텍스트 처리 로직(불용어 제거·구
후보 생성·TF-IDF 랭킹)은 v2(analysis/keyword_extraction.py)에서도 그대로
필요하다. dashboard_data.py에서 직접 import하면 v1 정리 시 v2가 같이
깨지므로, 의존 방향을 뒤집어 여기 공용 유틸로 두고 양쪽이 이걸 참조한다.

핵심 교훈(v1 실사용 검증, 2026-08-11): 단어 하나(unigram) 빈도만 쓰면 "AI"·
"모델" 같은 최상위 개념어가 항상 상위를 차지한다. 구(1~2단어) 후보 +
TF-IDF(로그감쇠 TF × 문서빈도 기반 IDF)로 바꾸면 이 문제가 풀리지만, 이건
**영어처럼 단어가 공백으로 이미 분리된 텍스트에서만** 잘 작동한다 — 한국어는
형태소 분석기 없이는 "기술을"·"모델을"처럼 조사가 붙은 채로 별도 토큰이 되고,
TF-IDF의 "너무 흔하지도 드물지도 않은 중간 빈도 우대" 특성과 만나면 이런
조사 결합형이 대거 상위권을 차지해버린다(실사용 검증으로 확인). 그래서
호출하는 쪽이 텍스트 성격(한국어 비중)에 따라 count_keywords(원시 빈도)와
tfidf_rank(구+TF-IDF) 중 하나를 선택해 쓰는 게 안전하다 — 이 모듈은 두
경로를 모두 제공하고, 어느 쪽을 쓸지는 호출자가 판단한다.
"""

import math
import re
from collections import Counter

# 한국어는 조사·어미가 규칙 기반으로 안 걸러져서(형태소 분석기 없음)
# 영어 불용어보다 더 대략적이다 — 뉴스 기사에 흔한 보도체 표현만 최소한으로 거른다.
KOREAN_STOPWORDS = {
    "이번", "관련", "위해", "통해", "대한", "있다", "했다", "한다", "된다",
    "것으로", "밝혔다", "전했다", "지난", "올해", "대해", "이라고", "라며",
    "따르면", "이날", "가운데", "예정이다", "있는", "하는", "된", "등",
    "위한", "실제", "직접", "새로운", "기존", "특히", "최근", "넘어",
    "아니라", "주요", "기반", "것이", "같은",
    # 형태소 분석 없이 정규식 토큰화만 해서 "AI"에 조사가 붙은 채로 별도
    # 단어처럼 집계되는 문제가 특히 자주 나오는 조합 몇 개는 직접 걸러낸다.
    "AI가", "AI는", "AI를", "AI도", "AI와", "AI의", "AI에",
}

# 흔한 영어 기능어만 거른다 — 도메인 키워드를 임의로 편집하지 않기 위해
# 최소한으로 유지한다(대명사·전치사·조동사 같은 순수 문법 기능어만).
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for",
    "with", "at", "by", "from", "up", "about", "into", "over", "after",
    "is", "are", "was", "were", "be", "been", "being", "as", "it", "its",
    "this", "that", "these", "those", "has", "have", "had", "will", "would",
    "can", "could", "not", "no", "than", "then", "so", "if", "how", "what",
    "who", "which", "new", "says", "said", "more", "out", "now", "just",
    "we", "they", "their", "them", "our", "your", "you", "he", "she", "his",
    "her", "there", "here", "when", "where", "why", "all", "also", "some",
    "any", "each", "other", "such", "most", "many", "much", "through",
    "across", "first", "one", "two", "three", "get", "gets", "getting",
    "make", "makes", "making", "like", "still", "even", "while", "using",
    "use", "used",
    # arXiv 논문 초록류 서술체 표현("we show that...", "however, ...") —
    # 내용 있는 명사가 아니라 논문 특유의 문장 골격이라 함께 제외한다.
    "however", "show", "shows", "shown", "showed", "demonstrate",
    "demonstrates", "demonstrated", "appear", "appears", "appeared",
    "remain", "remains", "remained", "under", "only", "paper", "papers",
}

_WORD_RE = re.compile(r"[A-Za-z가-힣][A-Za-z가-힣'\-]{1,}")
_URL_RE = re.compile(r"https?://\S+")
# hnrss처럼 본문이 없는 소스는 summary가 "Article URL: ... Points: N # Comments: M"
# 같은 구조적 메타데이터뿐이다. v2 검색결과 스니펫엔 보통 안 나오지만, 이런
# 텍스트가 섞여 들어와도 조용히 걸러지도록 v1과 동일하게 남겨둔다(부작용 없음).
_HN_METADATA_RE = re.compile(
    r"Article URL:|Comments URL:|Points:\s*\d+|#\s*Comments:\s*\d+", re.IGNORECASE
)

PHRASE_MAX_N = 2  # 3단어 이상은 기사마다 표현이 제각각이라 재사용도가 낮음(노이즈에 가까움)


def clean_for_keywords(text: str) -> str:
    text = _URL_RE.sub(" ", text)
    text = _HN_METADATA_RE.sub(" ", text)
    return text


def filtered_words(text: str) -> list[str]:
    """불용어·URL·HN 메타데이터를 걸러낸 단어를 원문 순서 그대로 나열한다
    (중복 제거 안 함 — n-gram을 만들려면 인접 관계가 필요하다)."""
    cleaned = clean_for_keywords(text)
    words: list[str] = []
    for raw_match in _WORD_RE.findall(cleaned):
        # 아포스트로피·하이픈이 단어 중간 문자로 허용돼 있어 "AI's" 뒤에 온점·
        # 따옴표가 곧장 붙으면 "AI'"처럼 꼬리에 구두점만 남은 조각이 매치될 수
        # 있다 — 앞뒤 아포스트로피·하이픈은 잘라내고 판단한다.
        match = raw_match.strip("'-")
        if not match:
            continue
        lower = match.lower()
        if lower in STOPWORDS or match in KOREAN_STOPWORDS or len(lower) < 2:
            continue
        words.append(match)
    return words


def tokens(text: str) -> set[str]:
    """텍스트 한 건에서 걸러진 단어의 집합(중복 제거, 유니그램만)."""
    return set(filtered_words(text))


def phrase_candidates(text: str, max_n: int = PHRASE_MAX_N) -> set[str]:
    """유니그램에 더해 1~max_n개짜리 연속 구(phrase)까지 후보로 만든다(기사 한
    건 안 중복은 한 번만). 불용어를 먼저 걷어낸 뒤 이어붙이므로 원문에서
    불용어가 중간에 끼어 있어도("AI 이번 모델") 의미상 이어지는 표현("AI 모델")이
    바이그램으로 잡힌다."""
    words = filtered_words(text)
    candidates: set[str] = set()
    for n in range(1, max_n + 1):
        for i in range(len(words) - n + 1):
            candidates.add(" ".join(words[i:i + n]))
    return candidates


def count_keywords(texts: list[str], top_n: int) -> list[dict]:
    """텍스트 목록에서 빈도 상위 단어를 뽑는다(원시 빈도 — TF-IDF 아님). 형태소
    분석 없는 한국어처럼 TF-IDF의 중간빈도 우대가 조사 결합형을 상위로 밀어
    올리는 텍스트에 적합하다. 텍스트 하나 안 중복은 한 번만 센다."""
    counter: Counter[str] = Counter()
    for text in texts:
        counter.update(tokens(text))
    return [{"word": word, "count": count} for word, count in counter.most_common(top_n)]


def tfidf_rank(term_sets: list[set[str]], top_n: int) -> list[dict]:
    """로그 감쇠 TF(sublinear TF) × IDF(문서빈도 기반 희소성 가중치)로 순위를
    매긴다. term_sets는 문서(기사) 하나당 등장한 term의 집합 목록 — 이미 문서
    내 중복은 제거된 상태라고 가정한다(tokens/phrase_candidates가 그렇게 만듦).

    단순 `count × idf`가 아니라 TF에 로그 감쇠(`1 + ln(count)`)를 적용해야
    한다 — term_sets가 문서당 집합이라 tf(전체 등장 횟수)와 df(등장 문서 수)가
    항상 같은 값이 되므로, 감쇠 없이는 `count × idf`가 사실상 `df × idf`가
    되어 df가 큰(=아주 흔한) 항목이 로그 스케일인 idf보다 선형으로 커서
    여전히 이긴다. 로그 감쇠를 적용하면 "적당히 자주 나오지만 모든 문서에
    있진 않은" 중간 대역에서 최고점을 찍고 양극단(너무 흔함/너무 드묾) 둘 다
    하위권으로 밀린다(v1 dashboard_data.py 실사용 검증, 2026-08-11)."""
    n_docs = len(term_sets) or 1
    tf: Counter[str] = Counter()
    df: Counter[str] = Counter()
    for terms in term_sets:
        tf.update(terms)
        df.update(terms)  # terms가 이미 set이므로 문서당 1회만 반영됨
    scored = []
    for term, count in tf.items():
        idf = math.log((n_docs + 1) / (df[term] + 1)) + 1
        tf_weight = 1 + math.log(count) if count > 0 else 0.0
        scored.append({
            "word": term, "count": count, "doc_freq": df[term],
            "score": round(tf_weight * idf, 2),
        })
    scored.sort(key=lambda r: -r["score"])
    return scored[:top_n]
