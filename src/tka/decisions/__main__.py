"""uv run python -m tka.decisions check

결정 표 행 수가 로그 행 수와 같은지, 링크·보정이 모두 풀리는지 본다 (5단계 완료 기준, 기준 커밋).
결정을 찾아 보는 사용자 명령은 `tka decision`이다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from tka.config import load_config
from tka.decisions.table import DecisionError, build_table, count_log_rows
from tka.ingest.fetch import FetchError, source_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.decisions", description="결정 표")
    parser.add_argument("command", choices=("check",))
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    config = load_config(args.config)
    try:
        table = build_table(config, args.root)
    except (FetchError, DecisionError) as e:
        print(f"멈춤 — {e}", file=sys.stderr)
        return 1

    assert config.decisions is not None
    source = next(s for s in config.sources if s.name == config.decisions.source)
    log_text = (source_dir(config, args.root, source) / table.log_path).read_text(encoding="utf-8")
    rows = count_log_rows(log_text)
    linked = sum(1 for d in table.decisions if d.detail_path)
    corrected = sum(1 for d in table.decisions if d.replaces or d.status != "현행")
    print(f"결정 로그 {table.repo}/{table.log_path} @ {table.commit[:7]}")
    print(f"  결정 표 {len(table.decisions)}행 / 로그 {rows}행")
    print(f"  상세 링크 {linked}개 (링크 없는 칸 {len(table.decisions) - linked}개)")
    print(f"  보정이 붙은 결정 {corrected}개")
    print(f"  문제 {len(table.problems)}건")
    for p in table.problems:
        print(f"    {p}")
    return 0 if rows == len(table.decisions) and not table.problems else 1


if __name__ == "__main__":
    sys.exit(main())
