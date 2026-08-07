from functools import lru_cache

MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_DIM = 1024


@lru_cache(maxsize=1)
def get_model():
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
