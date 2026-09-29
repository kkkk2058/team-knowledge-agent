"""기준 커밋의 파일 목록에 포함 규칙을 적용해 인덱싱할 파일을 고른다 (context.md §3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tka.config import Config, Source, matches_rule
from tka.ingest.fetch import FetchError, checkout_problem, git, source_dir

# git ls-tree 모드. 일반 파일만 고른다. 심볼릭 링크(120000)는 같은 파일을 두 번 넣게 되고,
# 서브모듈(160000)은 이 레포 내용이 아니다.
REGULAR_FILE_MODES = ("100644", "100755")


@dataclass(frozen=True)
class SourceFile:
    source: str  # 설정의 소스 이름
    repo: str  # owner를 뺀 레포 이름
    commit: str  # 전체 커밋 해시
    path: str  # 레포 루트 기준, `/` 구분


def select_files(config: Config, root: Path, source: Source) -> list[SourceFile]:
    directory = source_dir(config, root, source)
    problem = checkout_problem(directory, source)
    if problem:
        raise FetchError(problem)
    commit = git(directory, "rev-parse", "HEAD").strip()
    return [
        SourceFile(source.name, source.repo_name, commit, path)
        for path in sorted(_regular_files(directory))
        if source.includes(path)
    ]


def count_by_rule(files: list[SourceFile], source: Source) -> dict[str, int]:
    """포함 규칙마다 몇 개가 뽑혔는지. 규칙 하나가 통째로 0개면 설정이 틀렸을 가능성이 크다."""
    counts = dict.fromkeys(source.include, 0)
    for f in files:
        rule = next(r for r in source.include if matches_rule(f.path, r))
        counts[rule] += 1
    return counts


def read_lines(path: Path) -> list[str]:
    """줄 번호가 git·에디터와 같도록 `\\n`으로만 나눈다.

    str.splitlines()는 U+2028·폼피드 같은 문자에서도 줄을 나눠 줄 번호가 어긋난다.
    """
    lines = path.read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def _regular_files(directory: Path) -> list[str]:
    out = git(directory, "ls-tree", "-r", "-z", "HEAD")
    paths = []
    for entry in filter(None, out.split("\0")):
        meta, path = entry.split("\t", 1)
        if meta.split(" ", 1)[0] in REGULAR_FILE_MODES:
            paths.append(path)
    return paths
