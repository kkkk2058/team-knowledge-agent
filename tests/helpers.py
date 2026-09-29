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


DECISION_LOG = """---
wiki: DEC-000 결정 로그
---
**요약** 입구.

| 날짜 | 파트 | 결정 | 영향 파트 | 상세 |
|---|---|---|---|---|
| 09-28 | AI | ④ 피드는 GET으로 부른다 | AI, BE | [모델 API 명세](../ai/spec.md#feed) |
| 09-21 | AI | ③⑤ LangChain 도입 (V1 미도입 번복) | AI | [설계](../ai/design.md) |
| 09-17 | FS·AI | 공통 응답 `{message, data}` | FS, AI | 형식 변경 시 이 로그에 추가 |
| 09-10 | CLD | 배포는 Recreate | - | [팀](../../.agents/team.md) |
"""

SUPERSEDES = """corrections:
  - date: "09-21"
    contains: LangChain
    replaces: V1은 LangChain을 쓰지 않는다
    old_text_at: [docs/ai/design.md:2]
"""


def make_decision_repo(path: Path, log: str = DECISION_LOG) -> str:
    """결정 로그가 든 작은 wiki 저장소를 path에 만들고 커밋 해시를 돌려준다."""
    (path / "docs" / "dec").mkdir(parents=True)
    (path / "docs" / "ai").mkdir(parents=True)
    (path / ".agents").mkdir()
    (path / "docs" / "dec" / "log.md").write_text(log, encoding="utf-8")
    (path / "docs" / "ai" / "spec.md").write_text("# 명세\n④ GET\n", encoding="utf-8")
    (path / "docs" / "ai" / "design.md").write_text("# 설계\n옛: 미도입\n새: 도입\n")
    (path / ".agents" / "team.md").write_text("팀 결정\n", encoding="utf-8")
    git(path, "init", "-q", "-b", "main")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")
    return git(path, "rev-parse", "HEAD")


def decision_config(root: Path, commit: str, *, corrections: str | None = SUPERSEDES) -> Config:
    from tka.config import DecisionsConfig

    corrections_path = None
    if corrections is not None:
        corrections_path = Path("config/supersedes.yaml")
        (root / "config").mkdir(exist_ok=True)
        (root / corrections_path).write_text(corrections, encoding="utf-8")
    source = Source(
        name="wiki",
        repo="org/wiki",
        ref=commit[:7],
        include=("docs/",),
        exclude=(),
        extensions=(".md",),
    )
    return Config(
        team="t",
        decision_log_year=2026,
        cache_dir=Path(".cache/sources"),
        index_path=Path("data/index.sqlite"),
        sources=(source,),
        decisions=DecisionsConfig("wiki", "docs/dec/log.md", corrections_path),
        aliases=(("④", "feed", "피드"),),
    )
