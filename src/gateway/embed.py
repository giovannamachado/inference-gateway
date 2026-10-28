"""Embedder stateless por hashing de n-gramas de caracteres (sem treino, sem rede).

Substituível por qualquer objeto com `encode(text) -> np.ndarray` L2-normalizado
(ex.: sentence-transformers). O limiar do cache semântico depende do embedder: recalibre ao trocar.
"""
import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer


class HashingEmbedder:
    def __init__(self, dim: int = 2048):
        self.dim = dim
        self._vec = HashingVectorizer(n_features=dim, analyzer="char_wb", ngram_range=(3, 5), alternate_sign=False,
                                      norm="l2", lowercase=True, strip_accents="unicode")

    def encode(self, text: str) -> np.ndarray:
        return self._vec.transform([text]).toarray().ravel().astype(np.float32)
