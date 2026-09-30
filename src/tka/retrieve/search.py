"""청크 검색. 키워드(BM25)·벡터(e5)·둘을 합친 RRF.

RRF: 문서 점수 = Σ 가중치 / (60 + 순위). 점수 크기가 다른 두 결과를 순위만으로 합친다
(Cormack et al. 2009). 서비스 ① 검색은 키워드 3 : 벡터 1 가중 RRF다(결정 로그 09-23).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tka.config import Config, Source
from tka.decisions.table import DecisionTable
from tka.index.keyword import BM25, TOKENIZERS
from tka.index.vector import Embedder
from tka.ingest.chunk import Chunk, chunk_files
from tka.ingest.fetch import source_dir
from tka.ingest.files import select_files

RRF_K = 60
POOL = 100  # RRF에 넣을 각 결과의 앞쪽 개수


@dataclass(frozen=True)
class SearchSettings:
    tokenizer: str = "kiwi"
    text_mode: str = "context"
    max_chars: int = 900
    weights: tuple[float, float] = (3, 1)  # RRF (키워드, 벡터)
    # 결정 로그를 표째 자르지 않고 행 하나를 검색 단위 하나로 넣는다. 여러 행이 한 청크면
    # 한 행짜리 답이 묻힌다.
    decision_rows: bool = True
    # 설정의 별칭 묶음(③·챗봇, ④·피드·feed)을 키워드 검색에도 쓴다. 재 보니 recall@5는 같고
    # 근거 전부가 한 문항 줄어(10 → 9/18) 기본으로 쓰지 않는다 (2026-09-30).
    aliases: bool = False

    def label(self) -> str:
        kw, vec = self.weights
        rows = " · 결정 행 단위" if self.decision_rows else ""
        aliases = " · 별칭" if self.aliases else ""
        return f"RRF {kw:g}:{vec:g} · {self.tokenizer} · 청크 {self.max_chars}자{rows}{aliases}"


# 골든셋(튜닝용) recall@5가 가장 높은 방식: 94% (17/18), eval/results/2026-09-30-retrieval
DEFAULT_SETTINGS = SearchSettings()


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
        aliases: Sequence[Sequence[str]] = (),
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
        self._base_tokenize = TOKENIZERS[tokenizer]
        self._aliases = [tuple(term.lower() for term in group) for group in aliases]
        self._bm25 = BM25([self._tokenize(t) for t in texts])
        self._vectors = embedder.passages(texts) if embedder else None
        self._position = {id(c): i for i, c in enumerate(chunks)}

    def keyword(self, query: str, k: int = 10) -> list[Hit]:
        return self._top(self._bm25.scores(self._tokenize(query)), k)

    def _tokenize(self, text: str) -> list[str]:
        """토큰 + 별칭 묶음 토큰. 별칭 묶음(③·챗봇·…)의 말이 하나라도 있으면 같은 토큰을 더한다.

        Kiwi는 ③④ 같은 기호를 버리고, 문서는 "③④가"로 질문은 "챗봇(③)·피드(④)"로 쓰는 일이
        많다(g19). 별칭 토큰으로 둘을 잇는다.
        """
        tokens = self._base_tokenize(text)
        lowered = text.lower()
        tokens += [f"@별칭{i}" for i, g in enumerate(self._aliases) if any(t in lowered for t in g)]
        return tokens

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


def build_index(
    config: Config,
    root: Path,
    source: Source,
    embedder: Embedder | None,
    settings: SearchSettings = DEFAULT_SETTINGS,
    table: DecisionTable | None = None,
) -> SearchIndex:
    """받아 둔 소스(기준 커밋)의 문서를 골라 자르고 색인한다.

    table을 주고 settings.decision_rows면 결정 로그 표 청크를 결정 행 청크로 바꾼다.
    """
    files = select_files(config, root, source)
    _, chunks = chunk_files(files, source_dir(config, root, source), settings.max_chars)
    if settings.decision_rows and table is not None:
        chunks = with_decision_rows(chunks, table)
    return SearchIndex(
        chunks,
        embedder,
        tokenizer=settings.tokenizer,
        text_mode=settings.text_mode,
        aliases=config.aliases if settings.aliases else (),
    )


def with_decision_rows(chunks: list[Chunk], table: DecisionTable) -> list[Chunk]:
    """결정 로그 표가 든 청크를 빼고 결정 행 청크를 넣는다. 표 밖(요약 문단)은 남긴다."""
    rows = {d.line for d in table.decisions}
    kept = [
        c
        for c in chunks
        if not (c.path == table.log_path and any(c.start_line <= n <= c.end_line for n in rows))
    ]
    return kept + decision_chunks(table, doc_title=_log_title(chunks, table))


def decision_chunks(table: DecisionTable, doc_title: str = "결정 로그") -> list[Chunk]:
    by_line = {d.line: d for d in table.decisions}
    out = []
    for d in table.decisions:
        parts = [f"{d.date} {d.part} 결정: {d.text}"]
        if d.affected:
            parts.append(f"영향 파트: {', '.join(d.affected)}")
        if d.detail_path or d.detail_note:
            parts.append(f"상세: {d.detail_title or ''} {d.detail_path or d.detail_note}".strip())
        if d.replaces:
            parts.append(f"이전 결정: {d.replaces}")
        if d.superseded_by in by_line:
            newer = by_line[d.superseded_by]
            parts.append(f"이 결정은 {newer.date} 결정으로 대체됐다: {newer.text}")
        out.append(
            Chunk(
                repo=table.repo,
                commit=table.commit,
                path=table.log_path,
                doc_title=doc_title,
                heading_path=(f"{d.date} {d.part}", d.status),
                start_line=d.line,
                end_line=d.line,
                text=". ".join(parts),
                callout=None,
                oversized=False,
                in_details=False,
                kind="decision",
            )
        )
    return out


def _log_title(chunks: list[Chunk], table: DecisionTable) -> str:
    return next((c.doc_title for c in chunks if c.path == table.log_path), "결정 로그")
