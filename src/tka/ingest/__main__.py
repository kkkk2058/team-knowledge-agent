"""uv run python -m tka.ingest fetch|files [--source 이름] [--list]"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from tka.config import load_config
from tka.ingest.fetch import FetchError, fetch_source
from tka.ingest.files import count_by_rule, select_files


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.ingest", description="소스 가져오기")
    parser.add_argument("command", choices=("fetch", "files"))
    parser.add_argument("--source", help="이 소스만 (설정의 name)")
    parser.add_argument("--list", action="store_true", help="files: 고른 파일을 모두 보여준다")
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."), help="레포 루트")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    sources = [s for s in config.sources if not args.source or s.name == args.source]
    if not sources:
        parser.error(f"설정에 없는 소스다: {args.source}")

    for source in sources:
        try:
            if args.command == "fetch":
                fetched = fetch_source(config, args.root, source)
                print(
                    f"{source.name}: {fetched.action} → {fetched.commit[:7]} ({fetched.directory})"
                )
                continue
            files = select_files(config, args.root, source)
        except FetchError as e:
            print(f"{source.name}: 멈춤 — {e}", file=sys.stderr)
            return 1

        commit = files[0].commit[:7] if files else source.ref
        print(f"{source.name} @ {commit}: {len(files)}개")
        for rule, count in count_by_rule(files, source).items():
            warning = "  ← 0개. 규칙이 맞는지 확인한다" if count == 0 else ""
            print(f"  {rule:<55} {count:>3}{warning}")
        if args.list:
            for f in files:
                print(f"    {f.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
