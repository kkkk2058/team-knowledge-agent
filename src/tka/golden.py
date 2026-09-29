"""골든셋(eval/golden.yaml)을 읽고, 기준 커밋 원문과 대조해 검사한다.

    uv run python -m tka.golden check    근거 줄·포함 규칙·모름 문항을 검사한다
    uv run python -m tka.golden review   확인용 시트(근거 줄 원문 포함)를 data/에 만든다

검사하려면 소스 레포가 설정의 cache_dir 아래에 기준 커밋으로 받아져 있어야 한다.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tka.config import COMMIT_PATTERN, Config, Source, load_config

TYPES = (
    "명세 값",
    "결정 이유",
    "함정",
    "문서끼리 다름",
    "정책 값",
    "장애 동작",
    "클라우드",
    "팀 규칙",
    "모름",
    "회의 결정",
    "레포 횡단",
)
VERSIONS = ("v0", "v1", "v2", "v3")
STATUSES = ("확인 대기", "확정", "후보")
ID_PATTERN = re.compile(r"g\d{2,}")
LINES_PATTERN = re.compile(r"(\d+)(?:-(\d+))?")


class GoldenError(ValueError):
    """골든셋 파일의 형식이나 값이 잘못됐다."""


@dataclass(frozen=True)
class Evidence:
    repo: str  # owner를 뺀 레포 이름
    path: str
    start: int
    end: int

    def label(self) -> str:
        span = str(self.start) if self.start == self.end else f"{self.start}-{self.end}"
        return f"{self.repo}/{self.path}:{span}"


@dataclass(frozen=True)
class GoldenItem:
    id: str
    type: str
    version: str
    question: str
    expected: str
    evidence: tuple[Evidence, ...]
    status: str
    trap: str | None
    note: str | None
    absent_terms: tuple[str, ...]


@dataclass(frozen=True)
class GoldenSet:
    source_commits: dict[str, str]
    items: tuple[GoldenItem, ...]

    def for_version(self, version: str) -> tuple[GoldenItem, ...]:
        return tuple(i for i in self.items if i.version == version)


@dataclass(frozen=True)
class Problem:
    item_id: str  # 문항과 무관한 문제는 "-"
    message: str

    def __str__(self) -> str:
        return f"[{self.item_id}] {self.message}"


@dataclass(frozen=True)
class CheckResult:
    problems: tuple[Problem, ...]
    checked: tuple[str, ...]  # 대조한 레포
    skipped: dict[str, tuple[str, ...]]  # 설정에 없어 건너뛴 레포 → 문항 id


# ── 읽기·형식 검사 ────────────────────────────────────────────────


def load_golden(path: Path) -> GoldenSet:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise GoldenError(f"{path}: YAML 문법 오류: {e}") from e
    if not isinstance(raw, dict):
        raise GoldenError(f"{path}: 최상위가 매핑이 아니다")

    meta = _field(raw, "meta", dict, "")
    commits = _field(meta, "source_commits", dict, "meta")
    source_commits: dict[str, str] = {}
    for repo, commit in commits.items():
        commit = str(commit)
        if not COMMIT_PATTERN.fullmatch(commit):
            raise GoldenError(f"meta.source_commits.{repo}: 커밋 형식이 아니다: {commit!r}")
        source_commits[str(repo)] = commit

    raw_items = _field(raw, "items", list, "")
    if not raw_items:
        raise GoldenError("items: 문항이 하나도 없다")
    items = tuple(
        _parse_item(item, f"items[{i}]", source_commits) for i, item in enumerate(raw_items)
    )
    ids = [i.id for i in items]
    duplicates = sorted({x for x in ids if ids.count(x) > 1})
    if duplicates:
        raise GoldenError(f"items: id가 겹친다: {', '.join(duplicates)}")
    return GoldenSet(source_commits=source_commits, items=items)


def _parse_item(raw: Any, where: str, source_commits: dict[str, str]) -> GoldenItem:
    if not isinstance(raw, dict):
        raise GoldenError(f"{where}: 매핑이 아니다")
    item_id = _field(raw, "id", str, where)
    if not ID_PATTERN.fullmatch(item_id):
        raise GoldenError(f"{where}.id: 'g' + 숫자 두 자리 이상이어야 한다: {item_id!r}")
    where = item_id

    kind = _choice(raw, "type", TYPES, where)
    evidence = tuple(
        _parse_evidence(e, f"{where}.evidence[{i}]", source_commits)
        for i, e in enumerate(_field(raw, "evidence", list, where))
    )
    raw_terms = raw.get("absent_terms", [])
    if not isinstance(raw_terms, list) or not all(isinstance(t, str) and t for t in raw_terms):
        raise GoldenError(f"{where}.absent_terms: 빈 값이 아닌 문자열 목록이어야 한다")
    absent_terms = tuple(raw_terms)

    if kind == "모름":
        if not absent_terms:
            raise GoldenError(f"{where}: 모름 문항은 absent_terms로 없음을 확인할 단어를 적는다")
    else:
        if not evidence:
            raise GoldenError(f"{where}: 근거(evidence)가 없다. 근거 없는 정답은 모름 문항이다")
        if absent_terms:
            raise GoldenError(f"{where}: absent_terms는 모름 문항에만 쓴다")

    return GoldenItem(
        id=item_id,
        type=kind,
        version=_choice(raw, "version", VERSIONS, where),
        question=_text(raw, "question", where),
        expected=_text(raw, "expected", where),
        evidence=evidence,
        status=_choice(raw, "status", STATUSES, where),
        trap=_optional_text(raw, "trap", where),
        note=_optional_text(raw, "note", where),
        absent_terms=absent_terms,
    )


def _parse_evidence(raw: Any, where: str, source_commits: dict[str, str]) -> Evidence:
    if not isinstance(raw, dict):
        raise GoldenError(f"{where}: 매핑이 아니다")
    repo = _field(raw, "repo", str, where)
    if repo not in source_commits:
        raise GoldenError(f"{where}.repo: meta.source_commits에 없는 레포다: {repo!r}")
    lines = str(_field(raw, "lines", (str, int), where))
    match = LINES_PATTERN.fullmatch(lines)
    if not match:
        raise GoldenError(f"{where}.lines: 'N' 또는 'N-M' 형식이 아니다: {lines!r}")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start < 1 or end < start:
        raise GoldenError(f"{where}.lines: 줄 범위가 잘못됐다: {lines!r}")
    return Evidence(repo=repo, path=_field(raw, "path", str, where), start=start, end=end)


def _field(raw: dict, key: str, kind: type | tuple[type, ...], where: str) -> Any:
    label = f"{where}.{key}" if where else key
    if key not in raw:
        raise GoldenError(f"{label}: 없다")
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, kind):
        raise GoldenError(f"{label}: 타입이 맞지 않는다: {value!r}")
    return value


def _text(raw: dict, key: str, where: str) -> str:
    value = _field(raw, key, str, where).strip()
    if not value:
        raise GoldenError(f"{where}.{key}: 비어 있다")
    return value


def _optional_text(raw: dict, key: str, where: str) -> str | None:
    return _text(raw, key, where) if raw.get(key) is not None else None


def _choice(raw: dict, key: str, choices: Sequence[str], where: str) -> str:
    value = _field(raw, key, str, where)
    if value not in choices:
        raise GoldenError(f"{where}.{key}: {value!r}는 허용값이 아니다 ({', '.join(choices)})")
    return value


# ── 원문 대조 ─────────────────────────────────────────────────────


def source_dir(config: Config, root: Path, source: Source) -> Path:
    return root / config.cache_dir / source.name


def read_lines(path: Path) -> list[str]:
    """줄 번호가 git·에디터와 같도록 `\\n`으로만 나눈다.

    str.splitlines()는 U+2028·폼피드 같은 문자에서도 줄을 나눠 줄 번호가 어긋난다.
    """
    lines = path.read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def check_golden(golden: GoldenSet, config: Config, root: Path) -> CheckResult:
    problems: list[Problem] = []
    checked: list[str] = []
    skipped: dict[str, list[str]] = {}
    tracked: dict[str, set[str]] = {}

    for repo, commit in golden.source_commits.items():
        source = config.source_for_repo(repo)
        if source is None:
            continue
        if not (source.ref.startswith(commit) or commit.startswith(source.ref)):
            problems.append(
                Problem("-", f"{repo}: 골든셋 기준 커밋 {commit}과 설정 ref {source.ref}가 다르다")
            )
            continue
        directory = source_dir(config, root, source)
        problem = checkout_problem(directory, source)
        if problem:
            problems.append(Problem("-", problem))
            continue
        tracked[repo] = tracked_files(directory)
        checked.append(repo)

    for item in golden.items:
        for ev in item.evidence:
            source = config.source_for_repo(ev.repo)
            if source is None:
                skipped.setdefault(ev.repo, []).append(item.id)
                continue
            if ev.repo not in tracked:
                continue  # 위에서 checkout 문제로 이미 보고했다
            problems.extend(_evidence_problems(item, ev, source, config, root, tracked[ev.repo]))
        if item.absent_terms:
            problems.extend(_absent_problems(item, config, root, tracked))

    return CheckResult(
        problems=tuple(problems),
        checked=tuple(checked),
        skipped={repo: tuple(dict.fromkeys(ids)) for repo, ids in skipped.items()},
    )


def checkout_problem(directory: Path, source: Source) -> str | None:
    fetch_hint = (
        f"받는 법: git clone https://github.com/{source.repo} {directory}"
        f" && git -C {directory} checkout {source.ref}"
    )
    if not directory.is_dir():
        return f"{source.repo_name}: 소스가 없다 ({directory}). {fetch_hint}"
    if not (directory / ".git").exists():
        return f"{source.repo_name}: git 저장소가 아니다 ({directory})"
    head = _git(directory, "rev-parse", "HEAD").strip()
    if not head.startswith(source.ref):
        return (
            f"{source.repo_name}: 받아 둔 소스가 기준 커밋이 아니다 "
            f"(HEAD {head[:7]}, 기준 {source.ref}). git -C {directory} checkout {source.ref}"
        )
    return None


def _evidence_problems(
    item: GoldenItem,
    ev: Evidence,
    source: Source,
    config: Config,
    root: Path,
    tracked: set[str],
) -> list[Problem]:
    if ev.path not in tracked:
        return [Problem(item.id, f"{ev.label()}: 기준 커밋에 없는 파일이다")]
    problems = []
    if item.version == "v0" and not source.includes(ev.path):
        problems.append(Problem(item.id, f"{ev.label()}: v0 문항 근거가 설정의 포함 규칙 밖이다"))
    lines = read_lines(source_dir(config, root, source) / ev.path)
    if ev.end > len(lines):
        problems.append(Problem(item.id, f"{ev.label()}: 줄 범위 밖이다 (파일은 {len(lines)}줄)"))
    elif not any(line.strip() for line in lines[ev.start - 1 : ev.end]):
        problems.append(Problem(item.id, f"{ev.label()}: 근거 줄이 비어 있다"))
    return problems


def _absent_problems(
    item: GoldenItem, config: Config, root: Path, tracked: dict[str, set[str]]
) -> list[Problem]:
    pattern = re.compile("|".join(re.escape(t) for t in item.absent_terms), re.IGNORECASE)
    problems = []
    for repo, files in tracked.items():
        source = config.source_for_repo(repo)
        assert source is not None  # tracked에는 설정에 있는 레포만 들어 있다
        for path in sorted(p for p in files if source.includes(p)):
            for number, line in enumerate(read_lines(source_dir(config, root, source) / path), 1):
                found = pattern.search(line)
                if found:
                    problems.append(
                        Problem(
                            item.id,
                            f"모름 문항인데 {found.group(0)!r}이 {repo}/{path}:{number}에 있다",
                        )
                    )
    return problems


def tracked_files(directory: Path) -> set[str]:
    return set(filter(None, _git(directory, "ls-files", "-z").split("\0")))


def _git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout


# ── 확인용 시트 ───────────────────────────────────────────────────


def render_review(golden: GoldenSet, config: Config, root: Path) -> str:
    """레포 주인이 정답을 확인할 시트. 근거 줄 원문을 옆에 붙인다.

    원문 일부가 들어가므로 커밋하지 않는 data/에 쓴다 (소스 문서를 복사하지 않는다).
    """
    commits = ", ".join(f"{repo} @ {c}" for repo, c in golden.source_commits.items())
    out = [
        "# 골든셋 확인 시트",
        "",
        f"기준: {commits}",
        "",
        "정답(expected)이 근거 원문과 맞는지 보고, 고칠 것이 있으면 문항 id와 함께 알려준다.",
        "",
    ]
    for item in golden.items:
        out += [
            f"## {item.id} · {item.type} · {item.version} · {item.status}",
            "",
            f"**질문** {item.question}",
            "",
            f"**정답** {item.expected}",
            "",
        ]
        if item.trap:
            out += [f"**함정** {item.trap}", ""]
        if item.note:
            out += [f"**메모** {item.note}", ""]
        if item.absent_terms:
            out += [f"**없음 확인 단어** {', '.join(item.absent_terms)}", ""]
        for ev in item.evidence:
            out += [f"`{ev.label()}`", ""]
            source = config.source_for_repo(ev.repo)
            path = source_dir(config, root, source) / ev.path if source else None
            if path is None or not path.is_file():
                out += ["> (원문을 받지 않은 레포라 보여주지 않는다)", ""]
                continue
            lines = read_lines(path)
            quoted = [
                f"{n:>5}  {lines[n - 1]}" for n in range(ev.start, min(ev.end, len(lines)) + 1)
            ]
            out += ["```text", *quoted, "```", ""]
        out += ["- [ ] 맞다", "- [ ] 고칠 것:", ""]
    return "\n".join(out)


# ── CLI ───────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.golden", description="골든셋 검사")
    parser.add_argument("command", choices=("check", "review"))
    parser.add_argument("--golden", type=Path, default=Path("eval/golden.yaml"))
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."), help="레포 루트")
    parser.add_argument("--out", type=Path, default=Path("data/golden_review.md"))
    args = parser.parse_args(argv)

    golden = load_golden(args.golden)
    config = load_config(args.config)

    if args.command == "review":
        out = args.root / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_review(golden, config, args.root), encoding="utf-8")
        print(f"확인 시트: {out}")
        return 0

    result = check_golden(golden, config, args.root)
    counts = ", ".join(f"{v} {len(golden.for_version(v))}" for v in VERSIONS)
    print(f"골든셋 {len(golden.items)}문항 ({counts})")
    for repo in result.checked:
        print(f"대조한 소스: {repo} @ {golden.source_commits[repo]}")
    for repo, ids in result.skipped.items():
        print(f"건너뛴 레포 (설정에 없음): {repo} — {', '.join(ids)}")
    for problem in result.problems:
        print(problem)
    print(f"문제 {len(result.problems)}건")
    return 1 if result.problems else 0


if __name__ == "__main__":
    sys.exit(main())
