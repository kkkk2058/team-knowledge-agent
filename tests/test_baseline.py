import json
import sys
from pathlib import Path

import pytest
import yaml
from helpers import git, write_yaml

from tka.baseline import BaselineError, run_claude_code
from tka.golden import load_golden

FAKE_CLAUDE = f"""#!{sys.executable}
import json, os, sys
if sys.argv[1:] == ["--version"]:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
prompt = sys.argv[sys.argv.index("-p") + 1]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if mode == "auth":
    error = "Failed to authenticate: OAuth session expired"
    print(json.dumps({{"is_error": True, "result": error}}))
    sys.exit(1)
if mode == "notjson":
    print("hello")
    sys.exit(0)
if mode == "fail_second" and "두번째" in prompt:
    print(json.dumps({{"is_error": True, "result": "overloaded"}}))
    sys.exit(1)
print(json.dumps({{
    "is_error": False, "result": "답입니다\\ndocs/a.md:3", "duration_ms": 5,
    "total_cost_usd": 0.01, "num_turns": 2, "modelUsage": {{"claude-test": {{}}}},
}}))
"""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """프로젝트 밖의 clone, 가짜 claude, 골든셋 2문항."""
    repo = tmp_path / "outside" / "KTB4-13th-wiki"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "a.md").write_text("a\nb\nc\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    commit = git(repo, "rev-parse", "--short=7", "HEAD")

    fake = tmp_path / "fake_claude"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    evidence = [{"repo": "KTB4-13th-wiki", "path": "docs/a.md", "lines": "3"}]
    golden = load_golden(
        write_yaml(
            tmp_path / "golden.yaml",
            {
                "meta": {"source_commits": {"KTB4-13th-wiki": commit}},
                "items": [
                    {
                        "id": i,
                        "type": "명세 값",
                        "version": "v0",
                        "question": q,
                        "expected": "x",
                        "evidence": evidence,
                        "status": "확정",
                    }
                    for i, q in (("g01", "첫번째?"), ("g02", "두번째?"))
                ],
            },
        )
    )
    project = tmp_path / "project"
    project.mkdir()
    return golden, repo, project, str(fake), log


def _run(setup, **kwargs):
    golden, repo, project, fake, _ = setup
    out = project / "eval" / "results" / "run"
    kwargs.setdefault("repo_dir", repo)
    run_claude_code(
        golden,
        "KTB4-13th-wiki",
        kwargs.pop("repo_dir"),
        out,
        project_root=project,
        claude_bin=fake,
        **kwargs,
    )
    return yaml.safe_load((out / "answers.yaml").read_text(encoding="utf-8"))


def _calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_asks_every_item_in_a_fresh_restricted_session(setup):
    _, repo, _, _, log = setup

    data = _run(setup)

    assert [a["id"] for a in data["answers"]] == ["g01", "g02"]
    assert data["answers"][0]["answer"] == "답입니다\ndocs/a.md:3"
    assert data["answers"][0]["meta"] == {"duration_ms": 5, "cost_usd": 0.01, "num_turns": 2}
    assert data["detail"]["models"] == ["claude-test"]
    assert data["detail"]["tool"] == "Claude Code 9.9.9"
    calls = _calls(log)
    assert len(calls) == 2
    argv = calls[0]["argv"]
    assert Path(calls[0]["cwd"]).resolve() == repo.resolve()
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert "Bash" in argv[argv.index("--disallowedTools") + 1]
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert argv[argv.index("-p") + 1].startswith("첫번째?\n\n이 레포의 문서를 근거로")


def test_rerun_skips_answered_items(setup):
    *_, log = setup
    _run(setup)
    _run(setup)

    assert len(_calls(log)) == 2


def test_failed_item_is_recorded_and_retried_later(setup, monkeypatch):
    *_, log = setup
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail_second")
    data = _run(setup)

    assert data["answers"][1] == {"id": "g02", "answer": "", "error": "overloaded"}

    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    data = _run(setup)

    assert data["answers"][1]["answer"] == "답입니다\ndocs/a.md:3"
    assert len(_calls(log)) == 3  # g01 한 번, g02 두 번


def test_login_failure_stops_the_run(setup, monkeypatch):
    *_, log = setup
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "auth")

    with pytest.raises(BaselineError, match="로그인이 필요하다"):
        _run(setup)
    assert len(_calls(log)) == 1


def test_non_json_output_is_an_item_error(setup, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "notjson")

    data = _run(setup)

    assert data["answers"][0]["error"] == "출력이 JSON이 아니다: 'hello'"


def test_model_flag_is_passed(setup):
    *_, log = setup
    _run(setup, model="sonnet", ids=["g01"])

    argv = _calls(log)[0]["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_clone_inside_project_is_refused(setup):
    golden, repo, project, fake, _ = setup
    inside = project / "KTB4-13th-wiki"
    repo.rename(inside)

    with pytest.raises(BaselineError, match="이 프로젝트 안에 있다"):
        _run(setup, repo_dir=inside)


def test_clone_at_other_commit_is_refused(setup):
    _, repo, *_ = setup
    (repo / "docs" / "a.md").write_text("바뀜\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "next")

    with pytest.raises(BaselineError, match="기준 커밋이 아니다"):
        _run(setup)
