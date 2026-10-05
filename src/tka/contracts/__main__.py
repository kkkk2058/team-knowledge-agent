"""uv run python -m tka.contracts fetch|check [--out 파일]

fetch    대조용 소스(설정의 contracts.sources)를 캐시에 받아 기준 커밋으로 맞춘다
check    API 대조표를 마크다운으로 보여준다 (--out이면 파일로도 쓴다)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from tka.config import load_config
from tka.contracts.report import build_report, render_markdown
from tka.ingest.fetch import FetchError, fetch_source


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.contracts", description="API 대조표")
    parser.add_argument("command", choices=("fetch", "check"))
    parser.add_argument("--out", type=Path, help="check: 대조표를 쓸 파일")
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."), help="레포 루트")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if config.contracts is None:
        print("멈춤 — 설정에 contracts가 없다", file=sys.stderr)
        return 1
    try:
        if args.command == "fetch":
            for source in config.contracts.sources:
                fetched = fetch_source(config, args.root, source)
                print(f"{source.name}: {fetched.action} → {fetched.commit[:7]}")
            return 0
        markdown = render_markdown(build_report(config, args.root), date.today().isoformat())
    except FetchError as e:
        print(f"멈춤 — {e}", file=sys.stderr)
        return 1
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
