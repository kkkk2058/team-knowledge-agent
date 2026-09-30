"""벡터 검색: multilingual-e5-small (서비스와 같은 모델, 384차원).

- 질의에는 `query: `, 문서에는 `passage: ` 접두어를 붙인다. 빼먹으면 조용히 품질이 떨어진다
  (모델 카드에 명시, implementation.md ④).
- 임베딩은 SQLite 파일(설정의 index_path)에 글 내용의 해시로 저장해 다시 쓴다. 같은 글이면
  청크 크기·실험이 바뀌어도 다시 계산하지 않는다. 캐시는 커밋하지 않는다(data/).
- SQLite 연결은 읽고 쓸 때마다 짧게 연다. MCP 서버는 도구 함수를 다른 스레드에서 부르는데
  sqlite3 연결은 만든 스레드에서만 쓸 수 있다.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from functools import cached_property
from pathlib import Path

import numpy as np

MODEL = "intfloat/multilingual-e5-small"
BATCH = 32


class Embedder:
    def __init__(self, cache_path: Path | None, model_name: str = MODEL) -> None:
        self.model_name = model_name
        self.cache_path = cache_path
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(cache_path)) as db, db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS embeddings ("
                    " model TEXT, text_hash TEXT, dim INTEGER, vector BLOB,"
                    " PRIMARY KEY (model, text_hash))"
                )

    @cached_property
    def model(self):
        from sentence_transformers import SentenceTransformer  # 무거워서 쓸 때만 불러온다

        try:
            # 받아 둔 모델이 있으면 허브에 확인 요청을 보내지 않는다 (처음 검색이 20초 → 수 초)
            return SentenceTransformer(self.model_name, local_files_only=True)
        except OSError:
            return SentenceTransformer(self.model_name)

    def passages(self, texts: list[str]) -> np.ndarray:
        return self._encode([f"passage: {t}" for t in texts])

    def query(self, text: str) -> np.ndarray:
        return self._encode([f"query: {text}"])[0]

    def _encode(self, texts: list[str]) -> np.ndarray:
        hashes = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
        found = self._load(hashes)
        missing = [i for i, h in enumerate(hashes) if h not in found]
        if missing:
            vectors = self.model.encode(
                [texts[i] for i in missing],
                batch_size=BATCH,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            new = {hashes[i]: v.astype(np.float32) for i, v in zip(missing, vectors, strict=True)}
            self._save(new)
            found.update(new)
        return np.stack([found[h] for h in hashes])

    def _load(self, hashes: list[str]) -> dict[str, np.ndarray]:
        if self.cache_path is None or not hashes:
            return {}
        found = {}
        with closing(sqlite3.connect(self.cache_path)) as db:
            for start in range(0, len(hashes), 500):
                part = hashes[start : start + 500]
                rows = db.execute(
                    f"SELECT text_hash, vector FROM embeddings WHERE model = ? AND text_hash IN "
                    f"({','.join('?' * len(part))})",
                    [self.model_name, *part],
                )
                found.update({h: np.frombuffer(v, dtype=np.float32) for h, v in rows})
        return found

    def _save(self, vectors: dict[str, np.ndarray]) -> None:
        if self.cache_path is None:
            return
        with closing(sqlite3.connect(self.cache_path)) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?)",
                [(self.model_name, h, len(v), v.tobytes()) for h, v in vectors.items()],
            )
