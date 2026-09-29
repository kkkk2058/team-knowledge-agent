"""uv run python -m tka.ingest fetch|files|chunks [--source 이름] [--list]

fetch    소스 레포를 캐시에 받아 기준 커밋으로 맞춘다
files    포함 규칙으로 고른 파일 수 (--list로 목록)
chunks   고른 파일을 청크로 잘라 JSONL로 덤프하고 통계를 보여준다 (--list로 청크 제목)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from tka.config import Source, load_config
from tka.ingest.chunk import (
    MAX_CHARS,
    Chunk,
    ChunkError,
    Document,
    chunk_files,
    coverage_problems,
)
from tka.ingest.fetch import FetchError, fetch_source, source_dir
from tka.ingest.files import SourceFile, count_by_rule, select_files


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.ingest", description="소스 수집")
    parser.add_argument("command", choices=("fetch", "files", "chunks"))
    parser.add_argument("--source", help="이 소스만 (설정의 name)")
    parser.add_argument("--list", action="store_true", help="파일 목록·청크 제목을 모두 보여준다")
    parser.add_argument("--out", type=Path, default=Path("data/chunks.jsonl"), help="chunks 덤프")
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."), help="레포 루트")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    sources = [s for s in config.sources if not args.source or s.name == args.source]
    if not sources:
        parser.error(f"설정에 없는 소스다: {args.source}")

    all_chunks: list[Chunk] = []
    for source in sources:
        try:
            if args.command == "fetch":
                fetched = fetch_source(config, args.root, source)
                print(
                    f"{source.name}: {fetched.action} → {fetched.commit[:7]} ({fetched.directory})"
                )
                continue
            files = select_files(config, args.root, source)
            if args.command == "files":
                _print_files(source, files, args.list)
                continue
            directory = source_dir(config, args.root, source)
            docs, chunks = chunk_files(files, directory)
            gaps = [
                f"{d.file.path}: {p}"
                for d in docs
                for p in coverage_problems(
                    (directory / d.file.path).read_text(encoding="utf-8"),
                    [c for c in chunks if c.path == d.file.path],
                )
            ]
        except (FetchError, ChunkError) as e:
            print(f"{source.name}: 멈춤 — {e}", file=sys.stderr)
            return 1
        _print_chunks(source, docs, chunks, args.list)
        print(f"  빠지거나 겹친 본문 줄: {len(gaps)}개")
        for g in gaps[:20]:
            print(f"    {g}")
        all_chunks += chunks

    if args.command == "chunks":
        out = args.root / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for c in all_chunks:
                f.write(json.dumps({"id": c.id, **asdict(c)}, ensure_ascii=False) + "\n")
        print(f"덤프: {out} ({len(all_chunks)}개, 원문 일부가 들어 있어 커밋하지 않는다)")
    return 0


def _print_files(source: Source, files: list[SourceFile], show_list: bool) -> None:
    commit = files[0].commit[:7] if files else source.ref
    print(f"{source.name} @ {commit}: {len(files)}개")
    for rule, count in count_by_rule(files, source).items():
        warning = "  ← 0개. 규칙이 맞는지 확인한다" if count == 0 else ""
        print(f"  {rule:<55} {count:>3}{warning}")
    if show_list:
        for f in files:
            print(f"    {f.path}")


def _print_chunks(source: Source, docs: list[Document], chunks: list[Chunk], show: bool) -> None:
    sizes = [len(c.text) for c in chunks]
    per_doc = Counter(c.path for c in chunks)
    oversized = [c for c in chunks if c.oversized]
    print(f"{source.name}: 문서 {len(docs)}개 → 청크 {len(chunks)}개")
    if sizes:
        print(
            f"  본문 길이: 중앙값 {statistics.median(sizes):.0f}자, 최대 {max(sizes)}자, "
            f"{MAX_CHARS}자 넘는 청크 {sum(s > MAX_CHARS for s in sizes)}개 "
            f"(쪼갤 수 없는 표·코드·목록 하나가 넘는 것 {len(oversized)}개)"
        )
    with_callout = [d.file.path for d in docs if d.callout]
    print(f"  문서 안내가 붙은 문서 {len(with_callout)}개: {', '.join(with_callout) or '없음'}")
    for doc in docs:
        in_details = sum(1 for c in chunks if c.path == doc.file.path and c.in_details)
        details_note = f" (details 안 {in_details}개)" if in_details else ""
        print(f"    {per_doc[doc.file.path]:>3}  {doc.file.path}{details_note}")
        if show:
            for c in chunks:
                if c.path == doc.file.path:
                    title = " > ".join(c.heading_path) or "(머리말)"
                    flag = " [큼]" if c.oversized else ""
                    span = f"{c.start_line:>4}-{c.end_line:<4}"
                    print(f"          {span} {len(c.text):>5}자  {title}{flag}")


if __name__ == "__main__":
    sys.exit(main())
