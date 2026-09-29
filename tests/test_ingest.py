from pathlib import Path

import pytest
from helpers import git

from tka.config import Config, Source
from tka.ingest.__main__ import main
from tka.ingest.fetch import FetchError, fetch_source, source_dir
from tka.ingest.files import count_by_rule, select_files


@pytest.fixture
def origin(tmp_path) -> tuple[Path, str, str]:
    """원격 역할을 하는 로컬 저장소. (경로, 첫 커밋, 둘째 커밋)"""
    repo = tmp_path / "origin"
    (repo / "docs" / "ai").mkdir(parents=True)
    (repo / "backup").mkdir()
    (repo / "docs" / "ai" / "spec.md").write_text("명세\n", encoding="utf-8")
    (repo / "docs" / "README.md").write_text("목차\n", encoding="utf-8")
    (repo / "docs" / "note.tsv").write_text("a\tb\n", encoding="utf-8")
    (repo / "backup" / "old.md").write_text("옛\n", encoding="utf-8")
    (repo / "docs" / "ai" / "link.md").symlink_to("spec.md")  # 같은 파일을 가리키는 링크
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "first")
    first = git(repo, "rev-parse", "HEAD")
    (repo / "docs" / "ai" / "design.md").write_text("설계\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "second")
    second = git(repo, "rev-parse", "HEAD")
    return repo, first, second


def _config(ref: str, include=("docs/README.md", "docs/ai/", "docs/cld/")) -> Config:
    source = Source(
        name="wiki",
        repo="org/wiki",
        ref=ref,
        include=tuple(include),
        exclude=(),
        extensions=(".md",),
    )
    return Config("t", 2026, Path(".cache/sources"), Path("data/index.sqlite"), (source,))


def _fetch(tmp_path, origin_repo, ref):
    config = _config(ref[:7])
    return config, fetch_source(config, tmp_path, config.sources[0], url=str(origin_repo))


def test_clone_then_checkout_pinned_commit(tmp_path, origin):
    repo, first, _ = origin

    config, fetched = _fetch(tmp_path, repo, first)

    assert fetched.action == "clone"
    assert fetched.commit == first
    assert fetched.directory == tmp_path / ".cache" / "sources" / "wiki"


def test_existing_cache_is_reused_and_moved_to_new_ref(tmp_path, origin):
    repo, first, second = origin
    _fetch(tmp_path, repo, second)

    _, again = _fetch(tmp_path, repo, first)

    assert (again.action, again.commit) == ("있음", first)


def test_missing_commit_is_fetched(tmp_path, origin):
    repo, first, _ = origin
    _fetch(tmp_path, repo, first)
    (repo / "docs" / "ai" / "later.md").write_text("나중\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "third")
    third = git(repo, "rev-parse", "HEAD")

    _, fetched = _fetch(tmp_path, repo, third)

    assert (fetched.action, fetched.commit) == ("fetch", third)


def test_unknown_commit_stops(tmp_path, origin):
    repo, *_ = origin

    with pytest.raises(FetchError, match="커밋 abcdef1가 없다"):
        _fetch(tmp_path, repo, "abcdef1")


def test_local_changes_are_not_overwritten(tmp_path, origin):
    repo, first, second = origin
    config, fetched = _fetch(tmp_path, repo, first)
    (fetched.directory / "docs" / "ai" / "spec.md").write_text("손댐\n", encoding="utf-8")

    with pytest.raises(FetchError, match="로컬 수정이 있어 덮어쓰지 않는다"):
        _fetch(tmp_path, repo, second)
    assert (fetched.directory / "docs" / "ai" / "spec.md").read_text(encoding="utf-8") == "손댐\n"


def test_cache_from_another_repo_stops(tmp_path, origin):
    repo, first, _ = origin
    _fetch(tmp_path, repo, first)
    config = _config(first[:7])

    with pytest.raises(FetchError, match="origin이"):
        fetch_source(config, tmp_path, config.sources[0], url="https://github.com/other/repo.git")


def test_clone_failure_is_reported(tmp_path):
    config = _config("abcdef1")

    with pytest.raises(FetchError, match="org/wiki clone 실패"):
        fetch_source(config, tmp_path, config.sources[0], url=str(tmp_path / "없음"))


def test_select_applies_rules_and_skips_symlinks(tmp_path, origin):
    repo, _, second = origin
    config, _ = _fetch(tmp_path, repo, second)
    source = config.sources[0]

    files = select_files(config, tmp_path, source)

    assert [f.path for f in files] == ["docs/README.md", "docs/ai/design.md", "docs/ai/spec.md"]
    assert {f.commit for f in files} == {second}
    assert count_by_rule(files, source) == {"docs/README.md": 1, "docs/ai/": 2, "docs/cld/": 0}


def test_select_refuses_unpinned_checkout(tmp_path, origin):
    repo, first, second = origin
    config, fetched = _fetch(tmp_path, repo, first)
    git(fetched.directory, "checkout", "-q", "--detach", second)

    with pytest.raises(FetchError, match="기준 커밋이 아니다"):
        select_files(config, tmp_path, config.sources[0])


def test_select_without_fetch_tells_how(tmp_path):
    config = _config("abcdef1")

    with pytest.raises(FetchError, match="python -m tka.ingest fetch"):
        select_files(config, tmp_path, config.sources[0])


def test_cli_files_prints_counts_and_warns_empty_rule(tmp_path, origin, capsys):
    repo, _, second = origin
    config, _ = _fetch(tmp_path, repo, second)
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"""team: t
decision_log_year: 2026
paths: {{cache_dir: .cache/sources, index_path: data/index.sqlite}}
sources:
  - name: wiki
    repo: org/wiki
    ref: "{second[:7]}"
    include: [docs/README.md, docs/ai/, docs/cld/]
""",
        encoding="utf-8",
    )

    code = main(["files", "--config", str(config_file), "--root", str(tmp_path), "--list"])
    out = capsys.readouterr().out

    assert code == 0
    assert f"wiki @ {second[:7]}: 3개" in out
    assert "docs/cld/" in out and "0개. 규칙이 맞는지 확인한다" in out
    assert "    docs/ai/spec.md" in out
    assert source_dir(config, tmp_path, config.sources[0]).is_dir()
