"""소스 레포를 캐시 폴더(<cache_dir>/<소스 이름>)에 받아 기준 커밋으로 맞춘다.

작업 중인 로컬 폴더를 직접 읽지 않는다. 로컬 wiki 폴더는 다른 브랜치에 있을 수 있다
(implementation.md ①). 재시도는 하지 않는다. 실패하면 원인을 알리고 멈춘다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from tka.config import Config, Source


class FetchError(RuntimeError):
    """가져오기를 멈춰야 한다. 메시지에 원인과 다음 할 일을 적는다."""


@dataclass(frozen=True)
class Fetched:
    source: str
    directory: Path
    commit: str  # 전체 커밋 해시
    action: str  # clone · fetch · 있음


def github_url(source: Source) -> str:
    return f"https://github.com/{source.repo}.git"


def source_dir(config: Config, root: Path, source: Source) -> Path:
    return root / config.cache_dir / source.name


def fetch_source(config: Config, root: Path, source: Source, *, url: str | None = None) -> Fetched:
    """캐시가 없으면 clone, 기준 커밋이 없으면 fetch, 그다음 기준 커밋으로 checkout한다."""
    directory = source_dir(config, root, source)
    url = url or github_url(source)
    action = "있음"

    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        _run(None, "clone", "--quiet", url, str(directory), what=f"{source.repo} clone")
        action = "clone"
    else:
        _check_existing(directory, url)

    if not _has_commit(directory, source.ref):
        if action == "있음":
            _run(directory, "fetch", "--quiet", "origin", what=f"{source.repo} fetch")
            action = "fetch"
        if not _has_commit(directory, source.ref):
            raise FetchError(f"{source.repo}에 커밋 {source.ref}가 없다. 설정의 ref를 확인한다")

    _run(
        directory,
        "-c",
        "advice.detachedHead=false",
        "checkout",
        "--quiet",
        "--detach",
        source.ref,
        what=f"{source.repo} checkout {source.ref}",
    )
    commit = git(directory, "rev-parse", "HEAD").strip()
    return Fetched(source.name, directory, commit, action)


def checkout_problem(directory: Path, source: Source) -> str | None:
    """받아 둔 소스를 기준 커밋 그대로 읽을 수 있는가. 문제가 있으면 다음 할 일을 담은 설명."""
    hint = "uv run python -m tka.ingest fetch"
    if not directory.is_dir():
        return f"{source.repo_name}: 소스가 없다 ({directory}). 받는 법: {hint}"
    if not (directory / ".git").exists():
        return f"{source.repo_name}: git 저장소가 아니다 ({directory}). 폴더를 지우고 {hint}"
    head = git(directory, "rev-parse", "HEAD").strip()
    if not head.startswith(source.ref):
        return (
            f"{source.repo_name}: 받아 둔 소스가 기준 커밋이 아니다 "
            f"(HEAD {head[:7]}, 기준 {source.ref}). 맞추는 법: {hint}"
        )
    return None


def tracked_files(directory: Path) -> set[str]:
    """HEAD 커밋에 있는 경로 전부 (심볼릭 링크 포함)."""
    out = git(directory, "ls-tree", "-r", "-z", "--name-only", "HEAD")
    return set(filter(None, out.split("\0")))


def git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout


def _check_existing(directory: Path, url: str) -> None:
    if not (directory / ".git").exists():
        raise FetchError(f"{directory}: git 저장소가 아니다. 폴더를 지우고 다시 받는다")
    origin = git(directory, "remote", "get-url", "origin").strip()
    if not _same_repo(origin, url):
        raise FetchError(
            f"{directory}: origin이 {origin}이다 (기대: {url}). 폴더를 지우고 다시 받는다"
        )
    if git(directory, "status", "--porcelain", "--untracked-files=no").strip():
        raise FetchError(
            f"{directory}: 로컬 수정이 있어 덮어쓰지 않는다. 캐시라면 폴더를 지우고 다시 받는다"
        )


def _same_repo(a: str, b: str) -> bool:
    def norm(u: str) -> str:
        return u.rstrip("/").removesuffix(".git").lower()

    return norm(a) == norm(b)


def _has_commit(directory: Path, ref: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(directory), "cat-file", "-e", f"{ref}^{{commit}}"],
        capture_output=True,
    )
    return completed.returncode == 0


def _run(directory: Path | None, *args: str, what: str) -> None:
    command = ["git", *(["-C", str(directory)] if directory else []), *args]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise FetchError(f"{what} 실패: {completed.stderr.strip() or completed.stdout.strip()}")
