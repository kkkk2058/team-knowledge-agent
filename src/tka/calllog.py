"""호출 로그: MCP 도구와 tka 명령을 부를 때마다 한 줄씩 남긴다 (data/calls.jsonl, 커밋하지 않음).

- 틀린 답은 골든셋 재료다. 결과가 없던 검색과 "모름"은 문서가 비어 있는 곳의 후보다(plan.md §1-1 ⑤).
- 로그를 못 남겨도 답은 돌려준다. 원인은 stderr로 알린다(MCP는 stdout을 통신에 쓴다).
- 읽을 때 깨진 줄(쓰다 끊긴 줄 등)은 건너뛰고 개수만 알린다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

VIA_LABEL = {"mcp": "MCP", "cli": "CLI"}


@dataclass(frozen=True)
class CallLog:
    path: Path | None  # None이면 남기지 않는다 (평가 실행 등)
    via: str = ""  # mcp · cli

    def write(self, **record: Any) -> None:
        if self.path is None:
            return
        record = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "via": self.via,
            **record,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"호출 로그를 남기지 못했다: {e}", file=sys.stderr)


NO_LOG = CallLog(None)


def read_calls(path: Path) -> tuple[list[dict[str, Any]], int]:
    """(기록들, 읽지 못한 줄 수). 파일이 없으면 빈 목록."""
    if not path.is_file():
        return [], 0
    records, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(record, dict) and isinstance(record.get("tool"), str):
            records.append(record)
        else:
            bad += 1
    return records, bad


def is_empty(record: dict[str, Any]) -> bool:
    """근거를 하나도 돌려주지 못했거나 "모름"으로 답한 호출."""
    return record.get("unknown") is True or not record.get("returned")


def render_calls(
    records: list[dict[str, Any]],
    bad: int,
    *,
    path: Path,
    limit: int = 20,
    only_empty: bool = False,
) -> str:
    if not records:
        note = f" (읽지 못한 줄 {bad}개)" if bad else ""
        return f"호출 로그가 아직 없다: {path}{note}. MCP 도구나 tka 명령을 부르면 쌓인다."
    empty = [r for r in records if is_empty(r)]
    tools = Counter(r["tool"] for r in records)
    vias = Counter(r.get("via") or "mcp" for r in records)  # via가 없던 옛 기록은 MCP뿐이었다
    lines = [
        f"호출 로그 {path} — {len(records)}건, {_when(records[0])} ~ {_when(records[-1])}",
        "도구별: "
        + " · ".join(f"{t} {n}" for t, n in tools.most_common())
        + f" ({' · '.join(f'{VIA_LABEL.get(v, v)} {n}' for v, n in vias.most_common())})",
        f"결과 없음·모름 {len(empty)}건 — 문서가 비어 있는 곳의 후보다",
    ]
    if bad:
        lines.append(f"읽지 못한 줄 {bad}개")
    shown = (empty if only_empty else records)[-limit:]
    title = "결과 없음·모름" if only_empty else "최근"
    lines += ["", f"{title} {len(shown)}건"]
    lines += [_row(r) for r in reversed(shown)]
    return "\n".join(lines)


def _row(r: dict[str, Any]) -> str:
    args = r.get("args") or {}
    query = args.get("query") or args.get("question") or "(검색어 없음)"
    if r.get("unknown") is True:
        result = "모름"
    else:
        result = f"{len(r.get('returned') or [])}개"
    elapsed = f"{r['elapsed_ms'] / 1000:.1f}초" if isinstance(r.get("elapsed_ms"), int) else "-"
    commit = str(r.get("commit") or "?")[:7]
    via = (r.get("via") or "mcp").lower()
    return f"{_when(r)}  {via}  {r['tool']}  {result}  {elapsed}  {commit}  {query}"


def _when(r: dict[str, Any]) -> str:
    try:
        return datetime.fromisoformat(r["at"]).strftime("%m-%d %H:%M")
    except (KeyError, TypeError, ValueError):
        return "?"
