"""uv run python -m tka.retrieve eval | search "질문" [--k 5]

eval     방식별 recall@5를 재서 eval/results/<날짜>-retrieval/에 남긴다
search   기본 방식(DEFAULT)으로 찾아 본다
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from tka.config import Config, load_config
from tka.evaluation import dump_yaml
from tka.golden import load_golden
from tka.index.vector import MODEL, Embedder
from tka.ingest.chunk import Chunk, chunk_files
from tka.ingest.fetch import FetchError, source_dir
from tka.ingest.files import select_files
from tka.retrieve.evaluate import (
    K,
    Variant,
    config_hash,
    render_report,
    run,
    searcher,
    summarize,
    targets,
)
from tka.retrieve.search import SearchIndex

VARIANTS = [
    Variant("키워드 · 공백", "keyword", tokenizer="words"),
    Variant("키워드 · 글자 조각(2·3)", "keyword", tokenizer="ngram"),
    Variant("키워드 · Kiwi 형태소", "keyword", tokenizer="kiwi"),
    Variant("벡터 · e5-small", "vector"),
    Variant("RRF 1:1 · Kiwi", "hybrid", tokenizer="kiwi", weights=(1, 1)),
    Variant("RRF 3:1 · Kiwi", "hybrid", tokenizer="kiwi", weights=(3, 1)),
    Variant("RRF 1:1 · 글자 조각", "hybrid", tokenizer="ngram", weights=(1, 1)),
    Variant("RRF 3:1 · 글자 조각", "hybrid", tokenizer="ngram", weights=(3, 1)),
    # 끄고 켜기: 청크 앞에 붙인 제목 경로·문서 안내
    Variant("벡터 · 본문만", "vector", text_mode="body"),
    Variant("RRF 1:1 · Kiwi · 본문만", "hybrid", text_mode="body"),
    # 청크 크기: 1500자면 16%가 e5의 512토큰을 넘는다
    Variant("벡터 · 청크 900자", "vector", max_chars=900),
    Variant("키워드 · Kiwi · 청크 900자", "keyword", max_chars=900),
    Variant("RRF 1:1 · Kiwi · 청크 900자", "hybrid", max_chars=900),
    Variant("RRF 3:1 · Kiwi · 청크 900자", "hybrid", max_chars=900, weights=(3, 1)),
]
# 골든셋(튜닝용) recall@5가 가장 높은 방식 (eval/results/2026-09-30-retrieval)
DEFAULT = VARIANTS[-1]


class Indexes:
    """(청크 크기, 토크나이저, 글 방식)마다 색인을 한 번만 만든다."""

    def __init__(self, config: Config, root: Path, with_vectors: bool = True) -> None:
        self.config, self.root = config, root
        self.source = config.sources[0]
        self.embedder = Embedder(root / config.index_path) if with_vectors else None
        self._chunks: dict[int, list[Chunk]] = {}
        self._indexes: dict[tuple[int, str, str], SearchIndex] = {}

    def chunks(self, max_chars: int) -> list[Chunk]:
        if max_chars not in self._chunks:
            files = select_files(self.config, self.root, self.source)
            _, chunks = chunk_files(
                files, source_dir(self.config, self.root, self.source), max_chars
            )
            self._chunks[max_chars] = chunks
        return self._chunks[max_chars]

    def get(self, v: Variant) -> SearchIndex:
        key = (v.max_chars, v.tokenizer, v.text_mode)
        if key not in self._indexes:
            self._indexes[key] = SearchIndex(
                self.chunks(v.max_chars),
                self.embedder,
                tokenizer=v.tokenizer,
                text_mode=v.text_mode,
            )
        return self._indexes[key]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.retrieve", description="검색")
    parser.add_argument("command", choices=("eval", "search"))
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--k", type=int, default=K)
    parser.add_argument(
        "--out", type=Path, help="eval 결과 폴더. 기본: eval/results/<오늘>-retrieval"
    )
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--golden", type=Path, default=Path("eval/golden.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    config = load_config(args.config)
    indexes = Indexes(config, args.root)
    try:
        if args.command == "search":
            if not args.query:
                parser.error("search에는 질문이 필요하다")
            for rank, hit in enumerate(
                searcher(indexes.get(DEFAULT), DEFAULT)(args.query, args.k), 1
            ):
                c = hit.chunk
                title = " > ".join((c.doc_title, *c.heading_path))
                print(f"{rank}. {c.id}  {title}")
                print(f"   {' '.join(c.text.split())[:140]}")
            return 0
        return _eval(args, config, indexes)
    except FetchError as e:
        print(f"멈춤 — {e}", file=sys.stderr)
        return 1


def _eval(args, config: Config, indexes: Indexes) -> int:
    golden = load_golden(args.golden)
    runs = []
    for v in VARIANTS:
        results = run(indexes.get(v), v, golden)
        s = summarize(results)
        print(
            f"{v.name:<28} recall@{K} {s.recall():<12} 전부 {s.full_recall():<12} MRR {s.mrr:.2f}"
        )
        runs.append((v, results))

    best = max(runs, key=lambda r: _rank_key(summarize(r[1])))[0].name
    titles = {
        c.id: " > ".join((c.doc_title, *c.heading_path))
        for n in {v.max_chars for v in VARIANTS}
        for c in indexes.chunks(n)
    }
    meta = {
        "날짜": date.today().isoformat(),
        "소스": f"{indexes.source.repo_name} @ {golden.source_commits[indexes.source.repo_name]}",
        "평가 문항": f"골든셋 v0 중 근거가 있는 {len(targets(golden))}문항 (튜닝용)",
        "임베딩 모델": MODEL,
        "설정 해시": config_hash(args.config, args.root / config.decisions.corrections)
        if config.decisions and config.decisions.corrections
        else config_hash(args.config),
        "이 레포 커밋": _repo_head(args.root),
    }
    out = args.out or args.root / "eval" / "results" / f"{date.today().isoformat()}-retrieval"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(
        render_report(runs, meta=meta, titles=titles, best=best), "utf-8"
    )
    detail = {
        "meta": meta,
        "runs": [
            {
                "variant": v.__dict__ | {"weights": list(v.weights)},
                "items": [r.__dict__ | {"top": list(r.top)} for r in results],
            }
            for v, results in runs
        ],
    }
    (out / "runs.yaml").write_text(dump_yaml(detail), encoding="utf-8")
    print(f"\n가장 좋은 방식: {best}\n결과: {out}/summary.md")
    return 0


def _rank_key(s):
    return (s.hits, s.full, s.mrr)


def _repo_head(root: Path) -> str:
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return f"{head}{' + 커밋 안 한 변경' if dirty else ''}"


if __name__ == "__main__":
    sys.exit(main())
