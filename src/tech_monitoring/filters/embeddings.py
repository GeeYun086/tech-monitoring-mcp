import os
from functools import lru_cache

from tech_monitoring.config import settings

MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_DIM = 1024


@lru_cache(maxsize=1)
def get_model():
    # settings.hf_hub_offline 참고: HF Hub 버전 확인 네트워크 요청을 끄지 않으면
    # MCP stdio 호출이 그 요청 지연/행에 그대로 물려 타임아웃난다. sentence_transformers를
    # import하기 전에 환경변수를 세팅해야 huggingface_hub가 이를 읽는다.
    if settings.hf_hub_offline:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    # BGE-M3 기본 max_seq_length(8192)는 CPU에서 지나치게 느림.
    # 기사 제목+본문 앞부분만 쓰므로 512 토큰이면 충분.
    model.max_seq_length = 512
    return model


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_model()
    vectors = model.encode(texts, normalize_embeddings=True)
    return vectors.tolist()
