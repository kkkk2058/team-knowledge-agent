"""테스트에서 같이 쓰는 골든셋·소스 저장소 만들기."""

import subprocess
from pathlib import Path

import yaml

from tka.config import Config, Source


def make_item(**overrides) -> dict:
    item = {
        "id": "g01",
        "type": "명세 값",
        "version": "v0",
        "question": "질문?",
        "expected": "답",
        "evidence": [{"repo": "wiki", "path": "docs/a.md", "lines": "2"}],
        "status": "확인 대기",
    }
    item.update(overrides)
    return item


def make_golden(items: list[dict], commit: str = "abcdef1") -> dict:
    return {"meta": {"source_commits": {"wiki": commit}}, "items": items}


def write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_wiki_source(root: Path) -> tuple[Config, str]:
    """root/.cache/sources/wiki 에 작은 git 저장소를 만들고, 그걸 가리키는 설정을 돌려준다."""
    repo = root / ".cache" / "sources" / "wiki"
    (repo / "docs" / "sub").mkdir(parents=True)
    (repo / "backup").mkdir()
    (repo / ".agents").mkdir()
    (repo / "docs" / "a.md").write_text("# 제목\n\n임베딩은 e5-small이다\n", encoding="utf-8")
    (repo / "docs" / "b.md").write_text("결제는 테스트로 운영한다\n", encoding="utf-8")
    (repo / "docs" / "sub" / "spec.md").write_text(
        "\n".join(f"줄 {n}" for n in range(1, 21)) + "\n"
    )
    (repo / "backup" / "old.md").write_text("옛 설계\n", encoding="utf-8")
    (repo / ".agents" / "d.md").write_text("결정\n", encoding="utf-8")
    (repo / "docs" / "untracked.md").write_text("x\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "docs/a.md", "docs/b.md", "docs/sub/spec.md", "backup/old.md", ".agents/d.md")
    git(repo, "commit", "-q", "-m", "init")
    commit = git(repo, "rev-parse", "--short=7", "HEAD")
    config = Config(
        team="t",
        decision_log_year=2026,
        cache_dir=Path(".cache/sources"),
        index_path=Path("data/index.sqlite"),
        sources=(
            Source(
                name="wiki",
                repo="org/wiki",
                ref=commit,
                include=("docs/",),
                exclude=(),
                extensions=(".md",),
            ),
        ),
    )
    return config, commit
