"""테스트에서 같이 쓰는 골든셋·소스 저장소·가짜 임베딩 만들기."""

import hashlib
import subprocess
from pathlib import Path

import numpy as np
import yaml

from tka.config import Config, Source
from tka.index.keyword import tokenize_kiwi
from tka.index.vector import Embedder


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


# ── API 대조표용 작은 저장소 세 개: 명세·서버(FastAPI)·호출(fetch) ─────

CONTRACT_FILES = {
    "c-wiki": {"docs/api.md": "| ① | `POST /search` | 검색한다 |\n| ④ | `GET /feed` | 피드 |\n"},
    "srv": {"app/main.py": 'app = FastAPI()\n@app.post("/search")\n'},
    "web": {
        "src/api.ts": 'fetchWithAuth("/search", { method: "POST" });\nfetchWithAuth("/gone");\n'
    },
}


def make_contract_repos(base: Path) -> dict[str, str]:
    """base/<이름>에 저장소를 만들고 이름 → 커밋 해시를 돌려준다."""
    commits = {}
    for name, files in CONTRACT_FILES.items():
        repo = base / name
        for path, text in files.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text, encoding="utf-8")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "init")
        commits[name] = git(repo, "rev-parse", "HEAD")
    return commits


def contracts_config(commits: dict[str, str]):
    """명세 AI 하나, 서버 srv(FastAPI), 호출 web→AI(fetch)."""
    from tka.config import CallerContract, ContractsConfig, ServiceContract

    def source(name: str, include: str, ext: str) -> Source:
        return Source(name, f"org/{name}", commits[name][:7], (include,), (), (ext,))

    return ContractsConfig(
        sources=(
            source("c-wiki", "docs/", ".md"),
            source("srv", "app/", ".py"),
            source("web", "src/", ".ts"),
        ),
        services=(ServiceContract("AI", "c-wiki", "docs/api.md", "srv", "fastapi"),),
        callers=(CallerContract("FE→AI", "AI", "web", "fetch", (), "/", ("fetchWithAuth",)),),
    )


# ── 가짜 임베딩 모델 (e5를 불러오지 않는다) ───────────────────────────


class FakeModel:
    """낱말마다 정해진 방향을 더한 벡터. 같은 낱말이 많을수록 가깝다."""

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts, batch_size, normalize_embeddings, convert_to_numpy):
        self.calls += 1
        out = []
        for t in texts:
            v = np.zeros(64, dtype=np.float32)
            for word in tokenize_kiwi(t.split(": ", 1)[1]):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1
            out.append(v / (np.linalg.norm(v) or 1))
        return np.array(out)


def fake_embedder(cache=None):
    embedder = Embedder(cache)
    model = FakeModel()
    embedder.__dict__["model"] = model  # cached_property 자리에 넣어 진짜 모델을 불러오지 않는다
    return embedder, model
