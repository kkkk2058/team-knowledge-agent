"""청크 검색. 키워드(BM25)·벡터(e5)·둘을 합친 RRF.

RRF: 문서 점수 = Σ 가중치 / (60 + 순위). 점수 크기가 다른 두 결과를 순위만으로 합친다
(Cormack et al. 2009). 서비스 ① 검색은 키워드 3 : 벡터 1 가중 RRF다(결정 로그 09-23).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tka.index.keyword import BM25, TOKENIZERS
from tka.index.vector import Embedder
from tka.ingest.chunk import Chunk

RRF_K = 60
POOL = 100  # RRF에 넣을 각 결과의 앞쪽 개수


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class SearchIndex:
    """청크 목록 하나에 대한 키워드·벡터 색인.

    text_mode가 context면 제목 경로·문서 안내를 붙인 글(Chunk.context_text)을,
    body면 본문만 색인한다.
    """

    def __init__(
        self,
        chunks: list[Chunk],
        embedder: Embedder | None,
        *,
        tokenizer: str = "kiwi",
        text_mode: str = "context",
    ) -> None:
        if text_mode not in ("context", "body"):
            raise ValueError(f"text_mode는 context 또는 body다: {text_mode!r}")
        if tokenizer not in TOKENIZERS:
            raise ValueError(f"tokenizer는 {', '.join(TOKENIZERS)} 중 하나다: {tokenizer!r}")
        self.chunks = chunks
        self.embedder = embedder
        self.tokenizer = tokenizer
        self.text_mode = text_mode
        texts = [c.context_text() if text_mode == "context" else c.text for c in chunks]
        self._tokenize = TOKENIZERS[tokenizer]
        self._bm25 = BM25([self._tokenize(t) for t in texts])
        self._vectors = embedder.passages(texts) if embedder else None
        self._position = {id(c): i for i, c in enumerate(chunks)}

    def keyword(self, query: str, k: int = 10) -> list[Hit]:
        return self._top(self._bm25.scores(self._tokenize(query)), k)

    def vector(self, query: str, k: int = 10) -> list[Hit]:
        if self._vectors is None or self.embedder is None:
            raise ValueError("임베더 없이 만든 색인이라 벡터 검색을 할 수 없다")
        return self._top(self._vectors @ self.embedder.query(query), k)

    def hybrid(self, query: str, k: int = 10, weights: tuple[float, float] = (1, 1)) -> list[Hit]:
        """RRF. weights = (키워드, 벡터)."""
        rankings = [self.keyword(query, POOL), self.vector(query, POOL)]
        fused: dict[int, float] = {}
        for weight, hits in zip(weights, rankings, strict=True):
            for rank, hit in enumerate(hits, 1):
                i = self._position[id(hit.chunk)]
                fused[i] = fused.get(i, 0.0) + weight / (RRF_K + rank)
        order = sorted(fused, key=lambda i: -fused[i])[:k]
        return [Hit(self.chunks[i], fused[i]) for i in order]

    def _top(self, scores: np.ndarray, k: int) -> list[Hit]:
        if not len(scores):
            return []
        order = np.argsort(-scores, kind="stable")[:k]
        return [Hit(self.chunks[i], float(scores[i])) for i in order if scores[i] > 0]
