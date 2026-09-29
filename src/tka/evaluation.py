"""평가 실행 결과(eval/results/<run>/)를 읽고 채점·요약한다.

한 실행 폴더에는 세 파일이 있다.
    answers.yaml  시스템이 낸 답 (베이스라인 실행기나 사람이 채운다)
    scores.yaml   정답 판정: 정답 · 부분 · 오답 (사람이 확인한다)
    summary.md    이 모듈이 만든 요약 (정답률, 인용 정확도)

    uv run python -m tka.evaluation template eval/results/<run> --system deepwiki
    uv run python -m tka.evaluation summary eval/results/<run>

모든 시스템(베이스라인, 우리 봇)에 같은 질문 형식(PROMPT_TEMPLATE)을 쓴다.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tka.config import Config, load_config
from tka.golden import (
    GoldenItem,
    GoldenSet,
    checkout_problem,
    load_golden,
    read_lines,
    source_dir,
    tracked_files,
)

PROMPT_TEMPLATE = (
    "{question}\n\n"
    "이 레포의 문서를 근거로 한국어로 답해줘. "
    "근거로 쓴 파일 경로와 줄 번호를 `경로:줄` 형식으로 함께 적어줘. "
    "문서에 근거가 없으면 모른다고 답해."
)
VERDICTS = {"정답": 1.0, "부분": 0.5, "오답": 0.0}
# 인용한 줄이 골든셋 근거 줄에서 이만큼 떨어져 있어도 같은 곳으로 본다 (표·목록은 몇 줄에 걸친다).
LINE_TOLERANCE = 3

# path:12, path:12-15, path#L12, path#L12-L15. 경로는 확장자로 끝나야 한다.
CITATION_PATTERN = re.compile(
    r"(?P<path>[\w./-]+\.(?:md|ya?ml|tsv|java|py|ts|tsx|sql))"
    r"(?::|#L)(?P<start>\d+)(?:[-–~]L?(?P<end>\d+))?"
)


class ResultError(ValueError):
    """실행 결과 파일의 형식이나 값이 잘못됐다."""


def prompt_for(item: GoldenItem) -> str:
    return PROMPT_TEMPLATE.format(question=item.question)


# ── 인용 추출·판정 ────────────────────────────────────────────────


@dataclass(frozen=True)
class Citation:
    raw_path: str
    start: int
    end: int


@dataclass(frozen=True)
class CitationCheck:
    citation: Citation
    repo: str | None  # 경로를 찾은 레포. 못 찾으면 None
    path: str | None  # 레포 루트 기준 경로. 못 찾으면 None
    exists: bool  # 파일이 있고 줄이 범위 안이다
    hits_evidence: bool  # 골든셋 근거 줄과 겹친다


@dataclass(frozen=True)
class SourceFiles:
    directory: Path
    tracked: set[str]


def extract_citations(text: str) -> list[Citation]:
    seen: dict[tuple[str, int, int], Citation] = {}
    for m in CITATION_PATTERN.finditer(text):
        start = int(m.group("start"))
        end = int(m.group("end") or start)
        if end < start:
            continue
        path = _normalize_path(m.group("path"))
        seen.setdefault((path, start, end), Citation(path, start, end))
    return list(seen.values())


def _normalize_path(path: str) -> str:
    # GitHub 주소(…/blob/<ref>/docs/x.md)는 레포 안 경로만 남긴다.
    path = re.sub(r"^.*?/blob/[^/]+/", "", path)
    # lstrip("./")는 .agents/ 의 점까지 지우므로 쓰지 않는다.
    return path.removeprefix("./").removeprefix("/")


def resolve_path(raw: str, tracked: set[str]) -> str | None:
    """인용 경로를 레포 파일로 바꾼다. `spec.md`처럼 줄여 쓴 경로는 하나로 정해질 때만 인정한다."""
    if raw in tracked:
        return raw
    matches = [p for p in tracked if p.endswith("/" + raw)]
    return matches[0] if len(matches) == 1 else None


def check_citations(
    citations: list[Citation], item: GoldenItem, sources: dict[str, SourceFiles]
) -> list[CitationCheck]:
    """인용마다 레포를 차례로 찾아, 처음 경로가 풀리는 레포 기준으로 판정한다."""
    checks = []
    for c in citations:
        repo, path = next(
            ((r, p) for r, s in sources.items() if (p := resolve_path(c.raw_path, s.tracked))),
            (None, None),
        )
        if repo is None or path is None:
            checks.append(CitationCheck(c, None, None, False, False))
            continue
        exists = c.end <= len(read_lines(sources[repo].directory / path))
        hits = exists and any(
            e.repo == repo
            and e.path == path
            and c.start <= e.end + LINE_TOLERANCE
            and e.start <= c.end + LINE_TOLERANCE
            for e in item.evidence
        )
        checks.append(CitationCheck(c, repo, path, exists, hits))
    return checks


# ── 실행 결과 읽기 ────────────────────────────────────────────────


@dataclass(frozen=True)
class Answer:
    id: str
    answer: str
    error: str | None


@dataclass(frozen=True)
class Run:
    run_id: str
    system: str
    detail: dict[str, Any]
    source_commits: dict[str, str]
    answers: dict[str, Answer]


def load_run(run_dir: Path) -> Run:
    raw = _load_yaml(run_dir / "answers.yaml")
    answers = {}
    for i, a in enumerate(raw.get("answers") or []):
        if not isinstance(a, dict) or not isinstance(a.get("id"), str):
            raise ResultError(f"answers[{i}]: id가 없다")
        answers[a["id"]] = Answer(a["id"], str(a.get("answer") or ""), a.get("error"))
    return Run(
        run_id=str(raw.get("run_id") or run_dir.name),
        system=str(raw.get("system") or "?"),
        detail=dict(raw.get("detail") or {}),
        source_commits={str(k): str(v) for k, v in (raw.get("source_commits") or {}).items()},
        answers=answers,
    )


def load_scores(run_dir: Path) -> dict[str, str]:
    """id → 판정. scores.yaml이 없으면 빈 dict (아직 채점 전)."""
    path = run_dir / "scores.yaml"
    if not path.exists():
        return {}
    raw = _load_yaml(path)
    scores = {}
    for i, s in enumerate(raw.get("items") or []):
        verdict = s.get("verdict") if isinstance(s, dict) else None
        if verdict not in VERDICTS:
            raise ResultError(f"scores items[{i}]: verdict는 {'·'.join(VERDICTS)} 중 하나다")
        scores[s["id"]] = verdict
    return scores


class _LiteralDumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    # 여러 줄 답은 | 블록으로 써서 사람이 읽고 고치기 쉽게 한다.
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LiteralDumper.add_representer(str, _str_representer)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_LiteralDumper, allow_unicode=True, sort_keys=False, width=1000)


def _load_yaml(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ResultError(f"{path}: YAML 문법 오류: {e}") from e
    if not isinstance(raw, dict):
        raise ResultError(f"{path}: 최상위가 매핑이 아니다")
    return raw


# ── 요약 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ItemResult:
    item: GoldenItem
    answer: Answer | None
    verdict: str | None
    citations: list[CitationCheck]

    @property
    def cited_evidence(self) -> bool:
        return any(c.hits_evidence for c in self.citations)

    @property
    def has_answer(self) -> bool:
        return bool(self.answer and self.answer.answer and not self.answer.error)


def evaluate(
    golden: GoldenSet,
    run: Run,
    scores: dict[str, str],
    config: Config,
    root: Path,
    version: str = "v0",
) -> list[ItemResult]:
    sources = load_sources(golden, config, root)
    results = []
    for item in golden.for_version(version):
        answer = run.answers.get(item.id)
        checks = (
            check_citations(extract_citations(answer.answer), item, sources)
            if answer and answer.answer
            else []
        )
        results.append(ItemResult(item, answer, scores.get(item.id), checks))
    return results


def load_sources(golden: GoldenSet, config: Config, root: Path) -> dict[str, SourceFiles]:
    """설정에 있는 소스를 기준 커밋 그대로 읽는다. 커밋이 다르면 줄 판정이 틀리므로 멈춘다."""
    sources = {}
    for repo in golden.source_commits:
        source = config.source_for_repo(repo)
        if source is None:
            continue
        directory = source_dir(config, root, source)
        problem = checkout_problem(directory, source)
        if problem:
            raise ResultError(problem)
        sources[repo] = SourceFiles(directory, tracked_files(directory))
    return sources


def render_summary(run: Run, results: list[ItemResult]) -> str:
    scored = [r for r in results if r.verdict]
    with_evidence = [r for r in results if r.item.evidence and r.item.type != "모름"]
    all_citations = [c for r in results for c in r.citations]

    def pct(num: float, den: int) -> str:
        return f"{num / den:.0%} ({num:g}/{den})" if den else "—"

    accuracy = pct(sum(VERDICTS[r.verdict] for r in scored), len(scored))
    citation_accuracy = pct(sum(c.exists for c in all_citations), len(all_citations))
    evidence_hits = pct(sum(r.cited_evidence for r in with_evidence), len(with_evidence))
    lines = [
        f"# {run.run_id}",
        "",
        f"- 시스템: {run.system}",
        *[f"- {k}: {v}" for k, v in run.detail.items()],
        f"- 소스: {', '.join(f'{r} @ {c}' for r, c in run.source_commits.items())}",
        "",
        "## 요약",
        "",
        "| 지표 | 값 |",
        "|---|---|",
        f"| 정답률 (정답 1, 부분 0.5) | {accuracy} |",
        f"| 채점한 문항 | {len(scored)}/{len(results)} |",
        f"| 인용 정확도 (인용한 파일·줄이 실제로 있음) | {citation_accuracy} |",
        f"| 근거 적중 (골든셋 근거 줄을 인용한 문항) | {evidence_hits} |",
        f"| 답이 없는 문항 (오류 포함) | {sum(1 for r in results if not r.has_answer)} |",
        "",
        "## 유형별 정답률",
        "",
        "| 유형 | 정답률 |",
        "|---|---|",
    ]
    for kind in dict.fromkeys(r.item.type for r in results):
        rows = [r for r in scored if r.item.type == kind]
        lines.append(f"| {kind} | {pct(sum(VERDICTS[r.verdict] for r in rows), len(rows))} |")
    lines += [
        "",
        "## 문항별",
        "",
        "| id | 유형 | 판정 | 인용 (있음/전체) | 근거 적중 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        cites = f"{sum(c.exists for c in r.citations)}/{len(r.citations)}"
        hit = (
            "—"
            if not r.item.evidence or r.item.type == "모름"
            else ("O" if r.cited_evidence else "X")
        )
        lines.append(f"| {r.item.id} | {r.item.type} | {r.verdict or '미채점'} | {cites} | {hit} |")
    return "\n".join(lines) + "\n"


def write_template(run_dir: Path, golden: GoldenSet, system: str, version: str = "v0") -> Path:
    """사람이 답을 붙여 넣을 answers.yaml 틀을 만든다 (DeepWiki처럼 손으로 묻는 시스템용)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "answers.yaml"
    if path.exists():
        raise ResultError(f"{path}: 이미 있다. 덮어쓰지 않는다")
    data = {
        "run_id": run_dir.name,
        "system": system,
        "detail": {"prompt_template": PROMPT_TEMPLATE},
        "source_commits": golden.source_commits,
        "answers": [
            {"id": i.id, "question": prompt_for(i), "answer": ""}
            for i in golden.for_version(version)
        ],
    }
    path.write_text(dump_yaml(data), encoding="utf-8")
    return path


# ── CLI ───────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.evaluation", description="평가 결과 채점")
    parser.add_argument("command", choices=("summary", "template"))
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--system", help="template: 시스템 이름")
    parser.add_argument("--golden", type=Path, default=Path("eval/golden.yaml"))
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    golden = load_golden(args.golden)
    if args.command == "template":
        if not args.system:
            parser.error("template에는 --system이 필요하다")
        print(f"틀: {write_template(args.run_dir, golden, args.system)}")
        return 0

    run = load_run(args.run_dir)
    results = evaluate(golden, run, load_scores(args.run_dir), load_config(args.config), args.root)
    summary = render_summary(run, results)
    (args.run_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
