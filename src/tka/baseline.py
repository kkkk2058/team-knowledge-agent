"""Claude Code 베이스라인: 이 프로젝트 맥락이 없는 새 세션으로 골든셋을 묻는다 (plan.md D12).

    uv run python -m tka.baseline --repo-dir <깨끗한 clone> [--model M] [--ids g01,g02]

- repo_dir은 이 프로젝트 폴더 밖이어야 한다. Claude Code는 상위 폴더의 CLAUDE.md를 읽으므로,
  안에 두면 이 프로젝트의 규칙과 골든셋 위치가 새 세션에 섞인다.
- 도구는 Read·Grep·Glob만 쓴다. MCP는 끈다(화면 기록 같은 도구가 정답을 볼 수 있다).
  웹도 끈다(기준 커밋이 아닌 최신 문서를 보게 된다).
- 답은 문항마다 바로 저장한다. 다시 돌리면 답이 있는 문항은 건너뛴다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from tka.evaluation import PROMPT_TEMPLATE, dump_yaml, prompt_for
from tka.golden import GoldenSet, load_golden

ALLOWED_TOOLS = "Read,Grep,Glob"
DISALLOWED_TOOLS = "Bash,WebFetch,WebSearch,Task,Edit,Write,NotebookEdit"
TIMEOUT_SECONDS = 900


class BaselineError(RuntimeError):
    """베이스라인을 더 돌릴 수 없다 (로그인 만료, 잘못된 clone 위치·커밋)."""


def claude_command(prompt: str, claude_bin: str, model: str | None) -> list[str]:
    command = [
        claude_bin,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--allowedTools",
        ALLOWED_TOOLS,
        "--disallowedTools",
        DISALLOWED_TOOLS,
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    return command + (["--model", model] if model else [])


def run_claude_code(
    golden: GoldenSet,
    repo: str,
    repo_dir: Path,
    out_dir: Path,
    *,
    project_root: Path,
    claude_bin: str = "claude",
    model: str | None = None,
    ids: Sequence[str] | None = None,
    version: str = "v0",
) -> Path:
    repo_dir = repo_dir.resolve()
    _check_repo_dir(golden, repo, repo_dir, project_root.resolve())

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "answers.yaml"
    data = _load_or_start(path, golden, repo, out_dir.name)
    done = {a["id"] for a in data["answers"] if a.get("answer") and not a.get("error")}
    data["answers"] = [a for a in data["answers"] if a["id"] in done]
    models = set(data["detail"].get("models") or [])
    data["detail"]["tool"] = f"Claude Code {_claude_version(claude_bin)}"

    items = [i for i in golden.for_version(version) if not ids or i.id in ids]
    for item in items:
        if item.id in done:
            continue
        entry = _ask(item.id, prompt_for(item), claude_bin, model, repo_dir)
        models.update(entry.pop("_models", []))
        data["answers"].append(entry)
        data["answers"].sort(key=lambda a: a["id"])
        data["detail"]["models"] = sorted(models)
        path.write_text(dump_yaml(data), encoding="utf-8")
        print(f"{item.id}: {'오류 — ' + entry['error'] if entry.get('error') else '답 저장'}")
    return path


def _check_repo_dir(golden: GoldenSet, repo: str, repo_dir: Path, project_root: Path) -> None:
    if project_root == repo_dir or project_root in repo_dir.parents:
        raise BaselineError(
            f"{repo_dir}가 이 프로젝트 안에 있다. 새 세션이 이 프로젝트의 CLAUDE.md를 읽게 되므로 "
            "프로젝트 밖에 clone한다"
        )
    if repo not in golden.source_commits:
        raise BaselineError(f"{repo}: 골든셋 source_commits에 없는 레포다")
    commit = golden.source_commits[repo]
    head = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not head.startswith(commit):
        raise BaselineError(
            f"{repo_dir}가 기준 커밋이 아니다 (HEAD {head[:7]}, 기준 {commit}). "
            f"git -C {repo_dir} checkout {commit}"
        )


def _load_or_start(path: Path, golden: GoldenSet, repo: str, run_id: str) -> dict[str, Any]:
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data.setdefault("answers", [])
        data.setdefault("detail", {})
        return data
    return {
        "run_id": run_id,
        "system": "claude-code",
        "detail": {
            "session": "새 세션 (claude -p), 이 프로젝트 맥락 없음",
            "allowed_tools": ALLOWED_TOOLS,
            "mcp": "끔 (--strict-mcp-config)",
            "prompt_template": PROMPT_TEMPLATE,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "source_commits": {repo: golden.source_commits[repo]},
        "answers": [],
    }


def _ask(item_id: str, prompt: str, claude_bin: str, model: str | None, cwd: Path) -> dict:
    try:
        completed = subprocess.run(
            claude_command(prompt, claude_bin, model),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"id": item_id, "answer": "", "error": f"{TIMEOUT_SECONDS}초 안에 끝나지 않았다"}

    try:
        out = json.loads(completed.stdout)
    except json.JSONDecodeError:
        head = (completed.stdout or completed.stderr).strip()[:200]
        return {"id": item_id, "answer": "", "error": f"출력이 JSON이 아니다: {head!r}"}

    result = str(out.get("result") or "")
    if out.get("is_error"):
        if "authenticate" in result.lower():
            raise BaselineError(
                f"Claude Code 로그인이 필요하다 ({result}). 터미널에서 claude → /login"
            )
        return {"id": item_id, "answer": "", "error": result[:300] or "알 수 없는 오류"}

    return {
        "id": item_id,
        "answer": result,
        "meta": {
            "duration_ms": out.get("duration_ms"),
            "cost_usd": out.get("total_cost_usd"),
            "num_turns": out.get("num_turns"),
        },
        "_models": list((out.get("modelUsage") or {}).keys()),
    }


def _claude_version(claude_bin: str) -> str:
    completed = subprocess.run([claude_bin, "--version"], capture_output=True, text=True)
    return completed.stdout.strip().split(" ")[0] or "?"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tka.baseline", description="Claude Code 베이스라인"
    )
    parser.add_argument("--repo-dir", type=Path, required=True, help="프로젝트 밖의 깨끗한 clone")
    parser.add_argument("--repo", default="KTB4-13th-wiki")
    parser.add_argument("--out", type=Path, help="기본: eval/results/<오늘>-claude-code")
    parser.add_argument("--model")
    parser.add_argument("--ids", help="쉼표로 구분한 문항 id. 없으면 전부")
    parser.add_argument("--golden", type=Path, default=Path("eval/golden.yaml"))
    parser.add_argument("--claude-bin", default="claude")
    args = parser.parse_args(argv)

    out = args.out or Path("eval/results") / f"{datetime.now():%Y-%m-%d}-claude-code"
    try:
        path = run_claude_code(
            load_golden(args.golden),
            args.repo,
            args.repo_dir,
            out,
            project_root=Path.cwd(),
            claude_bin=args.claude_bin,
            model=args.model,
            ids=args.ids.split(",") if args.ids else None,
        )
    except BaselineError as e:
        print(f"멈춤: {e}", file=sys.stderr)
        return 1
    print(f"답: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
